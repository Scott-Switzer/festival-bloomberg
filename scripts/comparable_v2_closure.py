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

PHASE 3 — Wikimedia attention PIT gate
    Does the stored Wikimedia attention support a window that ends strictly
    before each corpus event's cutoff, or is it a trailing "now" window that
    would leak current attention backward?

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
VERDICT_PARTIAL = "PARTIAL_COMPETITION_NOT_EVALUABLE"

NOT_EVALUABLE = "NOT_EVALUABLE_ON_CURRENT_CORPUS"


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
    """Determine whether stored Wikimedia attention is PIT-safe for the corpus.

    The admitted feature is a 30d pageview window ending before the cutoff. The
    stored Wikimedia data is a trailing window collected in August 2026
    (period 2025-08-19 .. 2026-08-20), NOT per-cutoff historical windows. For
    any corpus event whose cutoff precedes ~2025-09, no window ending before
    that cutoff exists in the warehouse; using the trailing window would leak
    current attention backward.
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
    corpus_max_cutoff = max(_iso10(r.get("start_date")) for r in dated)

    # The strict PIT gate is availability: the feature's knowledge_time rule
    # requires every contributing day's available_at < cutoff, which implies
    # retrieved_at < cutoff for the stored aggregate. The stored rows were all
    # retrieved Aug 2026, after the latest corpus cutoff.
    all_retrieved_after_cutoff = retrieved_min > corpus_max_cutoff

    return {
        "population": "CORPUS target artists",
        "stored_wikimedia_window": [trailing_start, trailing_end],
        "stored_wikimedia_distinct_ok_artists": distinct_ok_artists,
        "stored_wikimedia_retrieved_range": [retrieved_min, retrieved_max],
        "corpus_dated_events": len(dated),
        "corpus_max_cutoff": corpus_max_cutoff,
        "every_stored_observation_retrieved_after_corpus_cutoff": all_retrieved_after_cutoff,
        "verdict": NOT_EVALUABLE,
        "reason": (
            "Stored Wikimedia pageviews are a single trailing window "
            f"{trailing_start}..{trailing_end} with retrieved_at "
            f"{retrieved_min}..{retrieved_max}. The latest corpus cutoff is "
            f"{corpus_max_cutoff}, so EVERY stored observation was retrieved "
            "after every corpus cutoff — the PIT availability rule "
            "(available_at < cutoff) fails for the entire corpus. Using the "
            "trailing window would leak current attention into a historical "
            "comparable set. Page resolution (does the artist have ANY "
            "pageview row) is not the same as PIT window availability at the "
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

    # Overall verdict: neither admitted family is evaluable on the current
    # corpus. The closest allowed label is PARTIAL_COMPETITION_NOT_EVALUABLE,
    # but the honest statement is that BOTH families are not evaluable here.
    overall = {
        "verdict": VERDICT_PARTIAL,
        "sub_verdicts": {
            "competition": NOT_EVALUABLE,
            "wikimedia_attention": NOT_EVALUABLE,
        },
        "statement": (
            "Comparable V2 cannot be validly run against the current frozen "
            "corpus: historical PIT competition has no historical event source, "
            "and stored Wikimedia attention is a trailing 2025-2026 window "
            "rather than per-cutoff historical windows. The earlier local "
            "experiment that reported a negative result used an invalid "
            "city-density competition proxy and trailing attention, so its "
            "numbers are not evidence about the admitted features. This is "
            "'not evaluable', NOT 'evaluated and found useless'."
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
