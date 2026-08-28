"""Comparable V2 backtest: hierarchical champion vs V1 vs V2.

Engines:
  A  hierarchical median champion (baselines.hierarchical_fallback)
  B  Comparable V1 — 4 components: artist, venue, market, calendar
  C  Comparable V2 — 6 components: + attention_30d, competition_same_day

New feature families:
  - artist_attention_wikimedia_30d_at_cutoff (continuous, normalized)
  - event_competition_same_day_market (continuous, log-scaled)

Run:  PYTHONPATH=python .venv/bin/python scripts/comparable_v2_backtest.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import duckdb

from festival_bloomberg.research.baselines import hierarchical_fallback, regression_metrics, spearman
from festival_bloomberg.research.comparable import (
    COMPONENTS as V1_COMPONENTS,
    assert_admissibility_contract,
    calendar_distance,
    comparable_distance,
    point_in_time_candidates,
    retrieve_global,
    retrieve_stratum,
    weighted_quantile,
    DEFAULT_WEIGHTS,
    DEFAULT_K,
    DEFAULT_MISSINGNESS_PENALTY as _DEFAULT_PENALTY,
    DEFAULT_MIN_COVERAGE,
    DEFAULT_MIN_STRATUM_SIZE,
)
from festival_bloomberg.research.features import (
    TARGET_ATTENDANCE,
    TARGET_GROSS,
    TARGET_PAID_TICKETS,
    compute_features,
    population,
)
from festival_bloomberg.research.experiment import _global_baseline, _split_folds
from festival_bloomberg.events.identity import normalize_artist_name
from festival_bloomberg.attention.wikimedia_pageviews import artist_key_for

MANIFEST = Path("reports/baseline_research_v1/corpus_v1_manifest.json")
SPLITS = ("TIME",)
TARGETS = (TARGET_ATTENDANCE, TARGET_GROSS, TARGET_PAID_TICKETS)

# V2 components = V1 + attention + competition
V2_COMPONENTS = ("artist", "venue", "market", "calendar", "attention", "competition")

# V2 weights: identity dominates, attention and competition supplement
V2_WEIGHTS = {
    "artist": 0.25, "venue": 0.25, "market": 0.20, "calendar": 0.10,
    "attention": 0.10, "competition": 0.10,
}

WIKIMEDIA_30D_MIN = 0.4  # minimum coverage for attention to be used
COMPETITION_SAME_DAY_MIN = 0.4


def _value_fn(target_type: str):
    if target_type == TARGET_GROSS:
        return lambda r: r.get("ticket_gross_total")
    return lambda r: r.get("headcount_total")


def _load_warehouse_features() -> tuple[dict[str, float], dict[str, float]]:
    """Load Wikimedia attention and competition from warehouse."""
    conn = duckdb.connect("data/warehouse/boxoffice_research_v2.duckdb", read_only=True)

    # Wikimedia: latest 30d observation per artist
    wiki = conn.execute("""
        SELECT artist_key, value_sum, period_start, period_end
        FROM metrics.artist_attention_observations
        WHERE project = 'en.wikipedia' AND status = 'ok'
        ORDER BY period_end DESC
    """).fetchall()

    # Take latest per artist
    attention_by_key: dict[str, float] = {}
    for r in wiki:
        ak = r[0]
        if ak not in attention_by_key and r[1] is not None:
            attention_by_key[ak] = float(r[1])

    # Competition: compute average same-day density per city from warehouse
    # This gives a market-level competition proxy for corpus events
    city_density: dict[str, float] = {}
    city_event_count: dict[str, int] = {}
    raw_cities = conn.execute("""
        SELECT LOWER(city) as city, COUNT(DISTINCT platform_object_id) as n_events
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster' AND city IS NOT NULL
        GROUP BY LOWER(city)
    """).fetchall()
    for city, n_events in raw_cities:
        city_event_count[city] = n_events
        # Average same-day competitors per event in this city
        avg = conn.execute("""
            SELECT AVG(daily_count) FROM (
                SELECT local_date, COUNT(DISTINCT platform_object_id) - 1 as daily_count
                FROM events.provider_event_snapshots
                WHERE provider = 'ticketmaster' AND LOWER(city) = ?
                GROUP BY local_date
            )
        """, [city]).fetchone()
        city_density[city] = float(avg[0]) if avg and avg[0] is not None else 0.0

    conn.close()
    return attention_by_key, city_density


def _enrich_corpus(rows: list[dict], attention_by_key: dict, city_density: dict) -> list[dict]:
    """Add attention_30d and competition_same_day to each corpus row.

    Competition uses city-level average same-day density from the warehouse
    as a market-level proxy (corpus dates don't overlap with warehouse dates).
    """
    enriched = []
    for r in rows:
        r2 = dict(r)

        # Attention
        a = normalize_artist_name(r.get("artist"))
        if a:
            ak = artist_key_for(a)
            r2["_attention_30d"] = attention_by_key.get(ak)

        # Competition: city-level average same-day density
        city = (r.get("city") or r.get("market") or "").strip().lower()
        if city and city in city_density:
            r2["_competition_same_day"] = city_density[city]

        enriched.append(r2)
    return enriched


def _v2_component_distances(target: dict, candidate: dict) -> tuple[dict[str, float], dict[str, bool]]:
    """6-component distance for V2."""
    # Start with V1 components
    distances = {}
    observed = {}

    ta, ca = target.get("artist"), candidate.get("artist")
    observed["artist"] = bool(ta) and bool(ca)
    distances["artist"] = 0.0 if (observed["artist"] and ta == ca) else 1.0

    tv, cv = target.get("venue"), candidate.get("venue")
    observed["venue"] = bool(tv) and bool(cv)
    distances["venue"] = 0.0 if (observed["venue"] and tv == cv) else 1.0

    tm = (target.get("city") or target.get("market") or "").strip().lower()
    cm = (candidate.get("city") or candidate.get("market") or "").strip().lower()
    observed["market"] = bool(tm) and bool(cm)
    distances["market"] = 0.0 if (observed["market"] and tm == cm) else 1.0

    cal = calendar_distance(target.get("start_date"), candidate.get("start_date"))
    observed["calendar"] = cal is not None
    distances["calendar"] = cal if cal is not None else 1.0

    # Attention: continuous distance (normalized)
    ta_att = target.get("_attention_30d")
    ca_att = candidate.get("_attention_30d")
    if ta_att is not None and ca_att is not None and (ta_att + ca_att) > 0:
        # Log-ratio distance
        log_ratio = abs(math.log1p(ta_att) - math.log1p(ca_att))
        max_expected = 15.0  # ~3M pageviews max
        distances["attention"] = min(1.0, log_ratio / max_expected)
        observed["attention"] = True
    else:
        distances["attention"] = 1.0
        observed["attention"] = False

    # Competition: continuous distance
    ta_comp = target.get("_competition_same_day")
    ca_comp = candidate.get("_competition_same_day")
    if ta_comp is not None and ca_comp is not None:
        # Log-scaled absolute difference
        diff = abs(math.log1p(ta_comp) - math.log1p(ca_comp))
        distances["competition"] = min(1.0, diff / 5.0)  # log1p(200) ~ 5.3
        observed["competition"] = True
    else:
        distances["competition"] = 1.0
        observed["competition"] = False

    return distances, observed


def v2_comparable_distance(target: dict, candidate: dict, *,
                            weights: dict | None = None,
                            missingness_penalty: float = 1.0) -> dict:
    """V2 distance with 6 components."""
    w = weights or V2_WEIGHTS
    distances, observed = _v2_component_distances(target, candidate)

    total_weight = sum(w.get(c, 0.0) for c in V2_COMPONENTS) or 1.0
    observed_weight = sum(w.get(c, 0.0) for c in V2_COMPONENTS if observed.get(c))
    observed_distance = (
        sum(w.get(c, 0.0) * distances[c] for c in V2_COMPONENTS if observed.get(c))
        / observed_weight if observed_weight > 0 else 1.0
    )
    coverage = observed_weight / total_weight if total_weight > 0 else 0.0
    missing_frac = 1.0 - coverage
    ranking_distance = observed_distance + missingness_penalty * missing_frac
    ranking_distance = min(ranking_distance, 1.0 + missingness_penalty)

    missing = [c for c in V2_COMPONENTS if not observed.get(c)]
    return {
        "observed_distance": round(observed_distance, 6),
        "coverage_score": round(coverage, 6),
        "ranking_distance": round(ranking_distance, 6),
        "components": {c: round(distances[c], 6) for c in V2_COMPONENTS},
        "observed": {c: bool(observed.get(c)) for c in V2_COMPONENTS},
        "missing": missing,
    }


def _v2_score_candidates(target, candidates, value_fn, *, weights, missingness_penalty, min_coverage):
    """Score candidates using V2 distance."""
    scored = []
    for cand in candidates:
        v = value_fn(cand)
        if v is None:
            continue
        d = v2_comparable_distance(target, cand, weights=weights,
                                   missingness_penalty=missingness_penalty)
        if d["coverage_score"] < min_coverage:
            continue
        scored.append((d["ranking_distance"], cand, v, d))
    scored.sort(key=lambda t: t[0])
    return scored


def _v2_retrieve_stratum(target, candidates, value_fn, *, k=10, weights=None,
                          missingness_penalty=1.0, min_coverage=0.25,
                          min_stratum_size=3, target_engagement_id=None):
    """V2 engine: stratum + V2 distance."""
    from festival_bloomberg.research.comparable import _stratum_members, STRATA, _valuation, _comp_payload

    pool = [c for c in candidates if not (target_engagement_id and c.get("engagement_id") == target_engagement_id)]
    members = _stratum_members(target, pool)
    chosen = None
    scored = []
    for stratum in STRATA:
        group = members.get(stratum, [])
        group_scored = _v2_score_candidates(target, group, value_fn, weights=weights,
                                             missingness_penalty=missingness_penalty,
                                             min_coverage=min_coverage)
        if len(group_scored) >= min_stratum_size:
            chosen = stratum
            scored = group_scored
            break
    if chosen is None:
        for stratum in STRATA:
            group = members.get(stratum, [])
            group_scored = _v2_score_candidates(target, group, value_fn, weights=weights,
                                                 missingness_penalty=missingness_penalty,
                                                 min_coverage=min_coverage)
            if group_scored:
                chosen = stratum
                scored = group_scored
                break

    top = scored[:k]
    valuation = _valuation(top)
    return {
        "comps": [_comp_payload(c, v, d) for _r, c, v, d in top],
        "valuation": valuation,
        "n_candidates": len(scored),
        "stratum": chosen,
        "coverage_score": round(
            sum(d["coverage_score"] for _r, _c, _v, d in top) / len(top), 6
        ) if top else 0.0,
    }


def _engine_a_hierarchical(test_feats, global_median):
    """Engine A: hierarchical champion."""
    return np.asarray([
        hierarchical_fallback(it["features"], global_median) or global_median
        for it in test_feats
    ], dtype=float)


def _engine_b_v1(test_rows, train_rows, target, value_fn, global_median, weights):
    """Engine B: Comparable V1 (stratum + 4-component distance)."""
    preds = []
    for t in test_rows:
        cands = point_in_time_candidates(t, train_rows, target_engagement_id=t.get("engagement_id"))
        res = retrieve_stratum(t, cands, value_fn=value_fn, weights=weights)
        v = res["valuation"]["weighted_median"] if res["valuation"] else None
        preds.append(v if v is not None else global_median)
    return np.asarray(preds, dtype=float)


def _engine_c_v2(test_rows, train_rows, target, value_fn, global_median, weights):
    """Engine C: Comparable V2 (stratum + 6-component distance)."""
    preds = []
    for t in test_rows:
        cands = point_in_time_candidates(t, train_rows, target_engagement_id=t.get("engagement_id"))
        res = _v2_retrieve_stratum(t, cands, value_fn=value_fn, weights=weights)
        v = res["valuation"]["weighted_median"] if res["valuation"] else None
        preds.append(v if v is not None else global_median)
    return np.asarray(preds, dtype=float)


def _engine_d_v2_attention_only(test_rows, train_rows, target, value_fn, global_median):
    """Engine D: V2 with ONLY attention (ablation)."""
    w = {"artist": 0.30, "venue": 0.30, "market": 0.25, "calendar": 0.15,
         "attention": 0.15, "competition": 0.0}
    preds = []
    for t in test_rows:
        cands = point_in_time_candidates(t, train_rows, target_engagement_id=t.get("engagement_id"))
        res = _v2_retrieve_stratum(t, cands, value_fn=value_fn, weights=w)
        v = res["valuation"]["weighted_median"] if res["valuation"] else None
        preds.append(v if v is not None else global_median)
    return np.asarray(preds, dtype=float)


def _engine_e_v2_competition_only(test_rows, train_rows, target, value_fn, global_median):
    """Engine E: V2 with ONLY competition (ablation)."""
    w = {"artist": 0.30, "venue": 0.30, "market": 0.25, "calendar": 0.15,
         "attention": 0.0, "competition": 0.15}
    preds = []
    for t in test_rows:
        cands = point_in_time_candidates(t, train_rows, target_engagement_id=t.get("engagement_id"))
        res = _v2_retrieve_stratum(t, cands, value_fn=value_fn, weights=w)
        v = res["valuation"]["weighted_median"] if res["valuation"] else None
        preds.append(v if v is not None else global_median)
    return np.asarray(preds, dtype=float)


def _bootstrap_delta(y_true, hier, challenger, groups, *, seed=42, B=200):
    """Cluster-bootstrap MAE delta."""
    rng = np.random.default_rng(seed)
    unique = np.unique(groups)
    deltas = []
    for _ in range(B):
        sample = rng.choice(unique, size=len(unique), replace=True)
        keep = np.isin(groups, sample)
        if keep.sum() == 0:
            continue
        ma = regression_metrics(y_true[keep], hier[keep])["mae"]
        mc = regression_metrics(y_true[keep], challenger[keep])["mae"]
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


def main():
    assert_admissibility_contract()
    manifest = json.loads(MANIFEST.read_text())
    rows = manifest["rows"]
    print(f"corpus: {len(rows)} rows\n")

    # Load warehouse features
    print("Loading warehouse features...")
    attention_by_key, city_density = _load_warehouse_features()
    print(f"  Wikimedia attention: {len(attention_by_key)} artists")
    print(f"  Competition (city density): {len(city_density)} cities")

    # Enrich corpus
    enriched = _enrich_corpus(rows, attention_by_key, city_density)

    # Count enrichment coverage
    att_count = sum(1 for r in enriched if r.get("_attention_30d") is not None)
    comp_count = sum(1 for r in enriched if r.get("_competition_same_day") is not None)
    print(f"\nEnrichment coverage:")
    print(f"  Attention: {att_count}/{len(enriched)} ({att_count/len(enriched)*100:.1f}%)")
    print(f"  Competition: {comp_count}/{len(enriched)} ({comp_count/len(enriched)*100:.1f}%)")
    if comp_count > 0:
        comp_vals = [r["_competition_same_day"] for r in enriched if r.get("_competition_same_day") is not None]
        print(f"  Competition range: {min(comp_vals):.1f} to {max(comp_vals):.1f} (avg same-day competitors)")

    # Run backtest
    report = {}
    for target in TARGETS:
        report[target] = {}
        for split in SPLITS:
            eligible, _ = population(enriched, target)
            train_rows, test_rows = _split_folds(eligible, split)
            if not train_rows or not test_rows:
                continue

            value_fn = _value_fn(target)
            train_feats = compute_features(train_rows, target, history_pool=train_rows)
            test_feats = compute_features(test_rows, target, history_pool=train_rows)
            global_median = _global_baseline(train_feats, target)

            y_true = np.asarray([it["target"] for it in test_feats], dtype=float)

            # Engine A: hierarchical champion
            preds_a = _engine_a_hierarchical(test_feats, global_median)

            # Engine B: V1 comparable (stratum + 4-component)
            preds_b = _engine_b_v1(test_rows, train_rows, target, value_fn, global_median,
                                    weights_for_target(target))

            # Engine C: V2 comparable (stratum + 6-component)
            preds_c = _engine_c_v2(test_rows, train_rows, target, value_fn, global_median,
                                    V2_WEIGHTS)

            # Engine D: V2 attention-only ablation
            preds_d = _engine_d_v2_attention_only(test_rows, train_rows, target, value_fn,
                                                   global_median)

            # Engine E: V2 competition-only ablation
            preds_e = _engine_e_v2_competition_only(test_rows, train_rows, target, value_fn,
                                                     global_median)

            def _metrics(y, p):
                m = regression_metrics(y, p)
                s = spearman(y, p)
                return {**m, "spearman": s}

            m_a = _metrics(y_true, preds_a)
            m_b = _metrics(y_true, preds_b)
            m_c = _metrics(y_true, preds_c)
            m_d = _metrics(y_true, preds_d)
            m_e = _metrics(y_true, preds_e)

            groups = np.asarray([r.get("artist") or "unknown" for r in test_rows])

            # Bootstrap deltas
            boot_bc = _bootstrap_delta(y_true, preds_b, preds_c, groups)
            boot_ac = _bootstrap_delta(y_true, preds_a, preds_c, groups)

            result = {
                "n_test": len(test_rows),
                "A_hierarchical": m_a,
                "B_V1_comparable": m_b,
                "C_V2_comparable": m_c,
                "D_V2_attention_only": m_d,
                "E_V2_competition_only": m_e,
                "delta_V1_vs_V2_MAE": round(m_b["mae"] - m_c["mae"], 1),
                "delta_champion_vs_V2_MAE": round(m_a["mae"] - m_c["mae"], 1),
                "winner_V1_vs_V2": "V2" if m_c["mae"] < m_b["mae"] else ("TIE" if m_c["mae"] == m_b["mae"] else "V1"),
                "winner_champion_vs_V2": "V2" if m_c["mae"] < m_a["mae"] else ("TIE" if m_c["mae"] == m_a["mae"] else "CHAMPION"),
                "bootstrap_V1_vs_V2": boot_bc,
                "bootstrap_champion_vs_V2": boot_ac,
            }
            report[target][split] = result

            print(f"\n{target:20s} {split}  n={len(test_rows)}")
            print(f"  A (champion):      MAE={m_a['mae']:9.0f}  MdAE={m_a['mdae']:9.0f}  WAPE={m_a['wape']:.3f}  Spearman={m_a['spearman']:.3f}")
            print(f"  B (V1 comparable): MAE={m_b['mae']:9.0f}  MdAE={m_b['mdae']:9.0f}  WAPE={m_b['wape']:.3f}  Spearman={m_b['spearman']:.3f}")
            print(f"  C (V2 comparable): MAE={m_c['mae']:9.0f}  MdAE={m_c['mdae']:9.0f}  WAPE={m_c['wape']:.3f}  Spearman={m_c['spearman']:.3f}")
            print(f"  D (V2 attn only):  MAE={m_d['mae']:9.0f}  MdAE={m_d['mdae']:9.0f}  WAPE={m_d['wape']:.3f}  Spearman={m_d['spearman']:.3f}")
            print(f"  E (V2 comp only):  MAE={m_e['mae']:9.0f}  MdAE={m_e['mdae']:9.0f}  WAPE={m_e['wape']:.3f}  Spearman={m_e['spearman']:.3f}")
            print(f"  delta V1→V2:  {result['delta_V1_vs_V2_MAE']:+.0f} MAE  winner={result['winner_V1_vs_V2']}")
            print(f"  delta A→V2:   {result['delta_champion_vs_V2_MAE']:+.0f} MAE  winner={result['winner_champion_vs_V2']}")
            print(f"  bootstrap V1→V2: point={boot_bc['point_delta']:.0f}  90%CI=[{boot_bc['ci_90'][0]:.0f},{boot_bc['ci_90'][1]:.0f}]  p_improve={boot_bc['p_improve']:.2f}")

    out_path = Path("reports/comparable_engine_v2_backtest.json")
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out_path}")


def weights_for_target(target_type):
    from festival_bloomberg.research.comparable import TARGET_WEIGHTS, DEFAULT_WEIGHTS
    return dict(TARGET_WEIGHTS.get(target_type, DEFAULT_WEIGHTS))


if __name__ == "__main__":
    main()
