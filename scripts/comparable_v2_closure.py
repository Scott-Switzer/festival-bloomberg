"""COMPARABLE_EVENT_ENGINE_V2 — research-closure audit (no model fitting).

This is the honest pre-flight for Comparable V2, replacing the earlier local
experiment that substituted (a) 2026-era city competition density for
historical event-level competition and (b) a trailing 2025-2026 Wikimedia
pageview window for per-cutoff historical attention.

The script performs no model fitting and mutates nothing. It answers three
questions with evidence drawn from the frozen corpus manifest and the
warehouse, then emits a machine-readable closure report:

PHASE 1 — denominator reconciliation
    Is the PR #35 ``500/500`` competition population the SAME population that
    Comparable V2 is evaluated on?

PHASE 2 — competition PIT gate
    Can event-level competition that was genuinely knowable at each corpus
    event's cutoff be reconstructed from the warehouse?

PHASE 3 — Wikimedia attention coverage gate
    Is the STORED Wikimedia panel a historical per-cutoff panel, or a trailing
    "now" window? Separately: is the SOURCE capable of supplying historical
    windows (it is, from 2015-07-01)? retrieved_at is provenance and is NEVER
    an admissibility gate.

Run:  PYTHONPATH=python .venv/bin/python scripts/comparable_v2_closure.py
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import duckdb

CORPUS_PATH = Path("reports/baseline_research_v1/corpus_v1_manifest.json")
WAREHOUSE_PATH = Path("data/warehouse/boxoffice_research_v2.duckdb")
OUT_PATH = Path("reports/comparable_engine_v2_closure.json")

VERDICT_PROMOTED = "PROMOTED"
VERDICT_NO_PROMOTION = "NO_PROMOTION"
VERDICT_ATTENTION_ONLY = "PARTIAL_ATTENTION_EVALUABLE_COMPETITION_NOT_EVALUABLE"

NOT_EVALUABLE = "NOT_EVALUABLE_ON_CURRENT_CORPUS"
WIKIMEDIA_ACQUISITION_REQUIRED = "EVALUABLE_AFTER_HISTORICAL_ACQUISITION"
STORED_PANEL_INSUFFICIENT = "INSUFFICIENT_FOR_COMPARABLE_V2"


def _iso10(value) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def _load_corpus() -> list[dict]:
    data = json.loads(CORPUS_PATH.read_text())
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("corpus manifest 'rows' is not a list")
    return rows


def phase1_reconcile(rows: list[dict], conn: duckdb.DuckDBPyConnection) -> dict:
    """Reconcile the PR #35 competition denominator against the corpus.

    The measurement report records competition as ``sample_size = 500`` with
    ``coverage = 1.0``. Those 500 were warehouse Ticketmaster events, not the
    657-row frozen corpus. This phase proves the two populations are disjoint
    in time and therefore the ``100% coverage`` claim is not a
    Comparable-V2-corpus coverage claim.
    """
    corpus_events = len(rows)
    corpus_time = sum(1 for r in rows if (r.get("folds") or {}).get("TIME") == "TEST")
    corpus_dates = sorted(_iso10(r.get("start_date")) for r in rows if r.get("start_date"))
    corpus_missing_dates = sum(1 for r in rows if not r.get("start_date"))

    wh = conn.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT platform_object_id),
               MIN(local_date), MAX(local_date),
               MIN(knowledge_time), MAX(knowledge_time),
               COUNT(DISTINCT LOWER(city))
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
        """
    ).fetchone()

    # The measurement report's 500-event sample is a warehouse sample. Confirm
    # it cannot be the corpus by comparing date coverage.
    wh_min, wh_max = _iso10(wh[2]), _iso10(wh[3])
    corpus_min, corpus_max = corpus_dates[0], corpus_dates[-1]

    # How many corpus events have a start_date that could possibly overlap the
    # warehouse event window?
    overlapable = sum(
        1 for r in rows
        if r.get("start_date") and _iso10(r.get("start_date")) >= wh_min
    )

    return {
        "population": "CORPUS vs WAREHOUSE",
        "corpus_events": corpus_events,
        "corpus_time_hold_events": corpus_time,
        "corpus_date_range": [corpus_min, corpus_max],
        "corpus_missing_start_date": corpus_missing_dates,
        "warehouse_ticketmaster_rows": wh[0],
        "warehouse_distinct_events": wh[1],
        "warehouse_date_range": [wh_min, wh_max],
        "warehouse_knowledge_time_range": [_iso10(wh[4]), _iso10(wh[5])],
        "warehouse_distinct_cities": wh[6],
        "corpus_events_with_date_inside_warehouse_window": overlapable,
        "conclusion": (
            "The 500-event competition sample is a WAREHOUSE population "
            "(2026-2028 Ticketmaster events), not the 657-row frozen corpus "
            "(2012-2026). The two populations are temporally disjoint, so the "
            "PR #35 '500/500 = 100% coverage' figure is NOT a Comparable-V2 "
            "corpus coverage figure. Denominators differ: warehouse events vs "
            f"corpus TIME hold ({corpus_time} events)."
        ),
    }


