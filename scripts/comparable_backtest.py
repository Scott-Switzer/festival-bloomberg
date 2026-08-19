"""Backtest the transparent comparable engine against the hierarchical champion.

Loads the frozen corpus (reports/baseline_research_v1/corpus_v1_manifest.json),
runs THREE engines under every leakage-safe hold, and reports MAE / WAPE / MdAE
/ coverage, bootstrap uncertainty on the challenger-vs-champion delta, and
negative controls (shuffled outcomes, K/weight/penalty sensitivity).

Engines:
  A  hierarchical median champion (baselines.hierarchical_fallback)
  B  global soft-distance comparable (retrieve_global)
  C  hierarchical-stratum + soft-distance comparable (retrieve_stratum)

Run:  PYTHONPATH=python .venv/bin/python scripts/comparable_backtest.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from festival_bloomberg.research.baselines import hierarchical_fallback, regression_metrics
from festival_bloomberg.research.comparable import (
    assert_admissibility_contract,
    point_in_time_candidates,
    retrieve_global,
    retrieve_stratum,
    weights_for_target,
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
TARGETS = (TARGET_ATTENDANCE, TARGET_GROSS, TARGET_PAID_TICKETS)


def _value_fn(target_type: str):
    if target_type == TARGET_GROSS:
        return lambda r: r.get("ticket_gross_total")
    return lambda r: r.get("headcount_total")  # REPORTED_ATTENDANCE / PAID_TICKETS


def _engine_preds(test_rows, train_rows, target_type, engine, *, k=10, weights=None,
                  penalty=1.0, min_coverage=0.25, min_stratum=3, global_median=None):
    value_fn = _value_fn(target_type)
    preds = []
    coverages = []
    for t in test_rows:
        cands = point_in_time_candidates(t, train_rows, target_engagement_id=t.get("engagement_id"))
        if engine == "B":
            res = retrieve_global(t, cands, value_fn=value_fn, k=k, weights=weights,
                                  missingness_penalty=penalty, min_coverage=min_coverage)
        else:
            res = retrieve_stratum(t, cands, value_fn=value_fn, k=k, weights=weights,
                                   missingness_penalty=penalty, min_coverage=min_coverage,
                                   min_stratum_size=min_stratum)
        v = res["valuation"]["weighted_median"] if res["valuation"] else None
        preds.append(v if v is not None else (global_median or 0.0))
        coverages.append(res["coverage_score"] if res["comps"] else 0.0)
    return np.asarray(preds, dtype=float), coverages


def _run_hold(rows, target, split):
    eligible, _ = population(rows, target)
    train_rows, test_rows = _split_folds(eligible, split)
    if not train_rows or not test_rows:
        return None
    value_fn = _value_fn(target)
    train_feats = compute_features(train_rows, target, history_pool=train_rows)
    test_feats = compute_features(test_rows, target, history_pool=train_rows)
    global_median = _global_baseline(train_feats, target)
    weights = weights_for_target(target)

    y_true = np.asarray([it["target"] for it in test_feats], dtype=float)
    hier = np.asarray(
        [hierarchical_fallback(it["features"], global_median) or global_median
         for it in test_feats], dtype=float)

    b_preds, _ = _engine_preds(test_rows, train_rows, target, "B",
                               weights=weights, global_median=global_median)
    c_preds, c_cov = _engine_preds(test_rows, train_rows, target, "C",
                                   weights=weights, global_median=global_median)

    out = {
        "n_test": int(len(test_rows)),
        "hierarchical": {"mae": regression_metrics(y_true, hier)["mae"],
                         "wape": regression_metrics(y_true, hier)["wape"],
                         "mdae": regression_metrics(y_true, hier)["mdae"]},
        "global": {"mae": regression_metrics(y_true, b_preds)["mae"],
                   "wape": regression_metrics(y_true, b_preds)["wape"],
                   "mdae": regression_metrics(y_true, b_preds)["mdae"]},
        "stratum": {"mae": regression_metrics(y_true, c_preds)["mae"],
                    "wape": regression_metrics(y_true, c_preds)["wape"],
                    "mdae": regression_metrics(y_true, c_preds)["mdae"]},
        "mean_coverage": float(np.mean(c_cov)) if c_cov else 0.0,
    }
    return out, test_rows, train_rows, target, y_true, hier, c_preds, global_median


def _bootstrap_delta(test_rows, train_rows, target, y_true, hier, c_preds, global_median, *,
                     seed=42, B=200):
    """Cluster-bootstrap (by artist) the MAE delta challenger(C) - champion(A)."""
    groups = np.asarray([r.get("artist") or "unknown" for r in test_rows])
    rng = np.random.default_rng(seed)
    unique = np.unique(groups)
    deltas = []
    for _ in range(B):
        sample_groups = rng.choice(unique, size=len(unique), replace=True)
        keep = np.isin(groups, sample_groups)
        if keep.sum() == 0:
            continue
        ma = regression_metrics(y_true[keep], hier[keep])["mae"]
        mc = regression_metrics(y_true[keep], c_preds[keep])["mae"]
        deltas.append(mc - ma)
    deltas = np.asarray(deltas)
    if deltas.size == 0:
        return {"note": "no bootstrap samples"}
    return {
        "point_delta": float(np.median(deltas)),
        "ci_90": [float(np.percentile(deltas, 5)), float(np.percentile(deltas, 95))],
        "ci_95": [float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5))],
        "p_improve": float(np.mean(deltas < 0)),
        "B": int(deltas.size),
    }


def _negative_controls(rows, target, split):
    """Shuffled outcomes + K/weight/penalty sensitivity on one hold."""
    eligible, _ = population(rows, target)
    train_rows, test_rows = _split_folds(eligible, split)
    if not train_rows or not test_rows:
        return {}
    value_fn = _value_fn(target)
    train_feats = compute_features(train_rows, target, history_pool=train_rows)
    test_feats = compute_features(test_rows, target, history_pool=train_rows)
    global_median = _global_baseline(train_feats, target)
    weights = weights_for_target(target)

    # shuffled outcomes: corrupt the train candidate outcomes used by engine C
    shuffled_rows = [dict(r) for r in train_rows]
    rng = np.random.default_rng(42)
    vals = [value_fn(r) for r in train_rows]
    nonnull = [v for v in vals if v is not None]
    rng.shuffle(nonnull)
    it = iter(nonnull)
    for r in shuffled_rows:
        if value_fn(r) is not None:
            r[target == TARGET_GROSS and "ticket_gross_total" or "headcount_total"] = next(it)

    def mae_of(preds):
        y = np.asarray([it["target"] for it in test_feats], dtype=float)
        return regression_metrics(y, preds)["mae"]

    shuffled_preds, _ = _engine_preds(test_rows, shuffled_rows, target, "C",
                                      weights=weights, global_median=global_median)
    out = {"shuffled_outcomes_mae": mae_of(shuffled_preds)}

    # K sensitivity
    for k in (3, 5, 10, 25):
        p, _ = _engine_preds(test_rows, train_rows, target, "C", k=k,
                             weights=weights, global_median=global_median)
        out[f"k={k}_mae"] = mae_of(p)

    # weight sensitivity
    for name, w in {
        "equal": {"artist": 0.25, "venue": 0.25, "market": 0.25, "calendar": 0.25},
        "venue_heavy": {"artist": 0.15, "venue": 0.55, "market": 0.20, "calendar": 0.10},
        "artist_heavy": {"artist": 0.55, "venue": 0.15, "market": 0.20, "calendar": 0.10},
    }.items():
        p, _ = _engine_preds(test_rows, train_rows, target, "C", weights=w,
                             global_median=global_median)
        out[f"weights={name}_mae"] = mae_of(p)

    # missingness penalty sensitivity
    for pen in (0.5, 1.0, 2.0):
        p, _ = _engine_preds(test_rows, train_rows, target, "C", penalty=pen,
                             weights=weights, global_median=global_median)
        out[f"penalty={pen}_mae"] = mae_of(p)

    return out


def main() -> None:
    assert_admissibility_contract()
    manifest = json.loads(MANIFEST.read_text())
    rows = manifest["rows"]
    print(f"corpus: {len(rows)} rows, checksum {manifest['checksum'][:16]}...\n")

    report: dict[str, dict] = {}
    for target in TARGETS:
        report[target] = {}
        for split in SPLITS:
            res = _run_hold(rows, target, split)
            if res is None:
                report[target][split] = {"note": "no test fold"}
                continue
            hold, test_rows, train_rows, _t, y_true, hier, c_preds, gm = res
            delta = hold["stratum"]["mae"] - hold["hierarchical"]["mae"]
            winner = "COMP" if delta < 0 else ("TIE" if delta == 0 else "HIER")
            hold["stratum_minus_hier_mae"] = round(delta, 1)
            hold["winner"] = winner
            if split == "TIME":
                hold["bootstrap"] = _bootstrap_delta(
                    test_rows, train_rows, target, y_true, hier, c_preds, gm)
            report[target][split] = hold
            print(f"{target:20s} {split:14s} n={hold['n_test']:3d}  "
                  f"hier={hold['hierarchical']['mae']:9.0f}  "
                  f"global={hold['global']['mae']:9.0f}  "
                  f"stratum={hold['stratum']['mae']:9.0f}  "
                  f"cov={hold['mean_coverage']:.2f}  delta={delta:+9.0f}  {winner}")

    # negative controls on TIME hold for the two continuous headline targets
    report["negative_controls"] = {
        target: _negative_controls(rows, target, "TIME")
        for target in (TARGET_ATTENDANCE, TARGET_GROSS)
    }

    Path("reports/comparable_engine_v1_backtest.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\nwrote reports/comparable_engine_v1_backtest.json")


if __name__ == "__main__":
    main()
