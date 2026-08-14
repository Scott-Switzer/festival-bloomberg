"""Baseline research orchestration.

Runs every target against every leakage-safe split with a fixed model ladder:

    global/venue/artist/market/artist×market/artist×venue medians
    → last/recent-3 artist
    → hierarchical comparable fallback
    → log-linear / Ridge / Poisson (regression) or logistic (sell-out)
    → partial pooling

The headline question is not "does a model beat nothing?" but "does a
statistical model beat the best historical comp under TIME and grouped holds?"
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from .baselines import (
    NAIVE_KINDS,
    classification_metrics,
    fit_log_linear,
    fit_logistic,
    fit_poisson,
    fit_ridge,
    hierarchical_fallback,
    naive_predict,
    partial_pooling_predict,
    predict_log,
    predict_prob,
    regression_metrics,
)
from .features import (
    TARGET_ATTENDANCE,
    TARGET_GROSS,
    TARGET_PAID_TICKETS,
    TARGET_SELL_OUT,
    compute_features,
    feature_vector,
    population,
)

SPLITS = ("TIME", "ARTIST_GROUP", "VENUE_GROUP", "MARKET_GROUP", "TOUR_GROUP")

VERDICT_NO_SIGNAL = "NO_PREDICTABLE_SIGNAL"
VERDICT_COMPS_ONLY = "COMPS_SIGNAL_ONLY"
VERDICT_STAT_IMPROVES = "STATISTICAL_BASELINE_IMPROVES"
VERDICT_POOLING_IMPROVES = "PARTIAL_POOLING_IMPROVES"
VERDICT_ADVANCED = "READY_FOR_ADVANCED_RESEARCH"


def _split_folds(eligible: list[dict[str, Any]], split_type: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train, test = [], []
    for row in eligible:
        fold = (row.get("folds") or {}).get(split_type)
        if fold == "TRAIN":
            train.append(row)
        elif fold == "TEST":
            test.append(row)
    return train, test


def _regression_models() -> dict[str, Any]:
    return {
        "log_linear": ("statistical", fit_log_linear, predict_log),
        "ridge": ("statistical", fit_ridge, predict_log),
        "poisson": ("statistical", fit_poisson, predict_log),
    }


def _classification_models() -> dict[str, Any]:
    return {
        "logistic": ("statistical", lambda X, y: fit_logistic(X, y, alpha=0.0), predict_prob),
        "ridge_logistic": ("statistical", lambda X, y: fit_logistic(X, y, alpha=1.0), predict_prob),
    }


def evaluate_split(
    rows: list[dict[str, Any]],
    target_type: str,
    split_type: str,
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Evaluate the full model ladder on one target × split."""
    eligible, waterfall = population(rows, target_type)
    train_rows, test_rows = _split_folds(eligible, split_type)
    if not train_rows or not test_rows:
        return {"eligible": len(eligible), "train": len(train_rows), "test": len(test_rows), "models": {}}

    train_feats = compute_features(train_rows, target_type, history_pool=train_rows)
    test_feats = compute_features(test_rows, target_type, history_pool=train_rows)
    if not test_feats:
        return {"eligible": len(eligible), "train": len(train_rows), "test": 0, "models": {}}

    global_median = _global_baseline(train_feats, target_type)
    y_true = np.asarray([it["target"] for it in test_feats], dtype=float)

    results: dict[str, Any] = {}

    # naive / comparable baselines
    for kind in NAIVE_KINDS:
        preds = [naive_predict(it, kind, global_median) for it in test_feats]
        preds = np.asarray([p if p is not None else global_median for p in preds], dtype=float)
        results[kind] = _metric(target_type, y_true, preds)

    # partial pooling
    pp = [partial_pooling_predict(it, global_median) for it in test_feats]
    pp = np.asarray([p if p is not None else global_median for p in pp], dtype=float)
    results["partial_pooling"] = _metric(target_type, y_true, pp)

    # statistical models
    models = _classification_models() if target_type == TARGET_SELL_OUT else _regression_models()
    for name in models:
        try:
            preds = _fit_predict_statistical(train_feats, test_feats, target_type, name, global_median)
            results[name] = _metric(target_type, y_true, preds)
        except Exception as exc:  # noqa: BLE001 - singular/degenerate fold
            results[name] = {"error": str(exc)}

    return {
        "eligible": len(eligible),
        "train": len(train_rows),
        "test": len(test_rows),
        "global_median": global_median,
        "models": results,
    }


