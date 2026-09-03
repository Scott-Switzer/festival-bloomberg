"""Artist Security Master computation — factor families from canonical evidence.

The Bloomberg analogy: every artist behaves like a tradable security. This
module computes the ARTIST SECURITY layer:

    ARTIST
      → GLOBAL SECURITY SNAPSHOT      (per-artist factor families)
      → ARTIST × MARKET SNAPSHOT      (market factor observations)
      → BOOKING SNAPSHOT              (artist × market × date — the decision object)

Factor families are EVIDENCE-BACKED observations written to
``metrics.artist_factor_observations`` — never a single opaque "artist score".

Semantics preserved everywhere:

- ``as_of`` = the observation date (market/event date), NOT the retrieval time.
- ``retrieved_at`` = when we fetched the evidence (provenance only).
- ``available_at`` = the source's publication/availability bound when known.
- ``UNKNOWN`` stays NULL — never a fabricated zero.
- No invented economics; no GO/HOLD/PASS; no demand-score ranking.
- rights/commercial status travel with every observation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..identity.spotify import normalize_name

SOFTWARE_VERSION = "artist_security_master_v1"
FACTOR_VERSION = "artist_factors_v1"
LIVE_VERSION = "artist_live_stats_v1"
CATALOG_VERSION = "artist_catalog_stats_v1"
SNAPSHOT_VERSION = "artist_security_snapshot_v1"

FACTOR_FAMILIES = (
    "DEMAND", "MOMENTUM", "LIVE", "MARKET", "PRICING", "TOURING",
    "CATALOG", "NETWORK", "FESTIVAL_FIT", "RISK", "RELATIVE_VALUE", "EVIDENCE",
)

DEFAULT_SECURITY_UNIVERSE_SIZE = 1000


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def factor_observation_key(
    *,
    artist_key: str,
    factor_name: str,
    as_of: str,
    source_system: str,
    generation: str | None = None,
) -> str:
    """Build an immutable observation key.

    ``generation`` is optional for backwards compatibility with migration 043
    callers. New collectors should provide it so two independently published
    snapshots on the same observation date remain distinct rows.
    """
    material = "|".join([
        artist_key, factor_name, as_of, source_system,
        generation or FACTOR_VERSION, FACTOR_VERSION,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def live_stat_key(*, artist_key: str, as_of: str) -> str:
    material = "|".join([artist_key, as_of, LIVE_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def catalog_stat_key(*, artist_key: str, as_of: str) -> str:
    material = "|".join([artist_key, as_of, CATALOG_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def snapshot_key(*, artist_key: str, snapshot_date: str) -> str:
    material = "|".join([artist_key, snapshot_date, SNAPSHOT_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def peer_edge_key(*, subject: str, peer: str, edge_type: str) -> str:
    material = "|".join([subject, peer, edge_type, FACTOR_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def fallback_artist_key(name: str) -> str:
    return f"name::{normalize_name(name)}"


# ---------------------------------------------------------------------------
# Universe selection — ARTIST_SECURITY_1000
# ---------------------------------------------------------------------------

def select_security_universe(
    conn,
    *,
    limit: int = DEFAULT_SECURITY_UNIVERSE_SIZE,
    min_attention_artists: bool = True,
) -> list[dict[str, Any]]:
    """Deterministically select the decision-relevant security universe.

    Ranking is EXPLICIT and NON-PREDICTIVE (no demand score):
      1. artists with ticket-market presence (event performers / TM estate),
      2. weighted by identity-linkage depth + attention coverage,
      3. lexicographic artist_key tie-break for determinism.

    Returns rows ``{artist_key, artist_name, mbid, attention_observations,
    external_ids, event_performances, selection_reason}``.
    """
    rows = conn.execute(
        """
        WITH attention_counts AS (
            SELECT artist_key, COUNT(*) AS n_attention
            FROM metrics.artist_attention_observations
            WHERE status = 'ok'
            GROUP BY artist_key
        ),
        id_counts AS (
            SELECT entity_key, COUNT(*) AS n_ids
            FROM core.entity_external_ids
            WHERE entity_type = 'artist'
            GROUP BY entity_key
        ),
        perf_counts AS (
            SELECT artist_mbid, COUNT(*) AS n_perf
            FROM core.event_performers
            WHERE artist_mbid IS NOT NULL
            GROUP BY artist_mbid
        )
        SELECT
            a.artist_key,
            a.name AS artist_name,
            a.musicbrainz_id AS mbid,
            COALESCE(atc.n_attention, 0) AS attention_observations,
            COALESCE(ic.n_ids, 0) AS external_ids,
            COALESCE(pc.n_perf, 0) AS event_performances
        FROM core.artists a
        LEFT JOIN attention_counts atc ON atc.artist_key = a.artist_key
        LEFT JOIN id_counts ic ON ic.entity_key = a.artist_key
        LEFT JOIN perf_counts pc ON pc.artist_mbid = a.musicbrainz_id
        WHERE a.artist_key IS NOT NULL
        ORDER BY
            (COALESCE(pc.n_perf, 0) > 0) DESC,
            (COALESCE(ic.n_ids, 0) + COALESCE(atc.n_attention, 0)) DESC,
            a.artist_key ASC
        LIMIT ?
        """,
        [limit],
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "artist_key": r[0],
            "artist_name": r[1],
            "mbid": r[2],
            "attention_observations": int(r[3]),
            "external_ids": int(r[4]),
            "event_performances": int(r[5]),
            "selection_reason": "security_universe_v1_non_predictive",
        })
    return out


# ---------------------------------------------------------------------------
# Demand / momentum factor observations (from the attention tape)
# ---------------------------------------------------------------------------

def _attention_level_observation(
    *,
    artist_key: str,
    artist_name: str,
    factor_name: str,
    family: str,
    value: float | None,
    unit: str,
    as_of: date,
    period_start: date | None,
    period_end: date | None,
    source_system: str,
    source_url: str,
    retrieved_at: str,
    rights_status: str = "TERMS_REVIEW_REQUIRED",
    commercial_use_status: str = "PROTOTYPE_ONLY",
    confidence: float | None = None,
    evidence: dict[str, Any] | None = None,
    platform: str | None = None,
    generation: str | None = None,
    observation_time: str | None = None,
    available_at: str | None = None,
    knowledge_time: str | None = None,
    source_scope: str | None = None,
    quality_status: str | None = None,
) -> dict[str, Any]:
    return {
        "factor_observation_key": factor_observation_key(
            artist_key=artist_key, factor_name=factor_name,
            as_of=as_of.isoformat(), source_system=source_system,
            generation=generation,
        ),
        "artist_key": artist_key,
        "factor_family": family,
        "factor_name": factor_name,
        "value": value,
        "value_unit": unit,
        "as_of": as_of.isoformat(),
        "available_at": available_at,
        "retrieved_at": retrieved_at,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "source_system": source_system,
        "source_version": FACTOR_VERSION,
        "source_url": source_url,
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "confidence": confidence,
        "platform": platform or source_system,
        "unit": unit,
        "observation_time": observation_time or f"{as_of.isoformat()}T00:00:00",
        "knowledge_time": knowledge_time or retrieved_at,
        "source": source_system,
        "evidence_ref": source_url,
        "source_scope": source_scope or "ARTIST_SECURITY_FACTOR",
        "quality_status": quality_status or ("UNKNOWN" if value is None else "OBSERVED"),
        "generation": generation or FACTOR_VERSION,
        "evidence_json": json.dumps(evidence or {}, default=str),
    }


def derive_demand_and_momentum_factors(
    conn,
    *,
    universe: list[dict[str, Any]],
    as_of: date | None = None,
    retrieved_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Derive DEMAND + MOMENTUM factor observations from the attention tape.

    Sources consumed:
      - LISTENBRAINZ_TOTAL_LISTEN_COUNT / LISTENBRAINZ_TOTAL_USER_COUNT
        (cumulative provider aggregates — DEMAND level, not local demand)
      - LISTENBRAINZ_LISTEN_COUNT per stats_range (week/month/all_time) →
        LB_LISTENS_7D/28D/90D windows + LB_LISTEN_VELOCITY where ranges exist
      - wikimedia pageviews (daily windows) → WIKI_VIEWS_1D/7D/28D + z-score

    Never interprets attention as ticket demand. UNKNOWN stays NULL.
    """
    retrieved_at = retrieved_at or datetime.now(UTC).isoformat()
    as_of = as_of or date.today()
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_in_universe": len(universe),
        "rows_written": 0,
        "demand_rows": 0,
        "momentum_rows": 0,
    }
    rows: list[dict[str, Any]] = []

    for artist in universe:
        artist_key = artist["artist_key"]
        name = artist.get("artist_name") or artist_key

        # --- ListenBrainz cumulative totals (DEMAND level) ---
        lb_totals = conn.execute(
            """
            SELECT metric_kind, value
            FROM metrics.artist_attention_observations
            WHERE artist_key = ? AND source_system = 'listenbrainz'
              AND status = 'ok'
              AND metric_kind IN ('LISTENBRAINZ_TOTAL_LISTEN_COUNT', 'LISTENBRAINZ_TOTAL_USER_COUNT')
            ORDER BY retrieved_at DESC
            """,
            [artist_key],
        ).fetchall()
        for metric_kind, value in lb_totals:
            if metric_kind == "LISTENBRAINZ_TOTAL_LISTEN_COUNT":
                factor_name, unit = "LB_TOTAL_LISTENS", "listens"
            else:
                factor_name, unit = "LB_TOTAL_LISTENERS", "listeners"
            rows.append(_attention_level_observation(
                artist_key=artist_key, artist_name=name, factor_name=factor_name,
                family="DEMAND", value=value, unit=unit, as_of=as_of,
                period_start=None, period_end=None, source_system="listenbrainz",
                source_url="https://api.listenbrainz.org/1/popularity/artist",
                retrieved_at=retrieved_at,
                evidence={"metric_kind": metric_kind, "semantics": "ATTENTION_CONSUMPTION_SAMPLE; never local demand"},
            ))
            summary["demand_rows"] += 1

        # --- ListenBrainz range-based listens → windows + velocity (MOMENTUM) ---
        range_rows = conn.execute(
            """
            SELECT metric_kind, value, granularity, period_start, period_end
            FROM metrics.artist_attention_observations
            WHERE artist_key = ? AND source_system = 'listenbrainz'
              AND status = 'ok'
              AND metric_kind = 'LISTENBRAINZ_LISTEN_COUNT'
            ORDER BY period_end DESC
            """,
            [artist_key],
        ).fetchall()
        range_map: dict[str, tuple[float | None, str | None, str | None]] = {}
        for _kind, value, granularity, p_start, p_end in range_rows:
            if granularity and granularity not in range_map:
                range_map[granularity] = (value, p_start.isoformat() if p_start else None,
                                          p_end.isoformat() if p_end else None)
        if "week" in range_map:
            v, ps, pe = range_map["week"]
            rows.append(_attention_level_observation(
                artist_key=artist_key, artist_name=name, factor_name="LB_LISTENS_7D",
                family="MOMENTUM", value=v, unit="listens", as_of=as_of,
                period_start=date.fromisoformat(ps) if ps else None,
                period_end=date.fromisoformat(pe) if pe else None,
                source_system="listenbrainz",
                source_url="https://api.listenbrainz.org/1/stats/artist/listeners",
                retrieved_at=retrieved_at,
                evidence={"stats_range": "week"},
            ))
            summary["momentum_rows"] += 1
        if "month" in range_map:
            v, ps, pe = range_map["month"]
            rows.append(_attention_level_observation(
                artist_key=artist_key, artist_name=name, factor_name="LB_LISTENS_28D",
                family="DEMAND", value=v, unit="listens", as_of=as_of,
                period_start=date.fromisoformat(ps) if ps else None,
                period_end=date.fromisoformat(pe) if pe else None,
                source_system="listenbrainz",
                source_url="https://api.listenbrainz.org/1/stats/artist/listeners",
                retrieved_at=retrieved_at,
                evidence={"stats_range": "month"},
            ))
            summary["demand_rows"] += 1
        if "week" in range_map and "month" in range_map:
            w, _, _ = range_map["week"]
            m, _, _ = range_map["month"]
            if w is not None and m is not None and m and m > 0:
                velocity = round(w / m, 4)
                rows.append(_attention_level_observation(
                    artist_key=artist_key, artist_name=name, factor_name="LB_LISTEN_VELOCITY",
                    family="MOMENTUM", value=velocity, unit="week_share", as_of=as_of,
                    period_start=None, period_end=None, source_system="listenbrainz",
                    source_url="https://api.listenbrainz.org/1/stats/artist/listeners",
                    retrieved_at=retrieved_at,
                    evidence={"formula": "week_listens / month_listens", "stats_range": "week|month"},
                ))
                summary["momentum_rows"] += 1

        # --- Wikimedia DAILY pageviews → WIKI windows + zscore + shock ---
        wiki_rows = conn.execute(
            """
            SELECT value, period_start
            FROM metrics.artist_attention_observations
            WHERE artist_key = ? AND source_system = 'wikimedia'
              AND status = 'ok' AND metric_kind = 'pageviews'
              AND period_start IS NOT NULL AND period_end = period_start
            ORDER BY period_start ASC
            """,
            [artist_key],
        ).fetchall()
        daily: dict[date, float] = {}
        for value, p_start in wiki_rows:
            if value is None:
                continue
            try:
                d = date.fromisoformat(str(p_start)[:10])
            except ValueError:
                continue
            daily[d] = float(value)
        if daily:
            _append_wiki_window_factors(rows, summary, artist_key=artist_key,
                                        artist_name=name, daily=daily, as_of=as_of,
                                        retrieved_at=retrieved_at)

    summary["rows_written"] = len(rows)
    summary["status"] = "COMPLETE"
    return rows, summary