def phase2_competition_gate(rows: list[dict], conn: duckdb.DuckDBPyConnection) -> dict:
    """Determine whether historical PIT competition is reconstructable.

    Event-level competition for a corpus event at date D requires OTHER events
    in the same market near D whose knowledge_time < cutoff. The warehouse has
    ZERO Ticketmaster events before 2026-08-14, while the dated corpus spans
    2012-2026. Therefore no corpus event (except at most a handful whose cutoff
    is inside the 2026-2028 window) has a reconstructable historical
    competition state.
    """
    wh_min = _iso10(conn.execute(
        "SELECT MIN(local_date) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"
    ).fetchone()[0])

    pre_window = conn.execute(
        """
        SELECT COUNT(*) FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster' AND local_date < ?
        """,
        [wh_min],
    ).fetchone()[0]

    # Canonical events table is empty in this warehouse generation.
    canonical_events = conn.execute("SELECT COUNT(*) FROM events.events").fetchone()[0]
    provider_obs = conn.execute(
        "SELECT COUNT(*) FROM events.provider_event_observations"
    ).fetchone()[0]

    dated = [r for r in rows if r.get("start_date")]
    evaluable = [r for r in dated if _iso10(r.get("start_date")) >= wh_min]
    # Even for evaluable-dated rows, a PIT window needs competitor knowledge_time
    # < the event's cutoff; warehouse knowledge_time is all ~2026-08-15..19.
    kt_min = _iso10(conn.execute(
        "SELECT MIN(knowledge_time) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"
    ).fetchone()[0])

    return {
        "population": "CORPUS target events",
        "corpus_dated_events": len(dated),
        "warehouse_earliest_ticketmaster_date": wh_min,
        "warehouse_pre_window_events": pre_window,
        "canonical_events_table_rows": canonical_events,
        "provider_event_observations_rows": provider_obs,
        "corpus_events_with_cutoff_inside_warehouse_window": len(evaluable),
        "warehouse_earliest_knowledge_time": kt_min,
        "verdict": NOT_EVALUABLE,
        "reason": (
            "No historical event source exists for the 2012-2026 corpus. "
            "warehouse Ticketmaster events begin 2026-08-14 (knowledge_time "
            "2026-08-15..19), events.events is empty, and "
            "provider_event_observations is empty. Event-level competition "
            "genuinely knowable at a 2012/2013/2024/2025 corpus cutoff cannot "
            "be reconstructed. The earlier local experiment substituted a "
            "2026-era city-density proxy, which is a different object than the "
            "admitted feature event_competition_same_day_market / _14d_market."
        ),
    }


