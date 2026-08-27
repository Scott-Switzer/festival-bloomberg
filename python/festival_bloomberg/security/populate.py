"""OPEN_ARTIST_MARKET_DATA_V1 — populate the Artist Security Master.

Orchestrates a full population pass:

    select_security_universe (ARTIST_SECURITY_1000)
        → ListenBrainz bulk totals + range history   (key-free)
        → Wikimedia historical daily backfill        (key-free)
        → YouTube channel snapshots                  (needs YOUTUBE_API_KEY)
        → Spotify catalog identity                   (needs SPOTIFY creds)
        → run_security_master (factors/live/catalog/peers/snapshots)
        → coverage report

Every collector is OPT-IN via its API key: missing credentials fail closed
(NOT_CONFIGURED) and are never treated as application failure. Wikimedia and
ListenBrainz are key-free and always attempted (bounded).

All data lands in ``metrics.artist_attention_observations`` and the
``metrics.artist_*`` / ``core.artist_*`` factor tables. Re-running is
idempotent (stable observation keys + INSERT-OR-IGNORE).

The ``coverage`` report is honest: it reports what was actually collected, not
targets.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..attention.listenbrainz_bulk import collect_security_universe_listenbrainz
from ..attention.spotify_catalog import collect_artist_catalog
from ..attention.wikimedia_historical import collect_artist_daily_pageviews
from ..attention.youtube_forward import collect_channel_snapshots
from .artist_security_master import (
    run_security_master,
    select_security_universe,
)

COVERAGE_TARGETS = {
    "universe_size": 1000,
    "musicbrainz_backed_pct": 95.0,
    "listenbrainz_usable_pct": 80.0,
    "wikimedia_usable_pct": 80.0,
    "historical_factor_observations": 1_000_000,
}


def load_identity_map(conn) -> dict[str, dict[str, Any]]:
    """artist_key → {channel_id, spotify_id} from core.entity_external_ids."""
    out: dict[str, dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT entity_key, id_type, id_value
        FROM core.entity_external_ids
        WHERE entity_type = 'artist'
          AND id_type IN ('youtube', 'spotify')
        """
    ).fetchall()
    for entity_key, id_type, id_value in rows:
        entry = out.setdefault(entity_key, {})
        if id_type == "youtube" and not entry.get("channel_id"):
            entry["channel_id"] = id_value
        if id_type == "spotify" and not entry.get("spotify_id"):
            entry["spotify_id"] = id_value
    return out