def _global_baseline(feats: list[dict[str, Any]], target_type: str) -> float | None:
    vals = sorted(it["target"] for it in feats)
    n = len(vals)
    if not n:
        return None
    if target_type == TARGET_SELL_OUT:
        return sum(vals) / n  # base rate, not median, for a binary target
    return (vals[n // 2] + vals[n - 1 - n // 2]) / 2.0


def _design(items: list[dict[str, Any]], global_median: float | None) -> tuple[np.ndarray, np.ndarray]:
    X: list[list[float]] = []
    y: list[float] = []
    for it in items:
        vec, _names = feature_vector(it["features"], global_median)
        X.append([1.0] + vec)
        y.append(float(it["target"]))
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float)


def _standardize(X_train: np.ndarray, X_test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Z-score non-intercept columns using TRAIN statistics (intercept untouched)."""
    mu = X_train[:, 1:].mean(axis=0)
    sd = X_train[:, 1:].std(axis=0)
    sd = np.where(sd == 0, 1.0, sd)
    Xt = X_train.copy()
    Xv = X_test.copy()
    Xt[:, 1:] = (X_train[:, 1:] - mu) / sd
    Xv[:, 1:] = (X_test[:, 1:] - mu) / sd
    return Xt, Xv


def _clip_preds(preds: np.ndarray, train_targets: np.ndarray, target_type: str) -> np.ndarray:
    if target_type == TARGET_SELL_OUT:
        return np.clip(preds, 1e-6, 1 - 1e-6)
    lo = max(float(np.min(train_targets)) * 0.05, 1e-3)
    hi = float(np.max(train_targets)) * 50.0
    return np.clip(preds, lo, hi)


def _fit_predict_statistical(
    train_feats: list[dict[str, Any]],
    test_feats: list[dict[str, Any]],
    target_type: str,
    name: str,
    global_median: float | None,
) -> np.ndarray:
    X_train, y_train = _design(train_feats, global_median)
    X_test, _ = _design(test_feats, global_median)
    X_train_s, X_test_s = _standardize(X_train, X_test)
    models = _classification_models() if target_type == TARGET_SELL_OUT else _regression_models()
    _kind, fitter, predictor = models[name]
    coef = fitter(X_train_s, y_train)
    return _clip_preds(predictor(X_test_s, coef), y_train, target_type)


def _metric(target_type: str, y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    if target_type == TARGET_SELL_OUT:
        return classification_metrics(y_true, y_pred)
    return regression_metrics(y_true, y_pred)


def _primary_metric(target_type: str) -> str:
    return "log_loss" if target_type == TARGET_SELL_OUT else "mae"


# ---------------------------------------------------------------------------
# Random-split comparison (to expose optimism)
# ---------------------------------------------------------------------------
def evaluate_random_split(rows: list[dict[str, Any]], target_type: str, *, seed: int = 42) -> dict[str, Any]:
    eligible, _ = population(rows, target_type)
    rng = np.random.default_rng(seed)
    n = len(eligible)
    if n < 4:
        return {"models": {}}
    idx = rng.permutation(n)
    split = int(n * 0.8)
    train_rows = [eligible[i] for i in idx[:split]]
    test_rows = [eligible[i] for i in idx[split:]]

    train_feats = compute_features(train_rows, target_type, history_pool=train_rows)
    test_feats = compute_features(test_rows, target_type, history_pool=train_rows)
    global_median = _global_baseline(train_feats, target_type)
    y_true = np.asarray([it["target"] for it in test_feats], dtype=float)

    out: dict[str, Any] = {}
    for kind in NAIVE_KINDS:
        preds = np.asarray([naive_predict(it, kind, global_median) or global_median for it in test_feats], dtype=float)
        out[kind] = _metric(target_type, y_true, preds)
    pp = np.asarray([partial_pooling_predict(it, global_median) or global_median for it in test_feats], dtype=float)
    out["partial_pooling"] = _metric(target_type, y_true, pp)

    models = _classification_models() if target_type == TARGET_SELL_OUT else _regression_models()
    for name in models:
        try:
            preds = _fit_predict_statistical(train_feats, test_feats, target_type, name, global_median)
            out[name] = _metric(target_type, y_true, preds)
        except Exception as exc:  # noqa: BLE001
            out[name] = {"error": str(exc)}
    return {"test": len(test_rows), "models": out}


# ---------------------------------------------------------------------------
# Cluster bootstrap for the delta between two models
# ---------------------------------------------------------------------------
def bootstrap_delta_ci(
    rows: list[dict[str, Any]],
    target_type: str,
    split_type: str,
    model_a: str,
    model_b: str,
    *,
    seed: int = 42,
    B: int = 200,
) -> dict[str, Any]:
    """Cluster-bootstrap (by artist) the metric delta between two models."""
    eligible, _ = population(rows, target_type)
    train_rows, test_rows = _split_folds(eligible, split_type)
    if not train_rows or not test_rows:
        return {"note": "insufficient folds"}

    train_feats = compute_features(train_rows, target_type, history_pool=train_rows)
    test_feats = compute_features(test_rows, target_type, history_pool=train_rows)
    global_median = _global_baseline(train_feats, target_type)

    y_true = np.asarray([it["target"] for it in test_feats], dtype=float)
    pred_a, pred_b = _model_preds(test_feats, train_feats, target_type, global_median, model_a), \
        _model_preds(test_feats, train_feats, target_type, global_median, model_b)

    metric = _primary_metric(target_type)
    groups = np.asarray([it["row"].get("artist") or "unknown" for it in test_feats])
    rng = np.random.default_rng(seed)
    unique = np.unique(groups)
    deltas: list[float] = []
    for _ in range(B):
        sample_groups = rng.choice(unique, size=len(unique), replace=True)
        keep = np.isin(groups, sample_groups)
        if keep.sum() == 0:
            continue
        ma = _metric(target_type, y_true[keep], pred_a[keep])[metric]
        mb = _metric(target_type, y_true[keep], pred_b[keep])[metric]
        deltas.append(ma - mb)
    deltas = np.asarray(deltas)
    if deltas.size == 0:
        return {"note": "no bootstrap samples"}
    lo, hi = np.percentile(deltas, [5, 95])
    return {
        "metric": metric,
        "point_delta": float(np.median(deltas)),
        "ci_90": [float(lo), float(hi)],
        "p_improve": float(np.mean(deltas < 0)),
    }


def _model_preds(test_feats, train_feats, target_type, global_median, name):
    if name in NAIVE_KINDS:
        return np.asarray([naive_predict(it, name, global_median) or global_median for it in test_feats], dtype=float)
    if name == "partial_pooling":
        return np.asarray([partial_pooling_predict(it, global_median) or global_median for it in test_feats], dtype=float)
    return _fit_predict_statistical(train_feats, test_feats, target_type, name, global_median)


# ---------------------------------------------------------------------------
# Negative control: shuffled target
# ---------------------------------------------------------------------------
def shuffled_target_control(rows: list[dict[str, Any]], target_type: str, split_type: str, *, seed: int = 42) -> dict[str, Any]:
    eligible, _ = population(rows, target_type)
    train_rows, test_rows = _split_folds(eligible, split_type)
    if not train_rows or not test_rows:
        return {}
    rng = np.random.default_rng(seed)
    train_feats = compute_features(train_rows, target_type, history_pool=train_rows)
    test_feats = compute_features(test_rows, target_type, history_pool=train_rows)
    global_median = _global_baseline(train_feats, target_type)
    y_true = np.asarray([it["target"] for it in test_feats], dtype=float)

    # shuffle train targets, refit a statistical model, measure collapse
    shuffled = rng.permutation([it["target"] for it in train_feats])
    shuffled_feats = [{**it, "target": shuffled[i]} for i, it in enumerate(train_feats)]
    X_train, y_train = _design(shuffled_feats, global_median)
    X_test, _ = _design(test_feats, global_median)
    X_train_s, X_test_s = _standardize(X_train, X_test)
    metric = _primary_metric(target_type)
    out = {}
    models = _classification_models() if target_type == TARGET_SELL_OUT else _regression_models()
    for name, (_kind, fitter, predictor) in models.items():
        coef = fitter(X_train_s, y_train)
        preds = _clip_preds(predictor(X_test_s, coef), y_train, target_type)
        out[name] = _metric(target_type, y_true, preds)[metric]
    return {"shuffled_train_target_metric": out}


# ---------------------------------------------------------------------------
# Feature-group ablations (log-linear / logistic)
# ---------------------------------------------------------------------------
_ABLATION_GROUPS: dict[str, list[str]] = {
    "artist_only": ["artist_median", "artist_mean", "artist_last", "artist_recent3_median", "artist_count", "has_artist_history"],
    "venue_only": ["venue_median", "venue_mean", "venue_count", "has_venue_history"],
    "market_only": ["market_median", "market_mean", "market_count", "has_market_history"],
    "artist_venue": ["artist_median", "artist_mean", "artist_last", "artist_recent3_median", "artist_count", "has_artist_history", "venue_median", "venue_mean", "venue_count", "has_venue_history"],
    "artist_venue_market": ["artist_median", "artist_mean", "artist_last", "artist_recent3_median", "artist_count", "has_artist_history", "venue_median", "venue_mean", "venue_count", "has_venue_history", "market_median", "market_mean", "market_count", "has_market_history"],
}


def ablation(rows: list[dict[str, Any]], target_type: str, split_type: str, *, seed: int = 42) -> dict[str, Any]:
    eligible, _ = population(rows, target_type)
    train_rows, test_rows = _split_folds(eligible, split_type)
    if not train_rows or not test_rows:
        return {}
    train_feats = compute_features(train_rows, target_type, history_pool=train_rows)
    test_feats = compute_features(test_rows, target_type, history_pool=train_rows)
    global_median = _global_baseline(train_feats, target_type)
    y_true = np.asarray([it["target"] for it in test_feats], dtype=float)
    metric = _primary_metric(target_type)
    out: dict[str, Any] = {}
    for group, names in _ABLATION_GROUPS.items():
        X_train = _design_subset(train_feats, names, global_median)
        X_test = _design_subset(test_feats, names, global_median)
        y_train = np.asarray([it["target"] for it in train_feats], dtype=float)
        X_train_s, X_test_s = _standardize(X_train, X_test)
        if target_type == TARGET_SELL_OUT:
            coef = fit_logistic(X_train_s, y_train, alpha=0.0)
            preds = _clip_preds(predict_prob(X_test_s, coef), y_train, target_type)
        else:
            coef = fit_log_linear(X_train_s, y_train)
            preds = _clip_preds(predict_log(X_test_s, coef), y_train, target_type)
        out[group] = _metric(target_type, y_true, preds)[metric]
    return out


def _design_subset(items: list[dict[str, Any]], names: list[str], global_median: float | None) -> np.ndarray:
    full_names = None
    X: list[list[float]] = []
    for it in items:
        vec, full_names = feature_vector(it["features"], global_median)
        idx = [full_names.index(n) for n in names]
        X.append([1.0] + [vec[i] for i in idx])
    return np.asarray(X, dtype=float)


# ---------------------------------------------------------------------------
# Error segmentation (best comparable model)
# ---------------------------------------------------------------------------
def error_segmentation(rows: list[dict[str, Any]], target_type: str, split_type: str) -> dict[str, Any]:
    eligible, _ = population(rows, target_type)
    train_rows, test_rows = _split_folds(eligible, split_type)
    if not train_rows or not test_rows:
        return {}
    train_feats = compute_features(train_rows, target_type, history_pool=train_rows)
    test_feats = compute_features(test_rows, target_type, history_pool=train_rows)
    global_median = _global_baseline(train_feats, target_type)
    metric = _primary_metric(target_type)

    buckets: dict[str, list[float]] = defaultdict(list)
    for it in test_feats:
        pred = hierarchical_fallback(it["features"], global_median) or global_median
        err = _metric(target_type, np.asarray([it["target"]]), np.asarray([pred]))[metric]
        ac = it["features"].get("artist_count") or 0
        if ac == 0:
            bucket = "artist_history=0"
        elif ac == 1:
            bucket = "artist_history=1"
        elif ac <= 4:
            bucket = "artist_history=2-4"
        else:
            bucket = "artist_history=5+"
        buckets[bucket].append(err)
        buckets[f"source={it['row'].get('reporting_source')}"].append(err)
        buckets[f"year={(it['row'].get('start_date') or '')[:4]}"].append(err)
        buckets["sellout=1" if it["row"].get("reported_sellouts") else "sellout=0"].append(err)

    return {
        segment: {"n": len(v), metric: round(float(np.mean(v)), 4)}
        for segment, v in sorted(buckets.items())
    }


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------
def run_baseline_research(rows: list[dict[str, Any]], *, seed: int = 42) -> dict[str, Any]:
    report: dict[str, Any] = {"seed": seed, "targets": {}}
    for target in (TARGET_ATTENDANCE, TARGET_PAID_TICKETS, TARGET_GROSS, TARGET_SELL_OUT):
        eligible, waterfall = population(rows, target)
        entry: dict[str, Any] = {"waterfall": waterfall, "splits": {}, "random_split": {}, "bootstrap": {}, "ablation": {}, "error_segmentation": {}, "shuffled_target": {}}
        for split in SPLITS:
            entry["splits"][split] = evaluate_split(rows, target, split, seed=seed)
            entry["ablation"][split] = ablation(rows, target, split, seed=seed)
            entry["shuffled_target"][split] = shuffled_target_control(rows, target, split, seed=seed)
        entry["random_split"] = evaluate_random_split(rows, target, seed=seed)
        # headline bootstrap: best statistical vs best comparable, TIME holdout
        if entry["splits"].get("TIME", {}).get("models"):
            entry["bootstrap"]["TIME"] = bootstrap_delta_ci(
                rows, target, "TIME", "logistic" if target == TARGET_SELL_OUT else "log_linear",
                "hierarchical_fallback", seed=seed,
            )
        entry["error_segmentation"] = error_segmentation(rows, target, "TIME")
        entry["verdict"] = _target_verdict(target, entry)
        report["targets"][target] = entry
    report["verdict"] = _overall_verdict(report)
    return report


def _target_verdict(target: str, entry: dict[str, Any]) -> dict[str, Any]:
    metric = "log_loss" if target == TARGET_SELL_OUT else "mae"
    rows: list[dict[str, Any]] = []
    for split, res in entry["splits"].items():
        models = res.get("models", {})
        g = models.get("global_median", {}).get(metric)
        if g is None:
            continue
        comps = [m[metric] for k, m in models.items() if k in NAIVE_KINDS and metric in m]
        stats = [m[metric] for k, m in models.items() if k in ("log_linear", "ridge", "poisson", "logistic", "ridge_logistic") and metric in m]
        pool = models.get("partial_pooling", {}).get(metric)
        if not comps:
            continue
        rows.append({
            "split": split,
            "global": g,
            "best_comp": min(comps),
            "best_stat": min(stats) if stats else None,
            "pooling": pool,
        })
    if not rows:
        return {"verdict": VERDICT_NO_SIGNAL, "reason": "no valid model evaluations"}
    n = len(rows)
    comp_wins = sum(1 for r in rows if r["best_comp"] < r["global"])
    stat_wins = sum(1 for r in rows if r["best_stat"] is not None and r["best_stat"] < r["best_comp"])
    pool_wins = sum(1 for r in rows if r["pooling"] is not None and r["pooling"] < r["best_comp"])
    if comp_wins == 0:
        return {"verdict": VERDICT_NO_SIGNAL, "reason": "comps never beat the global baseline", "holds": n, "comp_wins": comp_wins}
    if pool_wins > stat_wins and pool_wins >= n // 2 + 1:
        return {"verdict": VERDICT_POOLING_IMPROVES, "holds": n, "comp_wins": comp_wins, "stat_wins": stat_wins, "pool_wins": pool_wins}
    if stat_wins >= n // 2 + 1:
        return {"verdict": VERDICT_STAT_IMPROVES, "holds": n, "comp_wins": comp_wins, "stat_wins": stat_wins, "pool_wins": pool_wins}
    return {"verdict": VERDICT_COMPS_ONLY, "holds": n, "comp_wins": comp_wins, "stat_wins": stat_wins, "pool_wins": pool_wins}


def _overall_verdict(report: dict[str, Any]) -> dict[str, Any]:
    verdicts = Counter(v["verdict"]["verdict"] for v in report["targets"].values() if "verdict" in v)
    stat_improves = any(
        v["verdict"]["verdict"] in (VERDICT_STAT_IMPROVES, VERDICT_POOLING_IMPROVES)
        for v in report["targets"].values() if "verdict" in v
    )
    return {"target_verdicts": dict(verdicts), "statistical_or_pooling_improves_comps": stat_improves}


PROHIBITED_USE = [
    "production booking recommendation",
    "guarantee setting",
    "customer-facing forecast",
    "commercial API output",
]


def model_card(
    target_type: str,
    model_name: str,
    metrics: dict[str, Any],
    *,
    features: list[str] | None = None,
    split_type: str | None = None,
) -> dict[str, Any]:
    """A research model card; V1 models are research-only by construction."""
    return {
        "target": target_type,
        "model": model_name,
        "split": split_type,
        "features": features or ["historical comparable aggregates (point-in-time)"],
        "metrics": metrics,
        "known_biases": [
            "chart/editorial-selected research corpus, not a representative draw sample",
            "research-only rights; commercial corpus is empty",
        ],
        "rights": "RESEARCH_ONLY / TERMS_REVIEW_REQUIRED",
        "intended_use": "historical-comparable predictability research",
        "prohibited_use": PROHIBITED_USE,
        "software_version": "baseline_research_v1",
    }
