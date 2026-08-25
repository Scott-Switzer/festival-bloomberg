"""MARKET_COMPETITIVE_CALENDAR_V1 — full public live-event calendar pilot.

Runs the existing recursive date-window Ticketmaster sweep across ALL public
classification segments (Music, Sports, Arts & Theatre, Family, Film,
Miscellaneous) for a bounded set of high-activity markets, in two passes so
point-in-time knowability can be demonstrated honestly (events first observed
in pass 2 are ``observed_post_cutoff`` for a pass-1 decision cutoff).

Then measures the information lift of the full calendar over music-only data:

* events by segment, coordinate coverage, genre/subgenre coverage,
  knowledge-time coverage, duplicate rate, request efficiency,
  partition completeness;
* for target MUSIC events: % with >=1 same-day MUSIC event, % with >=1
  same-day NON-MUSIC event, % whose competitive context changes when non-music
  events are added, % with >=1 event within 5/10/25/50 miles, % with
  defensible PIT classification.

No secret value is ever written to the report.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..acquisition.contracts import utc_now
from ..acquisition.providers.ticketmaster import TicketmasterProvider
from ..acquisition.transport import UrllibTransport
from ..localenv import load_local_env
from ..planning.competitive_calendar import competitive_calendar
from ..warehouse.repository import FestivalRepository
from .data_fabric import _sweep_window

SOFTWARE_VERSION = "market_competitive_calendar_v1"

#: Ticketmaster classification segments to observe (raw provider taxonomy).
SEGMENTS: tuple[str, ...] = (
    "Music",
    "Sports",
    "Arts & Theatre",
    "Family",
    "Film",
    "Miscellaneous",
)

#: Geographically diverse high-activity markets (city, state).
DEFAULT_MARKETS: tuple[tuple[str, str], ...] = (
    ("Los Angeles", "CA"),
    ("New York", "NY"),
    ("Chicago", "IL"),
    ("Las Vegas", "NV"),
    ("Nashville", "TN"),
    ("Dallas", "TX"),
)

FUTURE_WINDOW_DAYS = 90
PILOT_PASSES = 2
PARTITION_COURTESY_SLEEP = 0.25


def _count(conn, sql: str, params: list[Any] | None = None) -> int:
    try:
        return int(conn.execute(sql, params or []).fetchone()[0])
    except Exception:
        return 0


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def run_calendar_sweep(
    conn,
    *,
    markets: tuple[tuple[str, str], ...] = DEFAULT_MARKETS,
    segments: tuple[str, ...] = SEGMENTS,
    window_days: int = FUTURE_WINDOW_DAYS,
    passes: int = PILOT_PASSES,
    transport=None,
) -> dict[str, Any]:
    """Sweep markets x segments x forward window in N passes.

    Every partition terminates explicitly (COMPLETE / SPLIT / TRUNCATED_BY_CAP
    / RATE_LIMITED / ERROR) and is persisted to ``terminal.acquisition_partitions``.
    """
    provider = TicketmasterProvider(transport=transport or UrllibTransport())
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "configured": provider.configured(),
        "passes": 0,
        "partitions": 0,
        "partitions_complete": 0,
        "partitions_split": 0,
        "partitions_truncated": 0,
        "partitions_failed": 0,
        "partitions_rate_limited": 0,
        "requests": 0,
        "rate_limited": 0,
        "provider_errors": 0,
        "events_persisted": 0,
        "market_segment_partitions": 0,
    }
    if not provider.configured():
        summary["status"] = "NOT_CONFIGURED"
        return summary

    start = utc_now() + timedelta(days=1)
    end = start + timedelta(days=window_days)
    for pass_no in range(passes):
        run_retrieved = utc_now().isoformat()
        for city, state in markets:
            for segment in segments:
                summary["status"] = "RUNNING"
                _sweep_window(
                    conn, provider, city, state, start, end,
                    depth=0, parent_id=None, summary=summary,
                    run_retrieved=run_retrieved,
                    classification_name=segment,
                    software_version=SOFTWARE_VERSION,
                )
                summary["market_segment_partitions"] += 1
                if summary["status"] == "NOT_CONFIGURED":
                    summary["status"] = "NOT_CONFIGURED"
                    return summary
                time.sleep(PARTITION_COURTESY_SLEEP)
        summary["passes"] += 1
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    return summary


def _partition_summary(conn) -> dict[str, Any]:
    rows = _rows(
        conn,
        "SELECT status, classification_name, COUNT(*) AS n "
        "FROM terminal.acquisition_partitions "
        "WHERE software_version = ? GROUP BY status, classification_name ORDER BY 2, 1",
        [SOFTWARE_VERSION],
    )
    by_status: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
        by_class[r["classification_name"]] = by_class.get(r["classification_name"], 0) + r["n"]
    return {"by_status": dict(sorted(by_status.items())), "by_classification": dict(sorted(by_class.items()))}


def measure_pilot(
    conn,
    *,
    markets: tuple[tuple[str, str], ...] = DEFAULT_MARKETS,
    target_segment: str = "Music",
    max_targets: int = 300,
    research_cutoff: str | None = None,
) -> dict[str, Any]:
    """Measure full-calendar vs music-only decision context for target events.

    ``research_cutoff`` (e.g. the pass-1 retrieval time) makes the PIT
    classification honest: competitors first observed after it are
    ``observed_post_cutoff`` and never counted as known at the cutoff.
    """
    city_list = [c for c, _ in markets]
    ph = ",".join(f"'{c}'" for c in city_list)
    where_city = f"LOWER(COALESCE(city, '')) IN ({ph.lower()})"

    by_segment = {
        r["segment"]: r["events"]
        for r in _rows(conn, f"""
            SELECT COALESCE(NULLIF(segment, ''), 'Undefined') AS segment, COUNT(DISTINCT platform_object_id) AS events
            FROM events.provider_event_snapshots WHERE provider='ticketmaster' AND {where_city}
            GROUP BY 1 ORDER BY 2 DESC
        """)
    }
    total_events = sum(by_segment.values())
    coords = _count(conn, f"""
        SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots
        WHERE provider='ticketmaster' AND {where_city} AND latitude IS NOT NULL AND longitude IS NOT NULL
    """)
    genre_count = _count(conn, f"""
        SELECT COUNT(DISTINCT genre) FROM events.provider_event_snapshots
        WHERE provider='ticketmaster' AND {where_city} AND genre IS NOT NULL AND genre <> ''
    """)
    subgenre_count = _count(conn, f"""
        SELECT COUNT(DISTINCT subgenre) FROM events.provider_event_snapshots
        WHERE provider='ticketmaster' AND {where_city} AND subgenre IS NOT NULL AND subgenre <> ''
    """)
    snapshots = _count(conn, f"""
        SELECT COUNT(*) FROM events.provider_event_snapshots WHERE provider='ticketmaster' AND {where_city}
    """)
    kt_covered = _count(conn, f"""
        SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots
        WHERE provider='ticketmaster' AND {where_city} AND knowledge_time IS NOT NULL
    """)

    # Target MUSIC events: one per distinct event, with city + coords.
    targets = _rows(conn, f"""
        SELECT platform_object_id AS event_id, MIN(local_date) AS event_date,
               MIN(city) AS city, MIN(COALESCE(state_code, '')) AS state_code,
               MIN(venue_id) AS venue_id, MIN(venue_name) AS venue_name,
               MIN(latitude) AS latitude, MIN(longitude) AS longitude,
               MIN(knowledge_time) AS earliest_knowledge_time
        FROM events.provider_event_snapshots
        WHERE provider='ticketmaster' AND {where_city}
          AND COALESCE(segment, '') = ?
          AND local_date IS NOT NULL
        GROUP BY platform_object_id
        ORDER BY MIN(local_date)
        LIMIT ?
    """, [target_segment, max_targets])

    same_day_music = 0
    same_day_non_music = 0
    context_changes_with_non_music = 0
    within_5 = within_10 = within_25 = within_50 = 0
    pit_defensible = 0
    pit_known = 0
    pit_post = 0
    pit_unknown = 0
    examined = 0
    examples: list[dict[str, Any]] = []

    for t in targets:
        cal = competitive_calendar(
            conn,
            city=t.get("city"),
            state_code=t.get("state_code") or None,
            target_event_id=t["event_id"],
            target_date=str(t["event_date"])[:10],
            target_venue_id=t.get("venue_id"),
            target_lat=float(t["latitude"]) if t.get("latitude") is not None else None,
            target_lon=float(t["longitude"]) if t.get("longitude") is not None else None,
            research_cutoff=research_cutoff,
        )
        if cal["status"] != "OBSERVED":
            continue
        examined += 1
        w0 = cal["windows"].get("pm0") or {}
        same_day_segs = set()
        for bucket in ("known_before_cutoff", "observed_post_cutoff", "unknown_knowledge_time"):
            same_day_segs.update((w0.get(bucket) or {}).keys())
        has_music = any("Music" in s for s in same_day_segs)
        has_non_music = any(s not in ("Music", "Undefined") for s in same_day_segs)
        if has_music:
            same_day_music += 1
        if has_non_music:
            same_day_non_music += 1

        # Context change: any NON-MUSIC competitor inside +-3 days (window pm3).
        w3 = cal["windows"].get("pm3") or {}
        pm3_segs = set()
        for bucket in ("known_before_cutoff", "observed_post_cutoff", "unknown_knowledge_time"):
            pm3_segs.update((w3.get(bucket) or {}).keys())
        if any(s not in ("Music", "Undefined") for s in pm3_segs):
            context_changes_with_non_music += 1

        dist = cal["distance"]
        if dist.get("within_5") or dist.get("same_venue"):
            within_5 += 1
        if dist.get("within_10") or dist.get("within_5") or dist.get("same_venue"):
            within_10 += 1
        if dist.get("within_25") or dist.get("within_10") or dist.get("within_5") or dist.get("same_venue"):
            within_25 += 1
        if dist.get("within_50") or dist.get("within_25") or dist.get("within_10") or dist.get("within_5") or dist.get("same_venue"):
            within_50 += 1

        if research_cutoff:
            if cal["known_at_cutoff"]:
                pit_known += 1
            if cal["observed_after_cutoff"]:
                pit_post += 1
            if cal["unknown_knowledge_time"]:
                pit_unknown += 1
            if cal["known_at_cutoff"] or cal["observed_after_cutoff"]:
                pit_defensible += 1
        elif t.get("earliest_knowledge_time"):
            pit_defensible += 1

        if len(examples) < 12 and (has_non_music or has_music):
            examples.append({
                "target_event_id": t["event_id"],
                "target_name": t.get("venue_name"),
                "target_date": str(t["event_date"])[:10],
                "city": t.get("city"),
                "same_day_music": has_music,
                "same_day_non_music": has_non_music,
                "same_day_by_segment": {
                    k: sorted(v.keys()) for k, v in (w0 or {}).items() if isinstance(v, dict)
                },
                "known_at_cutoff_count": len(cal["known_at_cutoff"]),
                "observed_after_cutoff_count": len(cal["observed_after_cutoff"]),
                "unknown_knowledge_time_count": len(cal["unknown_knowledge_time"]),
                "distance": dist,
            })

    return {
        "target_segment": target_segment,
        "markets": [c for c, _ in markets],
        "targets_available": len(targets),
        "targets_examined": examined,
        "by_segment_events": by_segment,
        "total_events": total_events,
        "coordinate_coverage": round(coords / total_events, 4) if total_events else None,
        "genre_distinct": genre_count,
        "subgenre_distinct": subgenre_count,
        "knowledge_time_coverage": round(kt_covered / total_events, 4) if total_events else None,
        "duplicate_rate": round(snapshots / total_events, 3) if total_events else None,
        "same_day_music_pct": round(same_day_music / examined, 4) if examined else None,
        "same_day_non_music_pct": round(same_day_non_music / examined, 4) if examined else None,
        "context_changes_with_non_music_pct": round(context_changes_with_non_music / examined, 4) if examined else None,
        "within_5_miles_pct": round(within_5 / examined, 4) if examined else None,
        "within_10_miles_pct": round(within_10 / examined, 4) if examined else None,
        "within_25_miles_pct": round(within_25 / examined, 4) if examined else None,
        "within_50_miles_pct": round(within_50 / examined, 4) if examined else None,
        "pit_defensible_pct": round(pit_defensible / examined, 4) if examined else None,
        "pit_known_targets": pit_known,
        "pit_post_targets": pit_post,
        "pit_unknown_targets": pit_unknown,
        "research_cutoff": research_cutoff,
        "examples": examples,
    }


def run_pilot(
    *,
    db_path: str,
    markets: tuple[tuple[str, str], ...] = DEFAULT_MARKETS,
    segments: tuple[str, ...] = SEGMENTS,
    window_days: int = FUTURE_WINDOW_DAYS,
    passes: int = PILOT_PASSES,
    max_targets: int = 300,
    measure_only: bool = False,
) -> dict[str, Any]:
    """Run the real two-pass calendar sweep and the lift measurement.

    ``measure_only=True`` skips the network sweep and measures the persisted
    estate (used when an earlier sweep already acquired the calendar).
    """
    load_local_env()
    repo = FestivalRepository(db_path)
    conn = repo.conn
    try:
        if measure_only:
            sweep = {
                "status": "SKIPPED_MEASURE_ONLY",
                "configured": True,
                "passes": 0, "partitions": 0, "partitions_complete": 0,
                "partitions_split": 0, "partitions_truncated": 0,
                "requests": 0, "rate_limited": 0, "provider_errors": 0,
                "events_persisted": 0, "market_segment_partitions": 0,
            }
        else:
            sweep = run_calendar_sweep(
                conn, markets=markets, segments=segments,
                window_days=window_days, passes=passes,
            )
        partitions = _partition_summary(conn)
        cutoff = None
        # The first pass's retrieval time is the honest decision cutoff for
        # PIT classification: anything first observed in pass 2 is post-cutoff.
        first_pass = _rows(
            conn,
            "SELECT MIN(retrieved_at) AS t0 FROM terminal.acquisition_partitions "
            "WHERE software_version = ? AND classification_name = 'Music'",
            [SOFTWARE_VERSION],
        )
        if first_pass and first_pass[0].get("t0"):
            cutoff = str(first_pass[0]["t0"])
        measurement = measure_pilot(
            conn, markets=markets, research_cutoff=cutoff, max_targets=max_targets,
        )
        report = {
            "software_version": SOFTWARE_VERSION,
            "generated_at": utc_now().isoformat(),
            "sweep": sweep,
            "partitions": partitions,
            "measurement": measurement,
        }
        out_path = "reports/market_competitive_calendar_v1.json"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return report
    finally:
        repo.close()


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/warehouse/boxoffice_research_v2.duckdb"
    window_days = int(sys.argv[2]) if len(sys.argv) > 2 else FUTURE_WINDOW_DAYS
    measure_only = "--measure-only" in sys.argv
    report = run_pilot(db_path=db_path, window_days=window_days, measure_only=measure_only)
    sweep = report["sweep"]
    m = report["measurement"]
    print("=== MARKET_COMPETITIVE_CALENDAR_V1 PILOT ===")
    print(f"sweep status: {sweep['status']}  passes={sweep['passes']}")
    print(f"partitions: {sweep['partitions']}  complete={sweep['partitions_complete']} "
          f"split={sweep['partitions_split']} truncated={sweep['partitions_truncated']} "
          f"failed={sweep['provider_errors']} rate_limited={sweep['rate_limited']}")
    print(f"requests: {sweep['requests']}  events_persisted: {sweep['events_persisted']}")
    print(f"events by segment: {json.dumps(m['by_segment_events'])}")
    print(f"same-day music %: {m['same_day_music_pct']}  non-music %: {m['same_day_non_music_pct']}")
    print(f"context changes with non-music %: {m['context_changes_with_non_music_pct']}")
    print(f"within 5/10/25/50 mi %: {m['within_5_miles_pct']}/{m['within_10_miles_pct']}/"
          f"{m['within_25_miles_pct']}/{m['within_50_miles_pct']}")
    print(f"pit defensible %: {m['pit_defensible_pct']}")


if __name__ == "__main__":
    main()