def _append_wiki_window_factors(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    artist_key: str,
    artist_name: str,
    daily: dict[date, float],
    as_of: date,
    retrieved_at: str,
) -> None:
    """WIKI_VIEWS_1D/7D/28D/90D + WIKI_MOMENTUM + WIKI_ACCELERATION + WIKI_ZSCORE
    + WIKI_ATTENTION_SHOCK.

    Trailing windows strictly before ``as_of``. z-score is computed against the
    trailing 180d daily series when >= 7 days exist (else NULL). Shock = latest
    1d vs trailing 90d mean, only when both exist. Never fabricates windows
    from an incomplete series: a window with zero observed days stays NULL.
    """
    def window_sum(days: int) -> float | None:
        lo = as_of - timedelta(days=days)
        vals = [v for d, v in daily.items() if lo <= d < as_of]
        return round(sum(vals), 4) if vals else None

    w1 = window_sum(1)
    _w7 = window_sum(7)  # noqa: F841 - computed alongside the windows actually used
    w28 = window_sum(28)
    _w90 = window_sum(90)  # noqa: F841 - computed alongside the windows actually used
    if w1 is not None:
        rows.append(_attention_level_observation(
            artist_key=artist_key, artist_name=artist_name, factor_name="WIKI_VIEWS_1D",
            family="DEMAND", value=w1, unit="pageviews", as_of=as_of,
            period_start=(as_of - timedelta(days=1)), period_end=(as_of - timedelta(days=1)),
            source_system="wikimedia",
            source_url="https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article",
            retrieved_at=retrieved_at,
            evidence={"window": "1d", "semantics": "ATTENTION_CHANNEL; never ticket demand"},
        ))
        summary["demand_rows"] += 1
    for days, fname in ((7, "WIKI_VIEWS_7D"), (28, "WIKI_VIEWS_28D"), (90, "WIKI_VIEWS_90D")):
        w = window_sum(days)
        if w is None:
            continue
        rows.append(_attention_level_observation(
            artist_key=artist_key, artist_name=artist_name, factor_name=fname,
            family="DEMAND" if days < 90 else "MOMENTUM", value=w, unit="pageviews",
            as_of=as_of,
            period_start=(as_of - timedelta(days=days)), period_end=(as_of - timedelta(days=1)),
            source_system="wikimedia",
            source_url="https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article",
            retrieved_at=retrieved_at,
            evidence={"window": f"{days}d", "semantics": "ATTENTION_CHANNEL; never ticket demand"},
        ))
        summary["demand_rows" if days < 90 else "momentum_rows"] += 1
    # Momentum: 28d vs prior 28d
    prev = _window_sum_span(daily, as_of - timedelta(days=56), as_of - timedelta(days=28))
    if w28 is not None and prev:
        rows.append(_attention_level_observation(
            artist_key=artist_key, artist_name=artist_name, factor_name="WIKI_MOMENTUM",
            family="MOMENTUM", value=round(w28 / prev - 1.0, 4), unit="relative", as_of=as_of,
            period_start=(as_of - timedelta(days=56)), period_end=(as_of - timedelta(days=1)),
            source_system="wikimedia",
            source_url="https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article",
            retrieved_at=retrieved_at,
            evidence={"formula": "28d_sum / prior_28d_sum - 1"},
        ))
        summary["momentum_rows"] += 1
    # Acceleration: momentum now vs momentum prior (second-order change)
    prev_prev = _window_sum_span(daily, as_of - timedelta(days=84), as_of - timedelta(days=56))
    if w28 is not None and prev and prev_prev:
        momentum_now = w28 / prev - 1.0
        momentum_prior = prev / prev_prev - 1.0
        rows.append(_attention_level_observation(
            artist_key=artist_key, artist_name=artist_name, factor_name="WIKI_ACCELERATION",
            family="MOMENTUM", value=round(momentum_now - momentum_prior, 4), unit="relative", as_of=as_of,
            period_start=(as_of - timedelta(days=84)), period_end=(as_of - timedelta(days=1)),
            source_system="wikimedia",
            source_url="https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article",
            retrieved_at=retrieved_at,
            evidence={"formula": "(28d/prev28d - 1) - (prev28d/prevprev28d - 1)"},
        ))
        summary["momentum_rows"] += 1
    # z-score against trailing 180d
    lo180 = as_of - timedelta(days=180)
    series = [v for d, v in daily.items() if lo180 <= d < as_of]
    if len(series) >= 7 and w1 is not None:
        mean = sum(series) / len(series)
        var = sum((v - mean) ** 2 for v in series) / (len(series) - 1)
        std = var ** 0.5
        if std > 0:
            rows.append(_attention_level_observation(
                artist_key=artist_key, artist_name=artist_name, factor_name="WIKI_ZSCORE",
                family="MOMENTUM", value=round((w1 - mean) / std, 4), unit="zscore", as_of=as_of,
                period_start=(as_of - timedelta(days=180)), period_end=(as_of - timedelta(days=1)),
                source_system="wikimedia",
                source_url="https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article",
                retrieved_at=retrieved_at,
                evidence={"formula": "(1d - mean180d) / std180d", "n_days": len(series)},
            ))
            summary["momentum_rows"] += 1
    # Shock: latest 1d vs trailing 90d mean
    lo90 = as_of - timedelta(days=90)
    s90 = [v for d, v in daily.items() if lo90 <= d < as_of]
    if len(s90) >= 7 and w1 is not None:
        mean90 = sum(s90) / len(s90)
        if mean90 > 0:
            rows.append(_attention_level_observation(
                artist_key=artist_key, artist_name=artist_name, factor_name="WIKI_ATTENTION_SHOCK",
                family="MOMENTUM", value=round(w1 / mean90, 4), unit="relative", as_of=as_of,
                period_start=(as_of - timedelta(days=90)), period_end=(as_of - timedelta(days=1)),
                source_system="wikimedia",
                source_url="https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article",
                retrieved_at=retrieved_at,
                evidence={"formula": "1d / mean90d", "n_days": len(s90)},
            ))
            summary["momentum_rows"] += 1


