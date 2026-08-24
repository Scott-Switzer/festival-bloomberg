"""Bounded real historical Wikimedia attention pilot (PIT-correct).

Answers: can a per-cutoff 30d pageview window be retrieved for the frozen
corpus's post-2015-07-01 events, with defensible source-availability semantics?
This is a REAL acquisition pilot, not the full backfill, and it mutates nothing
in the warehouse — it fetches and reports only.

PIT semantics (mirrors ``attention.historical_pit``):

* ``observation_time`` — the pageview day (from the API's per-day timestamp);
* ``available_at``    — ``observation_day + 1`` (Wikimedia loads a day's
                        aggregate at the end of that day; 00:00 UTC the next
                        day). This is the source-availability bound.
* ``retrieved_at``    — NOW, when we fetched it. Provenance only; it is NEVER
                        an admissibility gate.

A day is PIT-admissible for a cutoff T iff ``observation_day < T`` AND
``available_at < T``. Days before 2015-07-01 are UNAVAILABLE (the series did
not exist), never missing and never zero.

The pilot draws a deterministic stratified sample from the eligible corpus
(start_date >= 2015-07-01), preferring TIME-hold rows, capped at a bounded
number of events.

Run:  PYTHONPATH=python .venv/bin/python scripts/wikimedia_historical_pilot.py
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

from festival_bloomberg.acquisition.transport import UrllibTransport
from festival_bloomberg.attention.wikimedia_pageviews import (
    WIKIMEDIA_SERIES_START,
    fetch_pageviews,
    wikimedia_available_at,
)

CORPUS_PATH = Path("reports/baseline_research_v1/corpus_v1_manifest.json")
OUT_PATH = Path("reports/wikimedia_historical_pilot.json")

MAX_EVENTS = 30
WINDOW_DAYS = 30
MIN_INTERVAL_SECONDS = 0.5


def _load_corpus() -> list[dict]:
    data = json.loads(CORPUS_PATH.read_text())
    return data["rows"]


def _iso10(value) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def _eligible_sample(rows: list[dict], max_events: int) -> list[dict]:
    """Deterministic stratified sample: TIME-hold first, then by year, bounded.

    Only events with a start_date on/after the Wikimedia series start are
    eligible (the source did not exist before then).
    """
    series = WIKIMEDIA_SERIES_START.isoformat()
    eligible = [
        r for r in rows
        if r.get("start_date") and _iso10(r.get("start_date")) >= series
    ]
    eligible.sort(key=lambda r: (_iso10(r.get("start_date")) or "", r.get("artist") or ""))

    # Prefer TIME-hold rows, then fill with the rest, keeping year spread.
    time_rows = [r for r in eligible if (r.get("folds") or {}).get("TIME") == "TEST"]
    other_rows = [r for r in eligible if (r.get("folds") or {}).get("TIME") != "TEST"]
    chosen: list[dict] = []
    seen_artists: set[str] = set()
    for pool in (time_rows, other_rows):
        for r in pool:
            if len(chosen) >= max_events:
                break
            artist = (r.get("artist") or "").strip().lower()
            # cap repeats per artist so one artist can't dominate the pilot
            if artist and sum(1 for c in chosen if (c.get("artist") or "").strip().lower() == artist) >= 2:
                continue
            chosen.append(r)
        if len(chosen) >= max_events:
            break
    return chosen


def _window_for(cutoff_iso: str, days: int) -> tuple[str, str]:
    """A (start, end) pageview window ending strictly before the cutoff.

    Because a day d is admissible only if d+1 < cutoff, the latest admissible
    observation day is cutoff-2. We request [cutoff-days-1, cutoff-1] so the
    admissible days within it span a full ``days``-day window.
    """
    cutoff = date.fromisoformat(cutoff_iso)
    end = cutoff - timedelta(days=1)
    start = end - timedelta(days=days)  # days+1 calendar days inclusive
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _day_of(item: dict) -> date | None:
    ts = str(item.get("timestamp") or "")
    if len(ts) < 8:
        return None
    return date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))


def _pit_admissible_days(items: list[dict], cutoff_iso: str) -> list[tuple[date, int]]:
    """Return (day, views) pairs that are PIT-admissible for the cutoff."""
    cutoff = date.fromisoformat(cutoff_iso)
    out = []
    for item in items:
        d = _day_of(item)
        if d is None:
            continue
        if d < WIKIMEDIA_SERIES_START:
            continue  # UNAVAILABLE: source did not exist
        if not (d < cutoff):
            continue  # observation at/after cutoff
        if wikimedia_available_at(d) < cutoff:
            out.append((d, int(item.get("views") or 0)))
    return out


def _admissibility(items: list[dict], cutoff_iso: str) -> dict:
    """Classify each returned day and compute window completeness + PIT count."""
    cutoff = date.fromisoformat(cutoff_iso)
    total_days = 0
    post_cutoff = 0
    pre_series = 0
    zero_days = 0
    for item in items:
        d = _day_of(item)
        if d is None:
            continue
        total_days += 1
        if int(item.get("views") or 0) == 0:
            zero_days += 1
        if d < WIKIMEDIA_SERIES_START:
            pre_series += 1
        elif not (d < cutoff):
            post_cutoff += 1
    admissible = _pit_admissible_days(items, cutoff_iso)
    return {
        "days_returned": total_days,
        "days_pit_admissible": len(admissible),
        "days_post_cutoff": post_cutoff,
        "days_pre_series_unavailable": pre_series,
        "true_zero_days": zero_days,
        "window_completeness_pct": round(len(admissible) / WINDOW_DAYS, 4),
    }


def main() -> None:
    rows = _load_corpus()
    sample = _eligible_sample(rows, MAX_EVENTS)
    transport = UrllibTransport()

    print(f"eligible sample: {len(sample)} events (max {MAX_EVENTS})")

    results = []
    for i, row in enumerate(sample):
        artist = (row.get("artist") or "").strip()
        cutoff = _iso10(row.get("start_date"))
        start, end = _window_for(cutoff, WINDOW_DAYS)

        rec = {
            "artist": artist,
            "event_cutoff": cutoff,
            "engagement_id": row.get("engagement_id"),
            "is_time_hold": (row.get("folds") or {}).get("TIME") == "TEST",
            "window_requested": [start, end],
        }
        if i and MIN_INTERVAL_SECONDS > 0:
            time.sleep(MIN_INTERVAL_SECONDS)

        r = fetch_pageviews(transport, title=artist, start=start, end=end)
        rec["status"] = r["status"]
        rec["retrieved_at"] = r["retrieved_at"]
        if r["status"] == "ok":
            rec["admissibility"] = _admissibility(r["items"], cutoff)
            rec["pit_admissible_30d_sum"] = sum(
                v for _d, v in _pit_admissible_days(r["items"], cutoff)
            )
        else:
            rec["error_code"] = r.get("error_code")
            rec["error_message"] = (r.get("error_message") or "")[:200]

        results.append(rec)
        print(f"  {artist:28s} cutoff={cutoff}  status={r['status']}")

    summary = {
        "targets_attempted": len(results),
        "page_resolved": sum(1 for r in results if r["status"] == "ok"),
        "missing_404": sum(1 for r in results if r["status"] == "missing"),
        "error": sum(1 for r in results if r["status"] == "error"),
        "pit_admissible_30d_complete": sum(
            1 for r in results
            if r.get("admissibility") and r["admissibility"]["days_pit_admissible"] >= WINDOW_DAYS
        ),
        "results": results,
    }
    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