def resolve_identity(
    universe: list[dict[str, Any]],
    identity_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    """Attach channel_id/spotify_id to universe rows; return lookup maps."""
    artists_for_yt: list[dict[str, Any]] = []
    artists_for_spotify: list[dict[str, Any]] = []
    channel_by_key: dict[str, str] = {}
    spotify_by_key: dict[str, str] = {}
    for artist in universe:
        key = artist["artist_key"]
        ident = identity_map.get(key, {})
        artist_with_ids = {
            **artist,
            "channel_id": ident.get("channel_id"),
            "spotify_id": ident.get("spotify_id"),
        }
        artists_for_yt.append(artist_with_ids)
        artists_for_spotify.append(artist_with_ids)
        if ident.get("channel_id"):
            channel_by_key[key] = ident["channel_id"]
        if ident.get("spotify_id"):
            spotify_by_key[key] = ident["spotify_id"]
    return artists_for_yt, channel_by_key, spotify_by_key


def run_population(
    conn,
    transport,
    *,
    universe_limit: int = 1000,
    as_of: date | None = None,
    wiki_start: str | None = None,
    wiki_lookback_days: int | None = None,
    wiki_names: list[str] | None = None,
    youtube_api_key: str | None = None,
    spotify_client_id: str | None = None,
    spotify_client_secret: str | None = None,
    include_spotify: bool = True,
    include_youtube: bool = True,
    include_wikimedia: bool = True,
    include_listenbrainz: bool = True,
    min_interval_seconds: float = 0.2,
    include_lb_range_history: bool = True,
) -> dict[str, Any]:
    """Run the full population pass; returns a coverage report.

    ``wiki_names`` optionally overrides the wiki target list (used by tests /
    smoke runs); default = universe artist names.
    """
    universe = select_security_universe(conn, limit=universe_limit)
    identity_map = load_identity_map(conn)
    _yt_artists, channel_by_key, spotify_by_key = resolve_identity(universe, identity_map)

    report: dict[str, Any] = {
        "status": "RUNNING",
        "universe_size": len(universe),
        "targets": COVERAGE_TARGETS,
        "collectors": {},
        "materialization": None,
        "coverage": None,
    }

    if include_listenbrainz:
        lb = collect_security_universe_listenbrainz(
            conn, transport, universe=universe,
            min_interval_seconds=min_interval_seconds,
            include_range_history=include_lb_range_history,
        )
        report["collectors"]["listenbrainz"] = lb

    if include_wikimedia:
        from ..attention.wikimedia_historical import (
            artist_keys_by_name_for,
            collect_artist_daily_pageviews_bounded,
        )

        names = wiki_names or [a.get("artist_name") or a["artist_key"] for a in universe]
        key_map = {a.get("artist_name") or a["artist_key"]: a["artist_key"] for a in universe}
        if wiki_names:
            key_map = artist_keys_by_name_for(conn, names)
        if wiki_lookback_days:
            wiki = collect_artist_daily_pageviews_bounded(
                conn, transport, names=names,
                lookback_days=wiki_lookback_days,
                min_interval_seconds=min_interval_seconds,
                artist_keys_by_name=key_map,
            )
        else:
            wiki = collect_artist_daily_pageviews(
                conn, transport, names=names, start=wiki_start,
                min_interval_seconds=min_interval_seconds,
                artist_keys_by_name=key_map,
            )
        report["collectors"]["wikimedia"] = wiki

    if include_youtube:
        yt = collect_channel_snapshots(
            conn, transport,
            artists=[{**a, "channel_id": channel_by_key.get(a["artist_key"])} for a in universe],
            api_key=youtube_api_key,
        )
        report["collectors"]["youtube"] = yt

    if include_spotify:
        sp = collect_artist_catalog(
            conn, transport,
            artists=[{**a, "spotify_id": spotify_by_key.get(a["artist_key"])} for a in universe],
            spotify_id_by_key=spotify_by_key,
            client_id=spotify_client_id,
            client_secret=spotify_client_secret,
        )
        report["collectors"]["spotify"] = sp

    mat = run_security_master(conn, universe_limit=universe_limit, as_of=as_of)
    report["materialization"] = mat
    report["coverage"] = compute_coverage(conn, universe_limit=universe_limit)
    report["status"] = "COMPLETE"
    return report


def compute_coverage(conn, *, universe_limit: int = 1000) -> dict[str, Any]:
    """Honest coverage numbers from what is actually in the warehouse."""
    universe = select_security_universe(conn, limit=universe_limit)
    keys = [a["artist_key"] for a in universe]
    if not keys:
        return {"status": "EMPTY_UNIVERSE"}

    placeholders = ", ".join("?" for _ in keys)

    def count_by(source: str, metric_like: str) -> int:
        return conn.execute(
            f"""
            SELECT COUNT(DISTINCT artist_key)
            FROM metrics.artist_attention_observations
            WHERE artist_key IN ({placeholders})
              AND source_system = ?
              AND metric_kind LIKE ?
              AND status = 'ok'
            """,
            [*keys, source, metric_like],
        ).fetchone()[0]

    lb_artists = count_by("listenbrainz", "LISTENBRAINZ%")
    wiki_artists = count_by("wikimedia", "pageviews")
    yt_artists = count_by("youtube", "YT_%")
    sp_artists = count_by("spotify", "SPOTIFY%")

    wiki_daily_rows = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM metrics.artist_attention_observations
        WHERE artist_key IN ({placeholders})
          AND source_system = 'wikimedia'
          AND status = 'ok' AND metric_kind = 'pageviews'
          AND period_start IS NOT NULL AND period_end = period_start
        """,
        keys,
    ).fetchone()[0]

    factor_rows = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM metrics.artist_factor_observations
        WHERE artist_key IN ({placeholders})
        """,
        keys,
    ).fetchone()[0]

    mbid_backed = sum(1 for a in universe if a.get("mbid"))
    n = max(len(keys), 1)
    return {
        "status": "OK",
        "universe_size": len(keys),
        "artists_musicbrainz_backed": mbid_backed,
        "musicbrainz_backed_pct": round(mbid_backed / n * 100, 2),
        "listenbrainz_usable_artists": lb_artists,
        "listenbrainz_usable_pct": round(lb_artists / n * 100, 2),
        "wikimedia_usable_artists": wiki_artists,
        "wikimedia_usable_pct": round(wiki_artists / n * 100, 2),
        "youtube_usable_artists": yt_artists,
        "youtube_usable_pct": round(yt_artists / n * 100, 2),
        "spotify_usable_artists": sp_artists,
        "spotify_usable_pct": round(sp_artists / n * 100, 2),
        "wikimedia_daily_rows": wiki_daily_rows,
        "artist_factor_rows": factor_rows,
    }