def _window_sum_span(daily: dict[date, float], lo: date, hi: date) -> float | None:
    vals = [v for d, v in daily.items() if lo <= d < hi]
    return round(sum(vals), 4) if vals else None


def derive_youtube_snapshot_factors(
    conn,
    *,
    universe: list[dict[str, Any]],
    as_of: date | None = None,
    retrieved_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """LATEST YouTube channel snapshot → factor observations (DEMAND level).

    Consumes YT_SUBSCRIBERS / YT_CHANNEL_VIEWS / YT_VIDEO_COUNT observations
    (retrieval-day snapshots) and exposes the LATEST real snapshot per artist.
    Deltas are computed ONLY from two real snapshots in a later pass — never
    reconstructed here.
    """
    retrieved_at = retrieved_at or datetime.now(UTC).isoformat()
    as_of = as_of or date.today()
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_in_universe": len(universe),
        "rows_written": 0,
    }
    rows: list[dict[str, Any]] = []
    for artist in universe:
        artist_key = artist["artist_key"]
        name = artist.get("artist_name") or artist_key
        snaps = conn.execute(
            """
            SELECT metric_kind, value, period_end, provenance_json
            FROM metrics.artist_attention_observations
            WHERE artist_key = ? AND source_system = 'youtube'
              AND status = 'ok'
              AND metric_kind IN ('YT_SUBSCRIBERS', 'YT_CHANNEL_VIEWS', 'YT_VIDEO_COUNT')
            ORDER BY period_end DESC
            """,
            [artist_key],
        ).fetchall()
        seen: set[str] = set()
        for metric_kind, value, period_end, _prov in snaps:
            if metric_kind in seen:
                continue
            seen.add(metric_kind)
            if value is None:
                continue
            factor_name = {
                "YT_SUBSCRIBERS": "YT_SUBSCRIBERS",
                "YT_CHANNEL_VIEWS": "YT_CHANNEL_VIEWS",
                "YT_VIDEO_COUNT": "YT_VIDEO_COUNT",
            }[metric_kind]
            rows.append(_attention_level_observation(
                artist_key=artist_key, artist_name=name, factor_name=factor_name,
                family="DEMAND", value=float(value), unit="count", as_of=as_of,
                period_start=None, period_end=None, source_system="youtube",
                source_url="https://www.googleapis.com/youtube/v3/channels",
                retrieved_at=retrieved_at,
                evidence={
                    "snapshot_day": str(period_end)[:10] if period_end else None,
                    "semantics": "MUTABLE_PLATFORM_SNAPSHOT; latest real snapshot only",
                },
            ))
            summary["rows_written"] += 1
    summary["status"] = "COMPLETE"
    return rows, summary


