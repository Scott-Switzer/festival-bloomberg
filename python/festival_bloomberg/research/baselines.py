"""Baseline models and metrics for baseline research (pure numpy, no sklearn).

Models are deliberately simple and deterministic. The hurdle is the set of
historical-comparable baselines (venue/artist/market medians and a hierarchical
fallback); statistical models only matter if they beat those comps under
leakage-safe holds.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Naive / comparable baselines
# ---------------------------------------------------------------------------
_HIERARCHY = (
    ("artist_venue", "artist_venue_median"),
    ("artist_market", "artist_market_median"),
    ("artist", "artist_median"),
    ("venue", "venue_median"),
    ("market", "market_median"),
)


def hierarchical_fallback(features: dict[str, Any], global_median: float | None) -> float | None:
    """Most-specific available historical median, else global median."""
    for _level, key in _HIERARCHY:
        v = features.get(key)
        if v is not None:
            return v
    return global_median


def naive_predict(
    item: dict[str, Any],
    kind: str,
    global_median: float | None,
) -> float | None:
    f = item["features"]
    if kind == "global_median":
        return global_median
    if kind == "venue_median":
        return f.get("venue_median") if f.get("venue_median") is not None else global_median
    if kind == "artist_median":
        return f.get("artist_median") if f.get("artist_median") is not None else global_median
    if kind == "market_median":
        return f.get("market_median") if f.get("market_median") is not None else global_median
    if kind == "artist_market_median":
        return f.get("artist_market_median") if f.get("artist_market_median") is not None else global_median
    if kind == "artist_venue_median":
        return f.get("artist_venue_median") if f.get("artist_venue_median") is not None else global_median
    if kind == "artist_last":
        return f.get("artist_last") if f.get("artist_last") is not None else global_median
    if kind == "artist_recent3_median":
        return f.get("artist_recent3_median") if f.get("artist_recent3_median") is not None else global_median
    if kind == "hierarchical_fallback":
        return hierarchical_fallback(f, global_median)
    raise ValueError(f"unknown naive kind {kind!r}")


NAIVE_KINDS = (
    "global_median", "venue_median", "artist_median", "market_median",
    "artist_market_median", "artist_venue_median", "artist_last",
    "artist_recent3_median", "hierarchical_fallback",
)


# ---------------------------------------------------------------------------
# Partial pooling (shrunken hierarchical fallback)
# ---------------------------------------------------------------------------
_PARTIAL_HIERARCHY = (
    ("artist_venue", "artist_venue_count", "artist_venue_mean"),
    ("artist_market", "artist_market_count", "artist_market_mean"),
    ("artist", "artist_count", "artist_mean"),
    ("venue", "venue_count", "venue_mean"),
    ("market", "market_count", "market_mean"),
)


def partial_pooling_predict(
    item: dict[str, Any],
    global_mean: float | None,
    *,
    strength: float = 5.0,
) -> float | None:
    """Shrunken group mean toward the global mean (empirical-Bayes partial pooling)."""
    f = item["features"]
    for _level, count_key, mean_key in _PARTIAL_HIERARCHY:
        count = f.get(count_key)
        mean = f.get(mean_key)
        if count and mean is not None:
            return (count * mean + strength * (global_mean or 0.0)) / (count + strength)
    return global_mean


# ---------------------------------------------------------------------------
# Statistical baselines (pure numpy)
# ---------------------------------------------------------------------------
def _design_matrix(items: list[dict[str, Any]], global_median: float | None) -> tuple[np.ndarray, np.ndarray]:
    from .features import feature_vector

    X: list[list[float]] = []
    y: list[float] = []
    for item in items:
        vec, _names = feature_vector(item["features"], global_median)
        X.append([1.0] + vec)
        y.append(float(item["target"]))
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float)


def fit_log_linear(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """OLS on log(target). Returns coefficients (intercept first)."""
    y_log = np.log(np.clip(y, 1e-6, None))
    coef, *_ = np.linalg.lstsq(X, y_log, rcond=None)
    return coef


def fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    """Ridge regression on log(target). Intercept is not regularized."""
    y_log = np.log(np.clip(y, 1e-6, None))
    p = X.shape[1]
    penalty = alpha * np.eye(p)
    penalty[0, 0] = 0.0
    A = X.T @ X + penalty
    b = X.T @ y_log
    return np.linalg.solve(A, b)


def fit_poisson(X: np.ndarray, y: np.ndarray, max_iter: int = 50) -> np.ndarray:
    """Poisson GLM (log link) via IRLS. Deterministic."""
    coef = np.zeros(X.shape[1])
    for _ in range(max_iter):
        eta = X @ coef
        mu = np.clip(np.exp(eta), 1e-6, None)
        W = mu
        z = eta + (y - mu) / mu
        A = X.T @ (W[:, None] * X)
        b = X.T @ (W * z)
        try:
            new = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            break
        if np.max(np.abs(new - coef)) < 1e-6:
            coef = new
            break
        coef = new
    return coef


def fit_logistic(X: np.ndarray, y: np.ndarray, alpha: float = 0.0, max_iter: int = 100) -> np.ndarray:
    """Binary logistic regression via IRLS. Deterministic, divergence-guarded."""
    coef = np.zeros(X.shape[1])
    p = X.shape[1]
    penalty = (alpha + 1e-4) * np.eye(p)  # tiny L2 prevents IRLS blow-up
    penalty[0, 0] = 0.0
    for _ in range(max_iter):
        eta = np.clip(X @ coef, -30, 30)
        prob = 1.0 / (1.0 + np.exp(-eta))
        W = np.clip(prob * (1 - prob), 1e-6, None)
        z = eta + (y - prob) / W
        A = X.T @ (W[:, None] * X) + penalty
        b = X.T @ (W * z)
        new = np.linalg.lstsq(A, b, rcond=None)[0]
        if not np.all(np.isfinite(new)):
            break
        if np.max(np.abs(new - coef)) < 1e-6:
            coef = new
            break
        coef = new
    return coef


def predict_log(X: np.ndarray, coef: np.ndarray) -> np.ndarray:
    return np.exp(X @ coef)


def predict_prob(X: np.ndarray, coef: np.ndarray) -> np.ndarray:
    eta = np.clip(X @ coef, -30, 30)
    return 1.0 / (1.0 + np.exp(-eta))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(np.argsort(a))
    return order.astype(float)


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = _rank(a), _rank(b)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    resid = y_pred - y_true
    abs_resid = np.abs(resid)
    n = len(y_true)
    mean_true = float(np.mean(y_true))
    ss_tot = float(np.sum((y_true - mean_true) ** 2))
    ss_res = float(np.sum(resid ** 2))

    log_true = np.log(np.clip(y_true, 1e-6, None))
    log_pred = np.log(np.clip(y_pred, 1e-6, None))
    rmsle = float(np.sqrt(np.mean((log_pred - log_true) ** 2)))
    wape = float(np.sum(abs_resid) / np.sum(y_true)) if np.sum(y_true) else 0.0

    return {
        "n": n,
        "mae": float(np.mean(abs_resid)),
        "mdae": float(np.median(abs_resid)),
        "rmse": float(np.sqrt(np.mean(resid ** 2))),
        "rmsle": rmsle,
        "wape": wape,
        "spearman": spearman(y_true, y_pred),
        "r2": float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0,
        "mape": float(np.mean(abs_resid / np.clip(np.abs(y_true), 1e-6, None))),
    }


def roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=int)
    if len(np.unique(y_true)) < 2:
        return float("nan")
    # Rank all observations by score (1-indexed, ties averaged), then use the
    # Mann-Whitney U rank formulation: AUC = (sum of positive ranks -
    # n_pos*(n_pos+1)/2) / (n_pos * n_neg).
    y_score = np.asarray(y_score, dtype=float)
    n = len(y_score)
    order = np.argsort(y_score)
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j < n and y_score[order[j]] == y_score[order[i]]:
            j += 1
        ranks[order[i:j]] = (i + 1 + j) / 2.0  # 1-indexed average rank
        i = j
    n_pos = int(np.sum(y_true == 1))
    n_neg = n - n_pos
    rank_sum_pos = float(np.sum(ranks[y_true == 1]))
    return float((rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    order = np.argsort(-y_score)
    y_true = np.asarray(y_true, dtype=int)[order]
    n_pos = int(np.sum(y_true))
    if n_pos == 0:
        return float("nan")
    prec_sum = 0.0
    tp = 0
    fp = 0
    for i, label in enumerate(y_true):
        if label == 1:
            tp += 1
            prec_sum += tp / (tp + fp)
        else:
            fp += 1
    return float(prec_sum / n_pos)


def classification_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.clip(np.asarray(y_prob, dtype=float), 1e-6, 1 - 1e-6)
    eps = 1e-12
    log_loss = float(-np.mean(y_true * np.log(y_prob) + (1 - y_true) * np.log(1 - y_prob)))
    brier = float(np.mean((y_prob - y_true) ** 2))
    return {
        "n": len(y_true),
        "log_loss": log_loss,
        "brier": brier,
        "roc_auc": roc_auc(y_true, y_prob),
        "pr_auc": pr_auc(y_true, y_prob),
        "baseline_rate": float(np.mean(y_true)),
    }
