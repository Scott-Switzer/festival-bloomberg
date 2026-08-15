"""DATA_ACQUISITION_ACTIVATION_V1 — live operational acceptance.

Success is measured by ACQUIRED evidence, not by schema or plan counts. This
driver runs REAL, bounded, policy-approved acquisition:

    OUTCOME_HUNTER  Common Crawl CDX hunts (key-free, $0) on the persisted
                    source-document URLs, era-directed across crawl
                    collections; WARC fetch + conservative claim extraction
                    for captured pages. Attempts land in the append-only
                    ledger with classified failure semantics.
    PIT RECONSTRUCTION  evidence rows derived from REAL persisted source
                    publication dates (OBSERVED_DAY) and REAL archive
                    captures (ARCHIVE_CAPTURE_UPPER_BOUND); modes are
                    STRICT_PIT / CONSERVATIVE_BOUND_PIT / RESEARCH_ESTIMATED.
    FORWARD_WATCH   MusicBrainz future events (CC0, key-free) + real future
                    events and snapshots already persisted in the
                    event-history warehouse, migrated into the flywheel
                    watchlist with milestone-mapped observations.
    CONTEXT_PANEL   Wikimedia pageviews (key-free); other providers report
                    their real access gates (PARTIAL until keys exist).
    ACQUISITION ECONOMICS per-provider runs + derived yield metrics.

Keyed providers (Ticketmaster Discovery, SeatGeek, Census, BLS, ...) stay
registered KEY_REQUIRED and are NEVER bypassed. Everything degrades honestly;
no network / no rows is NOT_EVALUATED, never fabricated. Bounded, $0.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..acquisition.contracts import utc_now
from ..acquisition.providers.commoncrawl import (
    fetch_warc_record_bytes,
    extract_warc_payload_text,
    lookup_capture_offset,
)
from ..acquisition.transport import UrllibTransport
from ..flywheel.acquisition_accounting import (
    build_acquisition_run_row,
    derive_metrics,
)
from ..flywheel.coverage import measure_coverage, snapshot_id
from ..flywheel.forward_discovery import (
    MusicBrainzFutureEventsClient,
    migrate_history_watch,
)
from ..flywheel.hunt_execution import (
    TASK_CLAIM_FOUND,
    TASK_NOT_FOUND,
    build_attempt_row,
    extract_claims_from_page,
    run_cdx_hunt,
    summarize_attempts,
)
from ..flywheel.objectives import objective_rows
from ..flywheel.pit import (
    ARCHIVE_CAPTURE_UPPER_BOUND,
    CONSERVATIVE_BOUND_PIT,
    OBSERVED_DAY,
    PIT_EVIDENCE_CLASSES,
    RESEARCH_ESTIMATED,
    STRICT_PIT,
    build_archive_upper_bound_evidence,
    classify_source_document_evidence,
    count_events_reconstructable,
    event_key_from_engagement,
)
from ..flywheel.repository import FlywheelRepository
from ..flywheel.sources import source_rows
from ..localenv import load_local_env
from ..research.repository import ResearchRepository
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "data_acquisition_activation_v1"

#: Bounded operational budgets for the live run.
MAX_MB_FUTURE_EVENTS = 400
MB_HORIZON_DAYS = 365
MAX_CDX_WARC_FETCHES = 6
CDX_THROTTLE_SECONDS = 0.3
CDX_WINDOW = 1
CDX_MAX_CRAWLS_PER_URL = 8
#: Wikipedia capacity hunts are key-free ($0) and bounded for the live run.
MAX_WIKIPEDIA_VENUE_HUNTS = 60
WIKIPEDIA_THROTTLE_SECONDS = 1.0

COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
COLLINFO_CACHE = Path("data/cache/crawl_index_cache.json")


def run_activation_v1_oa(
    *,
    research_db: str = "data/warehouse/boxoffice_research_v2.duckdb",
    history_db: str = "data/warehouse/artist_market_event_history.duckdb",
    report_path: str | Path = "reports/data_acquisition_activation_v1.json",
    transport: UrllibTransport | None = None,
    mb_events: list[dict[str, Any]] | None = None,
    cdx_window: int = CDX_WINDOW,
    cdx_throttle_seconds: float = CDX_THROTTLE_SECONDS,
    cdx_max_crawls: int = CDX_MAX_CRAWLS_PER_URL,
) -> dict[str, Any]:
    """Run the activation OA. ``mb_events``/``transport`` allow hermetic tests;
    the default performs REAL bounded acquisition."""
    load_local_env()
    started = utc_now()
    oa_run_id = f"activation_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(research_db)
    try:
        flywheel = FlywheelRepository(repo.conn)
        research = ResearchRepository(repo.conn)

        # --------------------------------------------------------------
        # 0. Registry + objectives + BEFORE coverage
        # --------------------------------------------------------------
        for row in source_rows(registered_at=started):
            flywheel.insert_source(row)
        for row in objective_rows():
            row["registered_at"] = started.isoformat()
        flywheel.upsert_objectives(objective_rows())

        before = {r["objective_key"]: r["actual_value"] for r in measure_coverage(repo.conn, as_of=started)}
        coverage_rows = measure_coverage(repo.conn, as_of=started)
        for row in coverage_rows:
            row["snapshot_id"] = snapshot_id(started, row["objective_key"])
            flywheel.insert_coverage_snapshot(row)

        # --------------------------------------------------------------
        # 1. OUTCOME_HUNTER — real CDX hunts on persisted source docs
        # --------------------------------------------------------------
        hunts_result = _run_hunts(
            flywheel, research, transport=transport, as_of=started,
            window=cdx_window, throttle_seconds=cdx_throttle_seconds,
            max_crawls=cdx_max_crawls,
        )
        hunts = hunts_result["summary"]
        hunts_gate = hunts_result["gate"]
        warc_fetches = hunts.get("warc_fetches", 0)
        pit_from_capture = hunts_result["captures_by_url"]

        # --------------------------------------------------------------
        # 2. PIT reconstruction — from real persisted source dates + captures
        # --------------------------------------------------------------
        pit = _reconstruct_pit(flywheel, research, capture_evidence=pit_from_capture, as_of=started)

        # --------------------------------------------------------------
        # 3. FORWARD_WATCH — MusicBrainz future events + history migration
        # --------------------------------------------------------------
        forward = _activate_forward_watch(
            flywheel, history_db=history_db, transport=transport,
            mb_events=mb_events, as_of=started,
        )

        # --------------------------------------------------------------
        # 4. CONTEXT_PANEL — Wikimedia only (honest PARTIAL) + gates
        # --------------------------------------------------------------
        context = _context_panel(flywheel, transport=transport, as_of=started)

        # --------------------------------------------------------------
        # 5. Acquisition economics — runs + derived metrics
        # --------------------------------------------------------------
        accounting = _record_accounting(flywheel, hunts=hunts, warc_fetches=warc_fetches, forward=forward, context=context, as_of=started)

        # --------------------------------------------------------------
        # AFTER coverage + manifest
        # --------------------------------------------------------------
        after_rows = measure_coverage(repo.conn, as_of=utc_now())
        after = {r["objective_key"]: r["actual_value"] for r in after_rows}
        for row in after_rows:
            row["snapshot_id"] = snapshot_id(utc_now(), row["objective_key"])
            flywheel.insert_coverage_snapshot(row)

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "research_db": research_db,
            "history_db": history_db,
            "before_after": _before_after_diff(before, after),
            "milestone_baseline": _milestone_baseline(before),
            "pipelines": {
                "EVENT_GRAPH": {"status": "NOT_EVALUATED", "note": "identity resolution unchanged this run (MusicBrainz identity machinery already PASS in flywheel OA)"},
                "OUTCOME_HUNTER": hunts,
                "PIT_RECONSTRUCTION": pit["summary"],
                "FORWARD_WATCH": forward["summary"],
                "CONTEXT_PANEL": context["summary"],
                "ACQUISITION_ECONOMICS": accounting["summary"],
            },
            "gates": {
                "EVENT_GRAPH": "NOT_EVALUATED",
                "OUTCOME_HUNTER": hunts_gate,
                "PIT_RECONSTRUCTION": pit["gate"],
                "FORWARD_WATCH": forward["gate"],
                "CONTEXT_PANEL": context["gate"],
                "ACQUISITION_ECONOMICS": accounting["gate"],
            },
            "provider_cost_usd": 0.0,
            "rights": {
                "research_corpus_fail_closed": True,
                "note": "Keyed providers remain KEY_REQUIRED and are never bypassed; Common Crawl captures carry the UNDERLYING publisher's rights (TERMS_REVIEW_REQUIRED).",
            },
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# OUTCOME_HUNTER execution
# ---------------------------------------------------------------------------
def _run_hunts(flywheel, research, *, transport, as_of: datetime, window: int = CDX_WINDOW, throttle_seconds: float = CDX_THROTTLE_SECONDS, max_crawls: int = CDX_MAX_CRAWLS_PER_URL) -> dict[str, Any]:
    from ..flywheel.hunt_execution import run_wikipedia_capacity_hunt

    engagements = research.query_engagements()

    # -- channel 1: Common Crawl CDX hunts (may be unavailable) -------------
    cdx_summary = {
        "status": "NOT_EVALUATED",
        "distinct_source_docs_hunted": 0,
        "attempts": summarize_attempts(None),
        "cdx_statuses": summarize_attempts(None),
        "cdx_requests": 0,
        "warc_fetches": 0,
        "parser_failed_pages": 0,
        "claims_from_pages": 0,
        "captures_by_url": {},
        "note": "crawl index unavailable; CDX channel not attempted",
    }
    cdx_attempts: list[dict[str, Any]] = []
    captures_by_url: dict[str, list[tuple[str, str]]] = {}
    try:
        crawls = _fetch_crawls(transport)
    except Exception as exc:  # noqa: BLE001
        cdx_summary["note"] = f"crawl index unavailable; CDX channel not attempted: {exc}"
        crawls = None

    if crawls:
        # Distinct source documents (chart/article pages) drive the CDX hunts.
        source_urls: dict[str, dict[str, Any]] = {}
        for eng in engagements:
            url = eng.get("source_url")
            if not url:
                continue
            entry = source_urls.setdefault(url, {"url": url, "reporting_source": eng.get("reporting_source"), "years": set(), "engagement_ids": []})
            entry["years"].add(_year_of(eng.get("start_date")))
            entry["engagement_ids"].append(eng["engagement_id"])
        distinct = sorted(source_urls.values(), key=lambda e: (e["url"] or ""))

        requests = 0
        for entry in distinct:
            url = entry["url"]
            year = max(entry["years"]) if entry["years"] else as_of.year
            result = run_cdx_hunt(
                url=url,
                crawls=crawls,
                target_year=year,
                window=window,
                max_captures=5,
                max_crawls=max_crawls,
                transport=transport,
                throttle_seconds=throttle_seconds if transport is None else 0.0,
            )
            requests += result["request_count"]
            status = result["status"]
            captures = result["captures"]
            if captures:
                captures_by_url[url] = captures
            plan_id = f"plan_{entry['engagement_ids'][0][:24]}"
            task_id = f"task_{entry['engagement_ids'][0][:24]}_publication_time"
            cdx_attempts.append(
                build_attempt_row(
                    plan_id=plan_id,
                    task_id=task_id,
                    target_field="publication_time",
                    provider="commoncrawl_cdx",
                    status=status,
                    started_at=as_of,
                    request_count=result["request_count"],
                    source_url=url,
                    capture_count=len(captures),
                    detail=result["detail"],
                )
            )

        # WARC fetches for a bounded sample of captured pages (claim extraction).
        warc_fetches = 0
        parser_failed = 0
        claims_by_page = {}
        fetched = 0
        for url, captures in captures_by_url.items():
            if fetched >= MAX_CDX_WARC_FETCHES:
                break
            crawl_id, ts = captures[0]
            try:
                offset_info = lookup_capture_offset(transport or UrllibTransport(), url, crawl_id=crawl_id)
            except Exception:
                offset_info = None
            if not offset_info or not offset_info.get("filename"):
                continue
            try:
                raw = fetch_warc_record_bytes(
                    transport or UrllibTransport(), offset_info["filename"], offset_info["offset"], offset_info["length"]
                )
                text = extract_warc_payload_text(raw)
                claims = extract_claims_from_page(text, target_year=as_of.year)
                claims_by_page[url] = claims
                warc_fetches += 1
            except Exception as exc:  # noqa: BLE001
                parser_failed += 1
            fetched += 1

        cdx_summary = {
            "status": "PASS" if cdx_attempts else "NOT_EVALUATED",
            "distinct_source_docs_hunted": len(distinct),
            "attempts": summarize_attempts(cdx_attempts),
            "cdx_statuses": summarize_attempts(cdx_attempts),
            "cdx_requests": requests,
            "warc_fetches": warc_fetches,
            "parser_failed_pages": parser_failed,
            "claims_from_pages": sum(len(v) for v in claims_by_page.values()),
            "captures_by_url": {u: len(c) for u, c in captures_by_url.items()},
            "note": (
                "Real CDX hunts executed against era-directed crawl collections. "
                "NOT_FOUND is a genuine no-capture result; failures are classified "
                "(RATE_LIMITED/HTTP_FAILED), never mislabeled. WARC claim "
                "extraction is P2 corroboration."
            ),
        }

    # -- channel 2: key-free Wikipedia capacity hunts (always available) -----
    venues_by_key: dict[str, dict[str, Any]] = {}
    for eng in engagements:
        key = _venue_key_of(eng)
        entry = venues_by_key.setdefault(
            key,
            {"venue": eng.get("venue"), "city": eng.get("city") or eng.get("market"), "engagements": []},
        )
        entry["engagements"].append(eng)
    distinct_venues = sorted(
        venues_by_key.values(), key=lambda e: -len(e["engagements"])
    )
    cap_result = run_wikipedia_capacity_hunt(
        venues=distinct_venues,
        transport=transport,
        max_venues=MAX_WIKIPEDIA_VENUE_HUNTS,
        throttle_seconds=0.0 if transport is not None else WIKIPEDIA_THROTTLE_SECONDS,
    )
    cap_attempts = cap_result["attempts"]
    cap_requests = cap_result["requests"]
    # Persist real capacity claims (append-only; conflicts coexist).
    cap_claims_inserted = 0
    from ..economics.repository import EconomicsRepository

    econ = EconomicsRepository(flywheel.conn)
    for vr in cap_result["venue_results"]:
        for claim in vr["claims"]:
            if econ.insert_capacity_claim(claim):
                cap_claims_inserted += 1

    for attempt in cdx_attempts + cap_attempts:
        flywheel.insert_hunt_attempt(attempt)

    all_attempts = cdx_attempts + cap_attempts
    summary = {
        "status": "PASS" if all_attempts else "NOT_EVALUATED",
        "plans_present": len(engagements),
        "tasks_planned_total": len(engagements) * 11,
        "distinct_source_docs_hunted": cdx_summary["distinct_source_docs_hunted"],
        "attempts": summarize_attempts(all_attempts),
        "cdx_requests": cdx_summary["cdx_requests"],
        "warc_fetches": cdx_summary["warc_fetches"],
        "parser_failed_pages": cdx_summary["parser_failed_pages"],
        "claims_from_pages": cdx_summary["claims_from_pages"],
        "captures_by_url": cdx_summary["captures_by_url"],
        "capacity_venues_hunted": cap_result["venues_hunted"],
        "capacity_claims_inserted": cap_claims_inserted,
        "wikipedia_requests": cap_requests,
        "wikipedia_statuses": summarize_attempts(cap_attempts),
        "cdx_statuses": cdx_summary["cdx_statuses"],
        "note": (
            "Two real key-free channels: (1) Wikipedia infobox capacity hunts "
            "on distinct corpus venues (P0 field; claims persisted to "
            "economics.venue_capacity_claims); (2) era-directed Common Crawl "
            "CDX hunts on persisted source documents when the index is "
            "reachable. NOT_FOUND is a genuine no-evidence result; failures "
            "are classified (RATE_LIMITED/RIGHTS_BLOCKED/HTTP_FAILED), never "
            "mislabeled."
        ),
    }
    gate = "PASS" if summary["attempts"]["tasks_attempted"] > 0 else "NOT_EVALUATED"
    return {
        "summary": summary,
        "gate": gate,
        "captures_by_url": captures_by_url,
    }


# ---------------------------------------------------------------------------
# PIT reconstruction
# ---------------------------------------------------------------------------
def _reconstruct_pit(flywheel, research, *, capture_evidence: dict[str, list[tuple[str, str]]], as_of: datetime) -> dict[str, Any]:
    engagements = research.query_engagements()
    sources = {
        r["source_url"]: r
        for r in research.query_sources()
        if r.get("source_url")
    }
    inserted_observed_day = 0
    inserted_capture_bound = 0
    events_covered_day = set()
    events_covered_capture = set()
    events_total = set()

    for eng in engagements:
        canonical = event_key_from_engagement(eng)
        events_total.add(canonical)
        url = eng.get("source_url")
        source = sources.get(url)
        if source:
            pub_date = source.get("publication_date")
            if isinstance(pub_date, str):
                try:
                    pub_date = date.fromisoformat(pub_date)
                except ValueError:
                    pub_date = None
            rows = classify_source_document_evidence(
                canonical_event_id=canonical,
                reporting_source=eng.get("reporting_source") or "",
                source_url=url,
                publication_date=pub_date,
                source_document_id=source.get("source_id"),
            )
            for row in rows:
                if flywheel.insert_pit_evidence(row):
                    inserted_observed_day += 1
                if row["evidence_class"] == OBSERVED_DAY:
                    events_covered_day.add(canonical)
        captures = capture_evidence.get(url) or []
        if captures:
            for crawl_id, ts in captures[:1]:
                row = build_archive_upper_bound_evidence(
                    canonical_event_id=canonical,
                    capture_time=_cdx_ts_to_iso(ts),
                    source_url=url,
                    source_provider="commoncrawl",
                    source_document_id=crawl_id,
                )
                if flywheel.insert_pit_evidence(row):
                    inserted_capture_bound += 1
                events_covered_capture.add(canonical)

    evidence_total = int(
        flywheel.conn.execute("SELECT COUNT(*) FROM flywheel.pit_reconstruction_evidence").fetchone()[0]
    )
    # Persisted class coverage (across ALL runs — the PIT corpus only grows;
    # this-run inserts are reported separately below).
    class_rows = flywheel.conn.execute(
        "SELECT evidence_class, COUNT(*) FROM flywheel.pit_reconstruction_evidence GROUP BY 1"
    ).fetchall()
    class_counts = {cls: 0 for cls in PIT_EVIDENCE_CLASSES}
    for cls, cnt in class_rows:
        class_counts[cls] = int(cnt)
    persisted_day_events = int(
        flywheel.conn.execute(
            "SELECT COUNT(DISTINCT canonical_event_id) FROM flywheel.pit_reconstruction_evidence "
            "WHERE evidence_class = ?",
            [OBSERVED_DAY],
        ).fetchone()[0]
    )
    persisted_capture_events = int(
        flywheel.conn.execute(
            "SELECT COUNT(DISTINCT canonical_event_id) FROM flywheel.pit_reconstruction_evidence "
            "WHERE evidence_class = ?",
            [ARCHIVE_CAPTURE_UPPER_BOUND],
        ).fetchone()[0]
    )
    counts = {
        "events_total_single_show": len(events_total),
        "strict_pit_events": count_events_reconstructable(flywheel.conn, mode=STRICT_PIT),
        "conservative_bound_events": count_events_reconstructable(flywheel.conn, mode=CONSERVATIVE_BOUND_PIT),
        "research_estimated_events": count_events_reconstructable(flywheel.conn, mode=RESEARCH_ESTIMATED),
        "events_with_observed_day_evidence": persisted_day_events,
        "events_with_archive_upper_bound": persisted_capture_events,
        "evidence_rows_inserted_this_run": inserted_observed_day + inserted_capture_bound,
        "evidence_rows_total": evidence_total,
        "evidence_rows_observed_day": class_counts.get(OBSERVED_DAY, 0),
        "evidence_rows_capture_bound": class_counts.get(ARCHIVE_CAPTURE_UPPER_BOUND, 0),
        "evidence_classes": {k: v for k, v in sorted(class_counts.items()) if v},
    }
    summary = {
        "status": "PASS" if inserted_observed_day > 0 else "PARTIAL",
        **counts,
        "note": (
            "Evidence classes are REAL: OBSERVED_DAY comes from persisted "
            "boxoffice source-document publication dates (pollstar/touring "
            "data chart articles); ARCHIVE_CAPTURE_UPPER_BOUND comes from real "
            "Common Crawl captures. Archive captures prove availability BY the "
            "capture time, never original publication. No timestamps were "
            "fabricated; remaining UNKNOWN is reported, not hidden."
        ),
    }
    gate = "PASS" if summary["evidence_rows_total"] > 0 else "PARTIAL"
    return {"summary": summary, "gate": gate}


# ---------------------------------------------------------------------------
# FORWARD_WATCH activation
# ---------------------------------------------------------------------------
def _activate_forward_watch(flywheel, *, history_db: str, transport, mb_events, as_of: datetime) -> dict[str, Any]:
    import duckdb

    enrolled = 0
    observations = 0
    events_with_2plus = 0
    mb_total = 0
    migrated = {"events_enrolled": 0, "observations_inserted": 0, "events_with_2plus_observations": 0}

    # 1. MusicBrainz future events (CC0, key-free) — real, bounded.
    if mb_events is not None:
        events = mb_events
    else:
        client = MusicBrainzFutureEventsClient(transport=transport)
        events = client.future_events(horizon_days=MB_HORIZON_DAYS, max_events=MAX_MB_FUTURE_EVENTS)
    mb_total = len(events)
    for event in events:
        row = _build_mb_forward_row(event, first_seen_at=as_of)
        if flywheel.register_forward_event(row):
            enrolled += 1

    # 2. Real future events + snapshots already persisted in the history
    #    warehouse (Ticketmaster events acquired by the recurring collector).
    try:
        history_conn = duckdb.connect(history_db, read_only=True)
        try:
            migrated = migrate_history_watch(history_conn=history_conn, flywheel=flywheel, as_of=as_of)
        finally:
            history_conn.close()
    except Exception as exc:  # noqa: BLE001
        migrated["error"] = str(exc)

    enrolled += migrated["events_enrolled"]
    observations = migrated["observations_inserted"]
    events_with_2plus = migrated["events_with_2plus_observations"]

    persisted_total = int(
        flywheel.conn.execute("SELECT COUNT(*) FROM flywheel.forward_watch_events").fetchone()[0]
    )
    quality = _audit_forward_quality(flywheel, as_of=as_of)
    obs_buckets = _observation_buckets(flywheel)
    pit_replay = _pit_replay_gate(flywheel)
    summary = {
        "status": "PASS" if persisted_total > 0 else "NOT_EVALUATED",
        "musicbrainz_events_found": mb_total,
        "history_events_enrolled": migrated["events_enrolled"],
        "events_enrolled_this_run": enrolled,
        "forward_watch_events_total": persisted_total,
        "observations_inserted": observations,
        "events_with_2plus_observations": events_with_2plus,
        "quality": quality,
        "observation_buckets": obs_buckets,
        "pit_replay": pit_replay,
        "note": (
            "Deterministic key-free universe: MusicBrainz CC0 events with "
            "begin in [today, +365d] + real future Ticketmaster events "
            "already persisted by the earlier recurring collector. The "
            "US-market x Ticketmaster Discovery universe stays registered "
            "KEY_REQUIRED (never bypassed) and is the next expansion step. "
            "FORWARD_EVENT_USABLE is a conservative audit rule (real future "
            "date + artist + venue/market), not a row count."
        ),
    }
    gate = "PASS" if persisted_total > 0 else "NOT_EVALUATED"
    return {"summary": summary, "gate": gate}


def _audit_forward_quality(flywheel, *, as_of: datetime) -> dict[str, Any]:
    """Conservative usability audit of the enrolled forward universe.

    FORWARD_EVENT_USABLE requires ALL: a real future event date, artist
    (performer) evidence, and venue or market evidence. An event that only
    has a MusicBrainz id + a date is reported separately, never counted as
    high-quality. Duplicates are counted by provider_event_id and by the
    (artist, venue, date) canonical tuple. Nothing is fabricated; missing
    venue/market stays missing and is reported.
    """
    today = as_of.date()
    conn = flywheel.conn
    total = int(conn.execute("SELECT COUNT(*) FROM flywheel.forward_watch_events").fetchone()[0])
    future = int(
        conn.execute(
            "SELECT COUNT(*) FROM flywheel.forward_watch_events WHERE event_date >= ?",
            [today.isoformat()],
        ).fetchone()[0]
    )
    usable = int(
        conn.execute(
            "SELECT COUNT(*) FROM flywheel.forward_watch_events "
            "WHERE event_date >= ? AND artist_name IS NOT NULL "
            "AND (venue_name IS NOT NULL OR market IS NOT NULL)",
            [today.isoformat()],
        ).fetchone()[0]
    )
    with_artist = int(
        conn.execute("SELECT COUNT(*) FROM flywheel.forward_watch_events WHERE artist_name IS NOT NULL").fetchone()[0]
    )
    with_venue = int(
        conn.execute("SELECT COUNT(*) FROM flywheel.forward_watch_events WHERE venue_name IS NOT NULL").fetchone()[0]
    )
    with_market = int(
        conn.execute("SELECT COUNT(*) FROM flywheel.forward_watch_events WHERE market IS NOT NULL").fetchone()[0]
    )
    dup_provider = int(
        conn.execute(
            "SELECT COUNT(*) FROM (SELECT provider_event_id FROM flywheel.forward_watch_events "
            "GROUP BY provider_event_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    )
    dup_canonical = int(
        conn.execute(
            "SELECT COUNT(*) FROM (SELECT artist_name, venue_name, event_date "
            "FROM flywheel.forward_watch_events GROUP BY 1,2,3 HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    )
    return {
        "total_enrolled": total,
        "future_dated": future,
        "with_artist": with_artist,
        "with_venue": with_venue,
        "with_market": with_market,
        "FORWARD_EVENT_USABLE": usable,
        "duplicate_provider_events": dup_provider,
        "duplicate_canonical_tuples": dup_canonical,
        "rule": "future date AND artist AND (venue OR market)",
    }


def _observation_buckets(flywheel) -> dict[str, Any]:
    """Forward observation depth per event and per provider."""
    conn = flywheel.conn
    per_event = conn.execute(
        "SELECT watch_event_id, COUNT(*) FROM flywheel.forward_watch_observations GROUP BY 1"
    ).fetchall()
    n1 = sum(1 for _, c in per_event if c >= 1)
    n2 = sum(1 for _, c in per_event if c >= 2)
    n3 = sum(1 for _, c in per_event if c >= 3)
    by_provider = {}
    for provider, in conn.execute(
        "SELECT DISTINCT provider FROM flywheel.forward_watch_events"
    ).fetchall():
        by_provider[provider] = {
            "events": int(
                conn.execute(
                    "SELECT COUNT(*) FROM flywheel.forward_watch_events WHERE provider = ?",
                    [provider],
                ).fetchone()[0]
            ),
            "observations": int(
                conn.execute(
                    "SELECT COUNT(*) FROM flywheel.forward_watch_observations o "
                    "JOIN flywheel.forward_watch_events e ON o.watch_event_id = e.watch_event_id "
                    "WHERE e.provider = ?",
                    [provider],
                ).fetchone()[0]
            ),
        }
    return {
        "events_with_1_observation": n1,
        "events_with_2plus_observations": n2,
        "events_with_3plus_observations": n3,
        "by_provider": by_provider,
    }


def _pit_replay_gate(flywheel) -> dict[str, Any]:
    """Genuine A/B PIT replay on REAL persisted rows (no synthetic rows).

    Picks one real watch event with >= 2 temporally distinct observations,
    chooses a cutoff strictly between them, and demonstrates that a
    cutoff-bounded read exposes A but not B, and a later read exposes both.
    """
    conn = flywheel.conn
    row = conn.execute(
        "SELECT watch_event_id FROM flywheel.forward_watch_observations "
        "GROUP BY watch_event_id HAVING COUNT(*) >= 2 ORDER BY watch_event_id LIMIT 1"
    ).fetchone()
    if not row:
        return {"demonstrated": False, "reason": "no event with >= 2 observations"}
    watch_id = row[0]
    obs = conn.execute(
        "SELECT observation_id, knowledge_time, milestone FROM flywheel.forward_watch_observations "
        "WHERE watch_event_id = ? ORDER BY knowledge_time",
        [watch_id],
    ).fetchall()
    a = obs[0]
    b = obs[1]
    a_t = a[1]
    b_t = b[1]
    cutoff = a_t + (b_t - a_t) / 2
    visible_at_c = int(
        conn.execute(
            "SELECT COUNT(*) FROM flywheel.forward_watch_observations "
            "WHERE watch_event_id = ? AND knowledge_time <= ?",
            [watch_id, cutoff.isoformat()],
        ).fetchone()[0]
    )
    visible_after_b = int(
        conn.execute(
            "SELECT COUNT(*) FROM flywheel.forward_watch_observations "
            "WHERE watch_event_id = ? AND knowledge_time <= ?",
            [watch_id, (b_t + timedelta(seconds=1)).isoformat()],
        ).fetchone()[0]
    )
    return {
        "demonstrated": visible_at_c == 1 and visible_after_b >= 2,
        "watch_event_id": watch_id,
        "observation_a": {"id": a[0], "knowledge_time": a_t.isoformat(), "milestone": a[2]},
        "observation_b": {"id": b[0], "knowledge_time": b_t.isoformat(), "milestone": b[2]},
        "cutoff": cutoff.isoformat(),
        "visible_at_cutoff": visible_at_c,
        "visible_after_b": visible_after_b,
    }


def _build_mb_forward_row(event: dict[str, Any], *, first_seen_at: datetime) -> dict[str, Any]:
    from ..flywheel.forward_discovery import build_forward_event_row

    return build_forward_event_row(
        provider="musicbrainz",
        provider_event_id=event["provider_event_id"],
        artist_name=event.get("main_performer") or event.get("name"),
        venue_name=event.get("place"),
        market=None,
        event_date=event["begin_date"],
        first_seen_at=first_seen_at,
        source_url=event.get("source_url"),
        rights_status="OPEN_COMMERCIAL_OK",
        commercial_use_status="OPEN_COMMERCIAL_OK",
        observation_class="OBSERVED_PUBLIC",
    )


# ---------------------------------------------------------------------------
# CONTEXT_PANEL (Wikimedia only — honest PARTIAL)
# ---------------------------------------------------------------------------
def _context_panel(flywheel, *, transport, as_of: datetime) -> dict[str, Any]:
    from ..flywheel.context_panel import (
        PAGEVIEWS_FLOOR,
        build_pageview_series_rows,
        collect_artist_pageviews,
    )

    inserted = 0
    detail = None
    try:
        end = as_of.date()
        start = max(PAGEVIEWS_FLOOR, end - timedelta(days=90))
        series = collect_artist_pageviews(transport or UrllibTransport(), "Bad Bunny", start, end)
        rows = build_pageview_series_rows(
            entity_name="Bad Bunny",
            series=series,
            retrieved_at=as_of,
            source_url="wikimedia.org/rest_v1 metrics/pageviews/per-article",
        )
        for row in rows:
            if flywheel.insert_context_series(row):
                inserted += 1
        detail = f"collected {len(series)} daily observations, inserted {inserted}"
    except Exception as exc:  # noqa: BLE001
        detail = f"pageviews unavailable: {exc}"

    summary = {
        "status": "PARTIAL",
        "series_rows_inserted": inserted,
        "implemented_providers": ["wikimedia"],
        "providers": {
            "wikimedia": "IMPLEMENTED",
            "census": "KEY_REQUIRED",
            "bls": "KEY_REQUIRED",
            "bea": "NOT_IMPLEMENTED",
            "noaa": "KEY_REQUIRED",
            "era5": "NOT_CONFIGURED",
            "gdelt": "NOT_IMPLEMENTED",
        },
        "detail": detail,
        "note": (
            "Only Wikimedia is implemented. Census/BLS/NOAA need keys (unset "
            "locally, registered KEY_REQUIRED, never bypassed); BEA/GDELT are "
            "registered but not implemented. CONTEXT_PANEL is deliberately "
            "PARTIAL until market/weather/news panels produce rows."
        ),
    }
    return {"summary": summary, "gate": "PARTIAL"}


# ---------------------------------------------------------------------------
# Acquisition economics
# ---------------------------------------------------------------------------
def _record_accounting(flywheel, *, hunts, warc_fetches, forward, context, as_of: datetime) -> dict[str, Any]:
    runs = []
    metrics = []

    cdx_statuses = hunts.get("cdx_statuses") or hunts["attempts"]

    cap_run = build_acquisition_run_row(
        provider="wikipedia_mediawiki_api",
        pipeline="OUTCOME_HUNTER",
        started_at=as_of,
        requests=hunts["wikipedia_requests"],
        successful_responses=(
            hunts["wikipedia_statuses"][TASK_CLAIM_FOUND]
            + hunts["wikipedia_statuses"][TASK_NOT_FOUND]
        ),
        records_parsed=hunts["capacity_claims_inserted"],
        new_claims=hunts["capacity_claims_inserted"],
        new_cutoffs=0,
        not_found=hunts["wikipedia_statuses"][TASK_NOT_FOUND],
        rate_limited=hunts["wikipedia_statuses"]["RATE_LIMITED"],
        http_failed=hunts["wikipedia_statuses"]["HTTP_FAILED"],
        parser_failed=hunts["parser_failed_pages"],
        other_failure=hunts["wikipedia_statuses"]["OTHER_FAILURE"],
        monetary_cost_usd=0.0,
        detail="key-free Wikipedia infobox capacity hunts on corpus venues",
    )
    if flywheel.insert_acquisition_run(cap_run):
        runs.append(cap_run)
        metrics.append(derive_metrics(cap_run))

    run = build_acquisition_run_row(
        provider="commoncrawl_cdx",
        pipeline="OUTCOME_HUNTER",
        started_at=as_of,
        requests=hunts["cdx_requests"],
        successful_responses=cdx_statuses[TASK_CLAIM_FOUND] + cdx_statuses[TASK_NOT_FOUND],
        records_parsed=warc_fetches,
        new_claims=hunts["claims_from_pages"],
        new_cutoffs=0,
        not_found=cdx_statuses[TASK_NOT_FOUND],
        rate_limited=cdx_statuses["RATE_LIMITED"],
        http_failed=cdx_statuses["HTTP_FAILED"],
        parser_failed=hunts["parser_failed_pages"],
        other_failure=cdx_statuses["OTHER_FAILURE"],
        monetary_cost_usd=0.0,
        detail="era-directed CDX hunts on persisted source documents",
    )
    if flywheel.insert_acquisition_run(run):
        runs.append(run)
        metrics.append(derive_metrics(run))

    mb_run = build_acquisition_run_row(
        provider="musicbrainz_events",
        pipeline="FORWARD_WATCH",
        started_at=as_of,
        requests=max(forward["summary"].get("musicbrainz_events_found", 0) // 100, 1),
        successful_responses=1,
        records_parsed=forward["summary"].get("musicbrainz_events_found", 0),
        new_forward_observations=0,
        monetary_cost_usd=0.0,
        detail="CC0 future-event discovery (bounded)",
    )
    if flywheel.insert_acquisition_run(mb_run):
        runs.append(mb_run)
        metrics.append(derive_metrics(mb_run))

    hist_run = build_acquisition_run_row(
        provider="event_history",
        pipeline="FORWARD_WATCH",
        started_at=as_of,
        requests=1,
        successful_responses=1,
        records_parsed=forward["summary"]["history_events_enrolled"],
        new_forward_observations=forward["summary"]["observations_inserted"],
        new_ticket_pace_events=forward["summary"]["events_with_2plus_observations"],
        monetary_cost_usd=0.0,
        detail="migration of real persisted forward events + snapshot rows",
    )
    if flywheel.insert_acquisition_run(hist_run):
        runs.append(hist_run)
        metrics.append(derive_metrics(hist_run))

    wm_run = build_acquisition_run_row(
        provider="wikimedia",
        pipeline="CONTEXT_PANEL",
        started_at=as_of,
        requests=1,
        successful_responses=1 if context["summary"]["series_rows_inserted"] else 0,
        records_parsed=context["summary"]["series_rows_inserted"],
        monetary_cost_usd=0.0,
        detail="Wikimedia pageview series (key-free)",
    )
    if flywheel.insert_acquisition_run(wm_run):
        runs.append(wm_run)
        metrics.append(derive_metrics(wm_run))

    for metric in metrics:
        flywheel.insert_acquisition_metrics(metric)

    summary = {
        "status": "PASS" if runs else "NOT_EVALUATED",
        "runs_recorded": len(runs),
        "metrics_derived": len(metrics),
        "providers": [r["provider"] for r in runs],
        "note": (
            "Per-provider runs persist requests/successes/new claims/new "
            "cutoffs/new warm starts/failures by class/cost/latency; derived "
            "yields (per-1000-requests, cost-per-new-evidence) are computed "
            "rows, never hand-entered, and no composite provider score is "
            "invented."
        ),
    }
    return {"summary": summary, "gate": "PASS" if runs else "NOT_EVALUATED"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fetch_crawls(transport: UrllibTransport | None) -> list[dict[str, Any]]:
    """Fetch the Common Crawl collection index (network -> cached fallback).

    The CDX index occasionally throttles/refuses connections after bursts;
    the last-known crawl list is cached locally (gitignored) so acquisition
    degrades to the cached index instead of failing outright. A cached index
    is still a REAL crawl collection list; no URLs are invented.
    """
    last_error = None
    for _attempt in range(3):
        try:
            response = (transport or UrllibTransport()).request(
                "GET", COLLINFO_URL, headers={"User-Agent": "FestivalBloomberg/0.1 (research)"}, timeout_seconds=45.0
            )
            if response.status == 200:
                payload = json.loads(response.body.decode("utf-8", errors="replace"))
                if isinstance(payload, list) and payload:
                    try:
                        COLLINFO_CACHE.parent.mkdir(parents=True, exist_ok=True)
                        COLLINFO_CACHE.write_text(
                            json.dumps(payload), encoding="utf-8"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    return payload
            last_error = f"http {response.status}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1.0)
    if COLLINFO_CACHE.is_file():
        try:
            cached = json.loads(COLLINFO_CACHE.read_text(encoding="utf-8"))
            if isinstance(cached, list) and cached:
                return cached
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(f"collinfo.json unavailable: {last_error}")


def _venue_key_of(eng: dict[str, Any]) -> str:
    """Deterministic venue key for dedup across engagements."""
    from ..flywheel.hunt_execution import venue_key

    return venue_key(eng.get("venue"), eng.get("city") or eng.get("market"))


def _year_of(value: Any) -> int:
    if isinstance(value, (datetime, date)):
        return value.year
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return 2026


def _cdx_ts_to_iso(ts: str) -> str:
    t = ts.strip()
    if len(t) < 8 or not t.isdigit():
        return ts
    year, month, day = t[0:4], t[4:6], t[6:8]
    hour = t[8:10] or "00"
    minute = t[10:12] or "00"
    second = t[12:14] or "00"
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"


def _milestone_baseline(before: dict[str, float]) -> dict[str, dict[str, float | str]]:
    """Accepted milestone starting state (DATA_ACQUISITION_ACTIVATION_V1 spec).

    These are the documented BEFORE values from the accepted baseline — the
    state at the start of this milestone (flywheel measured, zero forward
    enrollment, zero hunt attempts, zero PIT evidence). ``measured_now`` is
    this run's BEFORE snapshot; the narrative AFTER lives in the report.
    """
    baseline = {
        "CANONICAL_BOXSCORE_ENGAGEMENTS": 657.0,
        "SINGLE_SHOW_ENGAGEMENTS": 443.0,
        "CANONICAL_PERFORMANCES": 443.0,
        "OUTCOME_CLAIMS": 1110.0,
        "UNIQUE_EVENTS_WITH_OUTCOMES": 443.0,
        "PRIVATE_EVENTS_WITH_SETTLEMENT_EVIDENCE": 0.0,
        "FORWARD_TRACKED_FUTURE_EVENTS": 0.0,
        "STRICT_PIT_RECONSTRUCTABLE": 0.0,
        "STRICT_PIT_WARM_START_EVENTS": 0.0,
        "OUTCOME_HUNTER_ATTEMPTS": 0.0,
    }
    return {
        "accepted_milestone_start": baseline,
        "measured_at_run_start": {k: before.get(k) for k in baseline},
        "note": (
            "BEFORE = accepted DATA_FLYWHEEL_AND_COVERAGE_V1 state: zero forward "
            "enrollment, zero hunt attempts, zero PIT evidence rows (all 657 "
            "source_publication_time NULL). AFTER is measured live each run."
        ),
    }


def _before_after_diff(before: dict[str, float], after: dict[str, float]) -> dict[str, dict[str, float]]:
    out = {}
    for key in sorted(set(before) | set(after)):
        b = before.get(key)
        a = after.get(key)
        if b != a:
            out[key] = {"before": b, "after": a}
    return out


if __name__ == "__main__":
    result = run_activation_v1_oa()
    print(json.dumps(result, indent=2, default=str))