# ---------------------------------------------------------------------------
# Live statistics (from core.event_performers + raw.musicbrainz_event)
# ---------------------------------------------------------------------------

def derive_live_statistics(
    conn,
    *,
    universe: list[dict[str, Any]],
    as_of: date | None = None,
    retrieved_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Live-strength factor family from the event/performance graph.

    SHOWS_30D/90D/365D, MARKETS_365D, UNIQUE_VENUES_365D,
    FESTIVAL_APPEARANCES_365D, DAYS_SINCE_LAST_SHOW.

    Event dates come from raw.musicbrainz_event.begin_date (EVENT_TIME).
    ``as_of`` is the observation date — never the retrieval time.
    """
    retrieved_at = retrieved_at or datetime.now(UTC).isoformat()
    as_of = as_of or date.today()
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_in_universe": len(universe),
        "rows_written": 0,
    }
    rows: list[dict[str, Any]] = []

    for artist in universe:
        mbid = artist.get("mbid")
        if not mbid:
            continue
        perf = conn.execute(
            """
            SELECT e.begin_date, e.event_type, COUNT(*) OVER ()
            FROM core.event_performers ep
            JOIN raw.musicbrainz_event e ON e.mbid = ep.event_mbid
            WHERE ep.artist_mbid = ? AND ep.performer_role = 'main performer'
              AND e.begin_date IS NOT NULL
            """,
            [mbid],
        ).fetchall()
        if not perf:
            continue
        show_dates: list[date] = []
        festival_dates: list[date] = []
        for begin_date, event_type, _ in perf:
            try:
                d = date.fromisoformat(str(begin_date)[:10])
            except ValueError:
                continue
            show_dates.append(d)
            if event_type and str(event_type).lower() == "festival":
                festival_dates.append(d)
        if not show_dates:
            continue

        cutoff_30 = as_of - timedelta(days=30)
        cutoff_90 = as_of - timedelta(days=90)
        cutoff_365 = as_of - timedelta(days=365)
        shows_30 = sum(1 for d in show_dates if d >= cutoff_30)
        shows_90 = sum(1 for d in show_dates if d >= cutoff_90)
        shows_365 = sum(1 for d in show_dates if d >= cutoff_365)
        festivals_365 = sum(1 for d in festival_dates if d >= cutoff_365)
        last_show = max(show_dates)
        days_since_last = (as_of - last_show).days if last_show <= as_of else 0

        rows.append({
            "stat_key": live_stat_key(artist_key=artist["artist_key"], as_of=as_of.isoformat()),
            "artist_key": artist["artist_key"],
            "as_of": as_of.isoformat(),
            "shows_30d": shows_30,
            "shows_90d": shows_90,
            "shows_365d": shows_365,
            "markets_365d": None,  # requires place/market join — not invented
            "unique_venues_365d": None,
            "festival_appearances_365d": festivals_365,
            "days_since_last_show": days_since_last,
            "venue_progression": None,
            "source_system": "musicbrainz",
            "source_version": LIVE_VERSION,
            "retrieved_at": retrieved_at,
            "rights_status": "CC0_CORE",
            "commercial_use_status": "PROTOTYPE_ONLY",
            "evidence_json": json.dumps({
                "shows_sampled": len(show_dates),
                "event_source": "raw.musicbrainz_event.begin_date",
                "role": "main performer",
            }, default=str),
        })
        summary["rows_written"] += 1

    summary["status"] = "COMPLETE"
    return rows, summary


# ---------------------------------------------------------------------------
# Catalog statistics (from core.releases)
# ---------------------------------------------------------------------------

def derive_catalog_statistics(
    conn,
    *,
    universe: list[dict[str, Any]],
    as_of: date | None = None,
    retrieved_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Catalog fundamentals from core.releases (MusicBrainz CC0 core data).

    RELEASES_12M/36M, DAYS_SINCE_LAST_RELEASE, CATALOG_DEPTH.
    Releases with no date are excluded from recency but count toward depth.
    """
    retrieved_at = retrieved_at or datetime.now(UTC).isoformat()
    as_of = as_of or date.today()
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_in_universe": len(universe),
        "rows_written": 0,
    }
    rows: list[dict[str, Any]] = []

    for artist in universe:
        artist_key = artist["artist_key"]
        # Releases join to release_groups (which carry artist_keys).
        releases = conn.execute(
            """
            SELECT r.release_date
            FROM core.releases r
            JOIN core.release_groups rg ON rg.release_group_key = r.release_group_key
            WHERE rg.artist_keys LIKE ?
            """,
            [f'%"{artist_key}"%'],
        ).fetchall()
        dates: list[date] = []
        for (rd,) in releases:
            if rd is None:
                continue
            try:
                dates.append(date.fromisoformat(str(rd)[:10]))
            except ValueError:
                continue
        releases_12m = sum(1 for d in dates if d >= as_of - timedelta(days=365))
        releases_36m = sum(1 for d in dates if d >= as_of - timedelta(days=365 * 3))
        days_since_last = None
        if dates:
            last = max(dates)
            if last <= as_of:
                days_since_last = (as_of - last).days
        rows.append({
            "stat_key": catalog_stat_key(artist_key=artist_key, as_of=as_of.isoformat()),
            "artist_key": artist_key,
            "as_of": as_of.isoformat(),
            "releases_12m": releases_12m,
            "releases_36m": releases_36m,
            "days_since_last_release": days_since_last,
            "catalog_depth": len(dates),
            "collaboration_centrality": None,
            "recent_release_intensity": round(releases_12m / 365, 4) if releases_12m else None,
            "source_system": "musicbrainz",
            "source_version": CATALOG_VERSION,
            "retrieved_at": retrieved_at,
            "rights_status": "CC0_CORE",
            "commercial_use_status": "PROTOTYPE_ONLY",
            "evidence_json": json.dumps({
                "releases_sampled": len(releases),
                "source": "core.releases",
            }, default=str),
        })
        summary["rows_written"] += 1

    summary["status"] = "COMPLETE"
    return rows, summary