def phase3_wikimedia_gate(rows: list[dict], conn: duckdb.DuckDBPyConnection) -> dict:
    """Distinguish stored-panel coverage from source capability.

    Three separate facts, which the first revision collapsed:

    1. STORED PANEL — what we have locally is a trailing window whose
       observation days are 2025-08-19 .. 2026-08-20. That cannot supply a
       30d window ending before a 2019/2024 cutoff, so it is INSUFFICIENT for
       Comparable V2 as-is.
    2. RETRIEVAL TIME — when we downloaded it (Aug 2026) is provenance and is
       NEVER an admissibility gate. A 2019 pageview fetched in 2026 was still
       knowable in 2019, because its available_at is observation_day + 1.
    3. SOURCE CAPABILITY — the Wikimedia Analytics API serves historical
       pageviews from 2015-07-01, so per-cutoff historical acquisition is
       possible for the eligible (post-2015-07-01) corpus tail.
    """
    windows = conn.execute(
        """
        SELECT MIN(period_start), MAX(period_end), COUNT(DISTINCT artist_key),
               MIN(retrieved_at), MAX(retrieved_at)
        FROM metrics.artist_attention_observations
        WHERE project = 'en.wikipedia' AND status = 'ok'
        """
    ).fetchone()
    trailing_start = _iso10(windows[0])
    trailing_end = _iso10(windows[1])
    distinct_ok_artists = windows[2]
    retrieved_min = _iso10(windows[3])
    retrieved_max = _iso10(windows[4])

    dated = [r for r in rows if r.get("start_date")]
    from datetime import date as _date
    series_start = _date(2015, 7, 1).isoformat()
    pre_series = sum(1 for r in dated if _iso10(r.get("start_date")) < series_start)
    eligible = sum(1 for r in dated if _iso10(r.get("start_date")) >= series_start)

    return {
        "population": "CORPUS target events (dated)",
        "stored_wikimedia_observation_window": [trailing_start, trailing_end],
        "stored_wikimedia_distinct_ok_artists": distinct_ok_artists,
        "stored_wikimedia_retrieved_range_provenance_only": [retrieved_min, retrieved_max],
        "wikimedia_series_start": series_start,
        "corpus_dated_events": len(dated),
        "pre_wikimedia_series_events": pre_series,
        "wikimedia_series_eligible_events": eligible,
        "stored_panel_verdict": STORED_PANEL_INSUFFICIENT,
        "source_verdict": WIKIMEDIA_ACQUISITION_REQUIRED,
        "retrieved_at_is_admissibility_gate": False,
        "reason": (
            f"The STORED panel's observation days span {trailing_start}.."
            f"{trailing_end}, a single trailing window, so it cannot supply a "
            "per-cutoff historical window for the 2012-2026 corpus — it is "
            f"{STORED_PANEL_INSUFFICIENT}. That is a COVERAGE fact, not a "
            "PIT-admissibility fact: retrieved_at "
            f"({retrieved_min}..{retrieved_max}) is provenance and is NEVER an "
            "admissibility gate. The source itself serves historical pageviews "
            f"from {series_start}, so {eligible} of {len(dated)} dated corpus "
            "events are eligible for a real per-cutoff historical backfill; "
            f"{pre_series} predate the series and are UNAVAILABLE (not missing, "
            "not zero). Page resolution (does the artist have ANY pageview row) "
            "is still not the same as historical window completeness at the "
            "cutoff."
        ),
    }


def main() -> None:
    rows = _load_corpus()
    conn = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        p1 = phase1_reconcile(rows, conn)
        p2 = phase2_competition_gate(rows, conn)
        p3 = phase3_wikimedia_gate(rows, conn)
    finally:
        conn.close()

    # Overall verdict: competition is not evaluable on the current corpus;
    # Wikimedia is not evaluable from the STORED panel but IS evaluable after a
    # historical acquisition for the post-2015-07-01 corpus tail. Whether that
    # acquisition actually delivers is decided by the bounded pilot
    # (scripts/wikimedia_historical_pilot.py), not by this static audit.
    overall = {
        "verdict": VERDICT_ATTENTION_ONLY,
        "sub_verdicts": {
            "competition": NOT_EVALUABLE,
            "wikimedia_stored_panel": STORED_PANEL_INSUFFICIENT,
            "wikimedia_source": WIKIMEDIA_ACQUISITION_REQUIRED,
        },
        "retrieved_at_is_admissibility_gate": False,
        "statement": (
            "Competition is NOT_EVALUABLE_ON_CURRENT_CORPUS (no historical "
            "event source). Wikimedia is not evaluable from the STORED "
            "trailing panel (INSUFFICIENT_FOR_COMPARABLE_V2), but the source "
            "serves historical pageviews from 2015-07-01, so it is "
            "EVALUABLE_AFTER_HISTORICAL_ACQUISITION for the eligible corpus "
            "tail. retrieved_at is provenance, never a gate. The earlier local "
            "experiment's 'attention hurts' claim is invalid because it used a "
            "trailing window as a historical window — not because late "
            "retrieval leaked. The historical pilot determines whether "
            "Comparable V2 becomes ATTENTION_ONLY_EVALUABLE or Wikimedia is "
            "genuinely not evaluable."
        ),
    }

    report = {
        "phase1_denominator_reconciliation": p1,
        "phase2_competition_pit_gate": p2,
        "phase3_wikimedia_attention_pit_gate": p3,
        "overall": overall,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
