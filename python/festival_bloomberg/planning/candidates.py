"""Candidate universe generation + professional artist scorecard.

Candidate generation is deterministic and explainable: every candidate carries
inclusion reasons with their evidence source. Availability is never invented
(CONFIRMED_CONFLICT / POSSIBLE_CONFLICT / NO_CONFLICT_OBSERVED / UNKNOWN;
NO_CONFLICT_OBSERVED != AVAILABLE).

The scorecard is a consolidated talent-buyer read model. Every section is
OBSERVED / DERIVED / ESTIMATED / UNKNOWN. There is deliberately NO opaque
one-number artist score.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..research.comparable import point_in_time_candidates, retrieve_stratum
from ..research.features import TARGET_ATTENDANCE, TARGET_GROSS

CORPUS_MANIFEST = Path("reports/baseline_research_v1/corpus_v1_manifest.json")

INCLUSION_REASONS = (
    "RECENT_FESTIVAL_ARTIST",      # appeared at a major festival recently
    "TOURING_IN_REGION",           # upcoming ticketmaster event in/near market
    "ATTENTION_MOMENTUM",          # rising attention (wikimedia/listenbrainz)
    "WATCHLIST_TARGET",            # already on a user watchlist
    "COMPARABLE_TO_PRIOR_BOOKING", # historical comparable to a prior booking
    "HISTORICAL_MARKET_ACTIVITY",  # historical shows in this market
)

_NORM = re.compile(r"[^a-z0-9]+")


def _norm(name: str | None) -> str:
    return _NORM.sub(" ", (name or "").lower()).strip()


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _corpus_rows() -> list[dict[str, Any]]:
    if not CORPUS_MANIFEST.exists():
        return []
    try:
        manifest = json.loads(CORPUS_MANIFEST.read_text())
        return manifest.get("rows", [])
    except (ValueError, OSError):
        return []


# ---------------------------------------------------------------------------
# Candidate universe (deterministic, evidence-backed)
# ---------------------------------------------------------------------------
def _candidate_from_event_performers(conn, *, limit: int) -> list[dict[str, Any]]:
    """RECENT_FESTIVAL_ARTIST: artists with reference-graph festival events."""
    try:
        return _rows(
            conn,
            """
            SELECT ep.artist_mbid AS musicbrainz_id, ep.artist_name AS name,
                   COUNT(*) AS evidence_count
            FROM core.event_performers ep
            JOIN core.series_events se ON se.event_mbid = ep.event_mbid
            JOIN core.event_series s ON s.series_key = se.series_key
            WHERE s.series_type = 'FESTIVAL'
            GROUP BY ep.artist_mbid, ep.artist_name
            ORDER BY evidence_count DESC LIMIT ?
            """,
            [limit],
        )
    except Exception:  # noqa: BLE001 - table may be absent in fresh test DBs
        return []


def _candidate_from_upcoming(conn, *, market: str | None, limit: int) -> list[dict[str, Any]]:
    """TOURING_IN_REGION: upcoming Ticketmaster events with attraction artists."""
    try:
        params: list[Any] = [datetime.now(timezone.utc).strftime("%Y-%m-%d")]
        sql = """
            SELECT DISTINCT a.value AS name, COUNT(*) AS evidence_count
            FROM events.provider_event_snapshots e,
                 json_extract(e.attractions, '$[*].ticketmaster_attraction_id') AS a
            WHERE e.provider = 'ticketmaster' AND e.local_date >= ?
        """
        if market:
            sql += " AND LOWER(e.city) LIKE ?"
            params.append(f"%{market.lower()}%")
        sql += " GROUP BY a.value ORDER BY evidence_count DESC LIMIT ?"
        params.append(limit)
        return _rows(conn, sql, params)
    except Exception:  # noqa: BLE001
        return []


def _candidate_from_attention(conn, *, limit: int) -> list[dict[str, Any]]:
    """ATTENTION_MOMENTUM: artists with the most recent attention observations."""
    try:
        return _rows(
            conn,
            """
            SELECT artist_key AS name, COUNT(*) AS evidence_count,
                   MAX(retrieved_at) AS latest
            FROM metrics.artist_attention_observations
            WHERE status = 'ok'
            GROUP BY artist_key
            ORDER BY MAX(retrieved_at) DESC LIMIT ?
            """,
            [limit],
        )
    except Exception:  # noqa: BLE001
        return []


def _candidate_from_watchlist(conn, *, limit: int) -> list[dict[str, Any]]:
    try:
        return _rows(
            conn,
            """
            SELECT entity_key AS artist_key, entity_name AS name, 1 AS evidence_count
            FROM core.watchlist_items
            WHERE removed_at IS NULL AND entity_type = 'ARTIST'
            ORDER BY entity_name LIMIT ?
            """,
            [limit],
        )
    except Exception:  # noqa: BLE001
        return []


def _candidate_from_boxoffice(conn, *, limit: int) -> list[dict[str, Any]]:
    """COMPARABLE_TO_PRIOR_BOOKING: most-recorded artists in the box-office corpus."""
    rows = _corpus_rows()
    counts: dict[str, int] = {}
    for r in rows:
        name = r.get("artist")
        if name:
            counts[name] = counts.get(name, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"name": name, "evidence_count": n} for name, n in top]


def build_candidate_universe(
    conn, *, project_key: str, market: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """Assemble the deterministic candidate universe for a project.

    Sources run independently; candidates are merged by normalized name and
    every inclusion reason is recorded with its evidence. Availability stays
    UNKNOWN until evidence exists.
    """
    from .repository import add_candidate, get_project

    project = get_project(conn, project_key)
    if project is None:
        raise ValueError(f"unknown project {project_key}")
    market = market or project.get("market")

    merged: dict[str, dict[str, Any]] = {}
    sources = [
        ("RECENT_FESTIVAL_ARTIST", _candidate_from_event_performers(conn, limit=limit)),
        ("TOURING_IN_REGION", _candidate_from_upcoming(conn, market=market, limit=limit)),
        ("ATTENTION_MOMENTUM", _candidate_from_attention(conn, limit=limit)),
        ("WATCHLIST_TARGET", _candidate_from_watchlist(conn, limit=limit)),
        ("COMPARABLE_TO_PRIOR_BOOKING", _candidate_from_boxoffice(conn, limit=limit)),
    ]
    for reason, rows in sources:
        for r in rows:
            name = r.get("name") or r.get("artist_name")
            if not name:
                continue
            nkey = _norm(name)
            entry = merged.setdefault(nkey, {
                "name": name, "artist_key": r.get("artist_key"),
                "musicbrainz_id": r.get("musicbrainz_id"),
                "reasons": [], "evidence": 0,
            })
            entry["reasons"].append({
                "reason": reason,
                "evidence": f"{r.get('evidence_count', 1)} records",
                "source": _reason_source(reason),
            })
            entry["evidence"] += int(r.get("evidence_count", 1))

    added = []
    for nkey, entry in sorted(merged.items(), key=lambda kv: kv[1]["evidence"], reverse=True):
        res = add_candidate(
            conn, project_key=project_key, artist_key=entry["artist_key"],
            artist_name=entry["name"], musicbrainz_id=entry.get("musicbrainz_id"),
            inclusion_reasons=entry["reasons"],
        )
        added.append(res)
    return {
        "project_key": project_key,
        "candidates_added": len(added),
        "reason_counts": {
            reason: sum(1 for e in merged.values() for r in e["reasons"] if r["reason"] == reason)
            for reason, _ in sources
        },
    }


def _reason_source(reason: str) -> str:
    return {
        "RECENT_FESTIVAL_ARTIST": "musicbrainz_festival_graph",
        "TOURING_IN_REGION": "ticketmaster_forward_watch",
        "ATTENTION_MOMENTUM": "metrics.artist_attention_observations",
        "WATCHLIST_TARGET": "core.watchlist_items",
        "COMPARABLE_TO_PRIOR_BOOKING": "boxoffice_research_corpus_v1",
        "HISTORICAL_MARKET_ACTIVITY": "boxoffice_research_corpus_v1",
    }.get(reason, "unknown")


# ---------------------------------------------------------------------------
# Professional artist scorecard
# ---------------------------------------------------------------------------
def _comparable_ranges(artist_name: str) -> dict[str, Any]:
    """PIT-safe comparable gross/attendance ranges from the frozen corpus."""
    rows = _corpus_rows()
    if not rows:
        return {"gross": {"status": "UNKNOWN"}, "attendance": {"status": "UNKNOWN"}}
    target = {"artist": artist_name, "start_date": "2027-01-01",
              "publication_time": "2026-12-31"}
    out: dict[str, Any] = {}
    for target_type, key in ((TARGET_GROSS, "gross"), (TARGET_ATTENDANCE, "attendance")):
        value_fn = (lambda r: r.get("ticket_gross_total")) if target_type == TARGET_GROSS \
            else (lambda r: r.get("headcount_total"))
        cands = point_in_time_candidates(target, rows)
        res = retrieve_stratum(target, cands, value_fn=value_fn, k=10)
        if res["valuation"] is None:
            out[key] = {"status": "UNKNOWN", "stratum": res["stratum"]}
            continue
        # The evidence class follows the STRATUM: artist-specific comps are
        # OBSERVED artist evidence; venue/market comps are DERIVED; a
        # broad-fallback range is NOT evidence about this artist at all.
        stratum = res["stratum"]
        if stratum in ("SAME_ARTIST_VENUE", "SAME_ARTIST_MARKET", "SAME_ARTIST"):
            status = "OBSERVED"
        elif stratum in ("SAME_VENUE", "SAME_MARKET"):
            status = "DERIVED"
        else:
            status = "UNKNOWN"  # broad fallback is a market baseline, not artist evidence
        v = res["valuation"]
        out[key] = {
            "status": status,
            "weighted_median": v["weighted_median"],
            "p10": v["p10"], "p90": v["p90"],
            "effective_sample_size": v["effective_sample_size"],
            "stratum": stratum,
        }
    return out


def artist_scorecard(
    conn, *, artist_key: str | None = None, artist_name: str | None = None,
) -> dict[str, Any]:
    """Consolidated talent-buyer scorecard for one artist.

    Sections: identity, live history, festival history, attention, market
    history, comparables, coverage, evidence. UNKNOWN fields are explicit.
    """
    name = artist_name or ""
    card: dict[str, Any] = {"artist_key": artist_key, "artist_name": name}

    # -- identity ----------------------------------------------------------
    identity = {"name": name, "external_ids": {}, "type": None, "area": None,
                "matched": False}
    if artist_key:
        rows = _rows(conn, "SELECT * FROM core.artists WHERE artist_key = ?", [artist_key])
        if rows:
            a = rows[0]
            identity.update({
                "name": a.get("name"),
                "type": a.get("artist_type"),
                "area": a.get("area_name") or a.get("area_mbid"),
                "disambiguation": a.get("disambiguation"),
                "matched": True,
            })
            for col, ns in (("musicbrainz_id", "musicbrainz"), ("spotify_id", "spotify"),
                            ("wikidata_id", "wikidata"), ("youtube_id", "youtube"),
                            ("isni", "isni"), ("ipi", "ipi")):
                if a.get(col):
                    identity["external_ids"][ns] = a[col]
    elif name:
        rows = _rows(
            conn, "SELECT * FROM core.artists WHERE normalized_name = ? OR name = ? LIMIT 1",
            [_norm(name), name],
        )
        if rows:
            a = rows[0]
            card["artist_key"] = artist_key = a["artist_key"]
            identity.update({
                "name": a.get("name"), "type": a.get("artist_type"),
                "area": a.get("area_name") or a.get("area_mbid"),
                "matched": True,
            })
            for col, ns in (("musicbrainz_id", "musicbrainz"), ("spotify_id", "spotify"),
                            ("wikidata_id", "wikidata"), ("youtube_id", "youtube"),
                            ("isni", "isni"), ("ipi", "ipi")):
                if a.get(col):
                    identity["external_ids"][ns] = a[col]
    card["identity"] = identity

    # -- live history (forward watch snapshots by name match) ---------------
    live = {"upcoming_count": 0, "historical_count": 0, "events": []}
    try:
        live_rows = _rows(
            conn,
            """
            SELECT platform_object_id, event_name, venue_name, city, local_date,
                   event_status, price_min, price_max
            FROM events.provider_event_snapshots
            WHERE provider = 'ticketmaster' AND LOWER(COALESCE(artist_name,'')) LIKE ?
            ORDER BY local_date DESC LIMIT 50
            """,
            [f"%{_norm(name)}%"],
        )
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        live["events"] = live_rows
        live["upcoming_count"] = sum(1 for r in live_rows if str(r.get("local_date") or "") >= today)
        live["historical_count"] = len(live_rows) - live["upcoming_count"]
    except Exception:  # noqa: BLE001
        pass
    card["live"] = live

    # -- festival history ----------------------------------------------------
    fest = {"festival_count": 0, "edition_count": 0, "appearances": [],
            "billing_tiers": {}}
    try:
        slots = _rows(
            conn,
            """
            SELECT festival_key, year, stage_name, billing_tier, billing_order,
                   performance_date, announcement_date
            FROM core.lineup_slots
            WHERE LOWER(normalized_artist_name) LIKE ?
            ORDER BY year DESC, billing_order NULLS LAST LIMIT 50
            """,
            [f"%{_norm(name)}%"],
        )
        fest["appearances"] = slots
        fest["edition_count"] = len(slots)
        fest["festival_count"] = len({r["festival_key"] for r in slots})
        tiers: dict[str, int] = {}
        for r in slots:
            t = r.get("billing_tier") or "UNKNOWN"
            tiers[t] = tiers.get(t, 0) + 1
        fest["billing_tiers"] = tiers
    except Exception:  # noqa: BLE001
        pass
    card["festival"] = fest

    # -- attention ------------------------------------------------------------
    attention = {"wikimedia": None, "listenbrainz": None, "latest": []}
    try:
        rows = _rows(
            conn,
            """
            SELECT source_system, metric_kind, value_sum, value_unit,
                   period_start, retrieved_at
            FROM metrics.artist_attention_observations
            WHERE status = 'ok' AND (
                LOWER(artist_key) LIKE ? OR LOWER(COALESCE(article_title,'')) LIKE ?
            )
            ORDER BY retrieved_at DESC LIMIT 20
            """,
            [f"%{_norm(name)}%", f"%{_norm(name)}%"],
        )
        attention["latest"] = rows
        for r in rows:
            src = r["source_system"]
            if attention.get(src) is None:
                attention[src] = {"metric_kind": r["metric_kind"],
                                  "value_sum": r["value_sum"],
                                  "value_unit": r["value_unit"],
                                  "period_start": r["period_start"]}
    except Exception:  # noqa: BLE001
        pass
    card["attention"] = attention

    # -- market history + comparables -----------------------------------------
    market_history = {"shows_in_market": 0, "markets": []}
    try:
        mk = _rows(
            conn,
            """
            SELECT city AS market, COUNT(*) AS n, MIN(local_date) AS first_date,
                   MAX(local_date) AS last_date
            FROM events.provider_event_snapshots
            WHERE provider = 'ticketmaster' AND LOWER(COALESCE(artist_name,'')) LIKE ?
            GROUP BY city ORDER BY n DESC LIMIT 10
            """,
            [f"%{_norm(name)}%"],
        )
        market_history["markets"] = mk
        market_history["shows_in_market"] = sum(r["n"] for r in mk)
    except Exception:  # noqa: BLE001
        pass
    card["market_history"] = market_history
    card["comparables"] = _comparable_ranges(name)

    # -- coverage ---------------------------------------------------------------
    coverage = {
        "identity": 1 if card["identity"].get("matched") else 0,
        "live_history": min(live["historical_count"] + live["upcoming_count"], 1),
        "festival_history": min(fest["edition_count"], 1),
        "attention": 1 if attention["latest"] else 0,
        "market_history": min(market_history["shows_in_market"], 1),
        "comparables": 1 if card["comparables"].get("gross", {}).get("status") == "OBSERVED" else 0,
    }
    coverage["known_source_count"] = sum(coverage.values())
    coverage["coverage_score"] = round(coverage["known_source_count"] / max(len(coverage) - 2, 1), 3)
    card["coverage"] = coverage
    return card