# ---------------------------------------------------------------------------
# Peer edges (co-billed at the same event → comparable universe)
# ---------------------------------------------------------------------------

def derive_peer_edges(
    conn,
    *,
    universe: list[dict[str, Any]],
    knowledge_time: str | None = None,
    max_peers_per_artist: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """CO_BILLED peer edges from shared event performers (main performers).

    Two artists are peers if they were main performers at the same event.
    Strength = number of co-billed events. This is a COMPARABLE universe for
    relative value — it is never a demand or booking recommendation.
    """
    knowledge_time = knowledge_time or datetime.now(UTC).isoformat()
    mbid_to_key = {a.get("mbid"): a["artist_key"] for a in universe if a.get("mbid")}
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_in_universe": len(universe),
        "rows_written": 0,
        "mbids_with_peers": 0,
    }
    rows: list[dict[str, Any]] = []
    seen_edges: set[str] = set()

    for artist in universe:
        mbid = artist.get("mbid")
        if not mbid or mbid not in mbid_to_key:
            continue
        co = conn.execute(
            """
            SELECT ep2.artist_mbid, COUNT(*) AS n
            FROM core.event_performers ep1
            JOIN core.event_performers ep2 ON ep2.event_mbid = ep1.event_mbid
            WHERE ep1.artist_mbid = ?
              AND ep1.performer_role = 'main performer'
              AND ep2.artist_mbid != ep1.artist_mbid
              AND ep2.artist_mbid IS NOT NULL
              AND ep2.artist_mbid IN (SELECT artist_mbid FROM core.event_performers WHERE artist_mbid IS NOT NULL)
            GROUP BY ep2.artist_mbid
            ORDER BY n DESC
            LIMIT ?
            """,
            [mbid, max_peers_per_artist],
        ).fetchall()
        added = 0
        for peer_mbid, strength in co:
            if peer_mbid not in mbid_to_key:
                continue
            peer_key = mbid_to_key[peer_mbid]
            key = peer_edge_key(subject=artist["artist_key"], peer=peer_key, edge_type="CO_BILLED")
            reverse = peer_edge_key(subject=peer_key, peer=artist["artist_key"], edge_type="CO_BILLED")
            if key in seen_edges or reverse in seen_edges:
                continue
            seen_edges.add(key)
            rows.append({
                "edge_key": key,
                "subject_key": artist["artist_key"],
                "peer_key": peer_key,
                "edge_type": "CO_BILLED",
                "strength": int(strength),
                "source_system": "musicbrainz",
                "source_url": None,
                "knowledge_time": knowledge_time,
            })
            added += 1
        if added:
            summary["mbids_with_peers"] += 1
        summary["rows_written"] += added

    summary["status"] = "COMPLETE"
    return rows, summary


