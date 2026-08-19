"""Backtest the transparent comparable engine against the hierarchical champion.

Loads the frozen corpus (reports/baseline_research_v1/corpus_v1_manifest.json),
runs the distance-based comparable engine and the existing hierarchical
fallback under every leakage-safe hold, and reports MAE / WAPE per target ×
split. The champion stays the champion until the engine consistently beats it.

Run:  PYTHONPATH=python .venv/bin/python scripts/comparable_backtest.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from festival_bloomberg.research.baselines import (
    hierarchical_fallback,
    naive_predict,
    regression_metrics,
)
from festival_bloomberg.research.comparable import (
    point_in_time_candidates,
    retrieve_comparables,
)
from festival_bloomberg.research.features import (
    TARGET_ATTENDANCE,
    TARGET_GROSS,
    TARGET_PAID_TICKETS,
    compute_features,
    population,
)
from festival_bloomberg.research.experiment import _global_baseline, _split_folds

MANIFEST = Path("reports/baseline_research_v1/corpus_v1_manifest.json")
SPLITS = ("TIME", "ARTIST_GROUP", "VENUE_GROUP", "MARKET_GROUP", "TOUR_GROUP")


def _value_fn(target_type: str):
    if target_type == TARGET_GROSS:
        return lambda r: r.get("ticket_gross_total")
    return lambda r: r.get("headcount_total")  # REPORTED_ATTENDANCE / PAID_TICKETS


def main() -> None:
    manifest = json.loads(MANIFEST.read_text())
    rows = manifest["rows"]
    print(f"corpus: {len(rows)} rows, checksum {manifest['checksum'][:16]}...\n")

    report: dict[str, dict] = {}
    for target in (TARGET_ATTENDANCE, TARGET_GROSS, TARGET_PAID_TICKETS):
        eligible, _ = population(rows, target)
        value_fn = _value_fn(target)
        report[target] = {}
        for split in SPLITS:
            train_rows, test_rows = _split_folds(eligible, split)
            if not train_rows or not test_rows:
                report[target][split] = {"note": "no test fold"}
                continue

            # Hierarchical champion + global median (same harness as baseline).
            train_feats = compute_features(train_rows, target, history_pool=train_rows)
            test_feats = compute_features(test_rows, target, history_pool=train_rows)
            global_median = _global_baseline(train_feats, target)

            y_true = np.asarray([it["target"] for it in test_feats], dtype=float)
            hier = np.asarray(
                [hierarchical_fallback(it["features"], global_median) or global_median
                 for it in test_feats], dtype=float)

            # Comparable engine: top-K weighted-median over PIT train candidates.
            comp_preds = []
            for t in test_rows:
                cands = point_in_time_candidates(t, train_rows, target_engagement_id=t.get("engagement_id"))
                res = retrieve_comparables(t, cands, value_fn=value_fn, k=10)
                v = res["valuation"]["weighted_median"] if res["valuation"] else None
                comp_preds.append(v if v is not None else (global_median or 0.0))
            comp_preds = np.asarray(comp_preds, dtype=float)

            hier_m = regression_metrics(y_true, hier)
            comp_m = regression_metrics(y_true, comp_preds)
            report[target][split] = {
                "n_test": int(len(test_rows)),
                "hierarchical": {"mae": hier_m["mae"], "wape": hier_m["wape"]},
                "comparable": {"mae": comp_m["mae"], "wape": comp_m["wape"]},
            }
            delta = comp_m["mae"] - hier_m["mae"]
            winner = "COMP" if delta < 0 else ("TIE" if delta == 0 else "HIER")
            print(f"{target:20s} {split:14s} n={len(test_rows):3d}  "
                  f"hier_mae={hier_m['mae']:9.0f}  comp_mae={comp_m['mae']:9.0f}  "
                  f"delta={delta:+9.0f}  {winner}")

    Path("reports/comparable_engine_v1_backtest.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\nwrote reports/comparable_engine_v1_backtest.json")


if __name__ == "__main__":
    main()