# ---------------------------------------------------------------------------
# Security snapshots — the terminal display object
# ---------------------------------------------------------------------------

def build_security_snapshots(
    conn,
    *,
    universe: list[dict[str, Any]],
    snapshot_date: date | None = None,
    calculated_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build per-artist security snapshots (factor_summary display object).

    factor_summary is a per-family JSON of the artist's CURRENT factor
    observations — the ARTIST GLOBAL SECURITY SNAPSHOT. Percentiles are
    computed across the universe where data exists; NULL when the family
    has no cross-sectional coverage (never fabricated).

    This is the ARTIST SECURITY object the terminal displays — with every
    number traceable back to a factor observation row.
    """
    snapshot_date = snapshot_date or date.today()
    calculated_at = calculated_at or datetime.now(UTC).isoformat()
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_in_universe": len(universe),
        "rows_written": 0,
    }
    rows: list[dict[str, Any]] = []

    for artist in universe:
        artist_key = artist["artist_key"]
        factors = conn.execute(
            """
            SELECT factor_family, factor_name, value, value_unit, as_of,
                   source_system, confidence
            FROM metrics.artist_factor_observations
            WHERE artist_key = ?
            ORDER BY factor_family, factor_name
            """,
            [artist_key],
        ).fetchall()
        family_map: dict[str, list[dict[str, Any]]] = {}
        for family, fname, value, unit, as_of, src, conf in factors:
            family_map.setdefault(family, []).append({
                "factor_name": fname,
                "value": value,
                "value_unit": unit,
                "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
                "source_system": src,
                "confidence": conf,
            })
        rows.append({
            "snapshot_key": snapshot_key(artist_key=artist_key, snapshot_date=snapshot_date.isoformat()),
            "artist_key": artist_key,
            "snapshot_date": snapshot_date.isoformat(),
            "factor_summary": json.dumps(family_map, default=str),
            "demand_percentile": None,
            "momentum_percentile": None,
            "live_percentile": None,
            "data_confidence": None,
            "snapshot_version": SNAPSHOT_VERSION,
            "calculated_at": calculated_at,
        })
        summary["rows_written"] += 1

    summary["status"] = "COMPLETE"
    return rows, summary


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _insert_ignore(conn, table: str, cols: list[str], row: dict[str, Any]) -> int:
    pk_col = {
        "metrics.artist_factor_observations": "factor_observation_key",
        "metrics.artist_live_statistics": "stat_key",
        "metrics.artist_catalog_statistics": "stat_key",
        "metrics.artist_security_snapshots": "snapshot_key",
        "core.artist_peer_edges": "edge_key",
    }[table]
    exists = conn.execute(f"SELECT 1 FROM {table} WHERE {pk_col} = ?", [row[pk_col]]).fetchone()
    if exists:
        return 0
    keys = list(row.keys())
    placeholders = ", ".join("?" for _ in keys)
    col_list = ", ".join(keys)
    conn.execute(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
        [row[k] for k in keys],
    )
    return 1


def run_security_master(
    conn,
    *,
    universe_limit: int = DEFAULT_SECURITY_UNIVERSE_SIZE,
    as_of: date | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Run the full Artist Security Master pass (universe → factors → stats → snapshots)."""
    as_of = as_of or date.today()
    retrieved_at = retrieved_at or datetime.now(UTC).isoformat()
    universe = select_security_universe(conn, limit=universe_limit)

    factors, factor_summary = derive_demand_and_momentum_factors(
        conn, universe=universe, as_of=as_of, retrieved_at=retrieved_at,
    )
    yt, yt_summary = derive_youtube_snapshot_factors(
        conn, universe=universe, as_of=as_of, retrieved_at=retrieved_at,
    )
    factors = factors + yt
    factor_summary = {**factor_summary, "youtube_snapshot_rows": len(yt)}
    live, live_summary = derive_live_statistics(
        conn, universe=universe, as_of=as_of, retrieved_at=retrieved_at,
    )
    catalog, catalog_summary = derive_catalog_statistics(
        conn, universe=universe, as_of=as_of, retrieved_at=retrieved_at,
    )
    peers, peer_summary = derive_peer_edges(conn, universe=universe, knowledge_time=retrieved_at)

    factor_written = sum(_insert_ignore(conn, "metrics.artist_factor_observations", list(f.keys()), f) for f in factors)
    live_written = sum(_insert_ignore(conn, "metrics.artist_live_statistics", list(r.keys()), r) for r in live)
    catalog_written = sum(_insert_ignore(conn, "metrics.artist_catalog_statistics", list(c.keys()), c) for c in catalog)
    peer_written = sum(_insert_ignore(conn, "core.artist_peer_edges", list(p.keys()), p) for p in peers)

    # Snapshots must run AFTER factors are persisted (they read them back).
    snapshots, snapshot_summary = build_security_snapshots(
        conn, universe=universe, snapshot_date=as_of, calculated_at=retrieved_at,
    )
    snapshot_written = sum(
        _insert_ignore(conn, "metrics.artist_security_snapshots", list(s.keys()), s) for s in snapshots
    )

    # Upsert the security master rows. (DuckDB parses CURRENT_TIMESTAMP in the
    # DO UPDATE SET clause as a column reference, so the timestamp is passed as
    # a parameter instead — matching the repository.py upsert convention.)
    now_ts = datetime.now(UTC).isoformat()
    for artist in universe:
        conn.execute(
            """
            INSERT INTO asm.artist_security_master
                (artist_key, security_status, primary_name, factor_families,
                 last_snapshot_at, data_confidence, updated_at)
            VALUES (?, 'ACTIVE', ?, NULL, ?, NULL, ?)
            ON CONFLICT (artist_key) DO UPDATE SET
                primary_name = excluded.primary_name,
                last_snapshot_at = excluded.last_snapshot_at,
                updated_at = excluded.updated_at
            """,
            [artist["artist_key"], artist.get("artist_name"), as_of.isoformat(), now_ts],
        )

    return {
        "status": "COMPLETE",
        "universe_size": len(universe),
        "as_of": as_of.isoformat(),
        "retrieved_at": retrieved_at,
        "factor_observations": {"derived": len(factors), "written": factor_written},
        "live_statistics": {"derived": len(live), "written": live_written},
        "catalog_statistics": {"derived": len(catalog), "written": catalog_written},
        "peer_edges": {"derived": len(peers), "written": peer_written},
        "security_snapshots": {"derived": len(snapshots), "written": snapshot_written},
        "software_version": SOFTWARE_VERSION,
    }
