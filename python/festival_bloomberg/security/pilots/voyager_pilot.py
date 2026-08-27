"""PILOT 1 — Spotify Voyager (Apache-2.0) nearest-neighbor retrieval.

Evaluates the pattern Voyager provides (ANN/nearest-neighbor artist search)
against OUR deterministic factor vectors. This is NOT a predictive model and
NOT a booking recommendation: it is retrieval-only comparable search.

Approach (isolated, no production wiring):

1. Build a deterministic factor vector per artist from
   ``metrics.artist_factor_observations`` (latest value per selected factor).
2. KNN retrieval: cosine / euclidean nearest neighbors over the vector space.
3. Evaluate against the CO_BILLED peer edges already in the warehouse
   (a true comparable signal): do the KNN neighbors of an artist overlap its
   co-billed peers materially more than random?

The verdict (ADOPT vs REJECT) is computed from measured overlap lift and fed
to the adoption registry. If ADOPT, the actual voyager library would be added
as a dependency; this module only proves the pattern is worth adopting.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

FACTOR_NAMES = (
    "LB_TOTAL_LISTENS",
    "LB_TOTAL_LISTENERS",
    "WIKI_VIEWS_28D",
    "WIKI_VIEWS_90D",
    "YT_SUBSCRIBERS",
    "YT_CHANNEL_VIEWS",
)


def build_factor_vectors(conn, *, artist_keys: list[str]) -> dict[str, dict[str, float]]:
    """Latest value per factor per artist (deterministic; UNKNOWN stays absent)."""
    vectors: dict[str, dict[str, float]] = {}
    if not artist_keys:
        return vectors
    placeholders = ", ".join("?" for _ in artist_keys)
    rows = conn.execute(
        f"""
        SELECT artist_key, factor_name, value
        FROM (
            SELECT artist_key, factor_name, value,
                   ROW_NUMBER() OVER (PARTITION BY artist_key, factor_name
                                      ORDER BY as_of DESC, retrieved_at DESC) AS rn
            FROM metrics.artist_factor_observations
            WHERE artist_key IN ({placeholders})
              AND factor_name IN ({", ".join('?' for _ in FACTOR_NAMES)})
              AND value IS NOT NULL
        )
        WHERE rn = 1
        """,
        [*artist_keys, *FACTOR_NAMES],
    ).fetchall()
    for artist_key, factor_name, value in rows:
        vectors.setdefault(artist_key, {})[factor_name] = float(value)
    return vectors


def _dot(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) & set(b)
    return sum(a[k] * b[k] for k in keys)


def _norm(a: dict[str, float]) -> float:
    return math.sqrt(sum(v * v for v in a.values()))


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return _dot(a, b) / (na * nb)


def knn(
    vectors: dict[str, dict[str, float]],
    *,
    k: int = 20,
) -> dict[str, list[tuple[str, float]]]:
    """K nearest neighbors per artist (cosine over shared factors)."""
    out: dict[str, list[tuple[str, float]]] = {}
    keys = list(vectors)
    for i, artist in enumerate(keys):
        scored: list[tuple[str, float]] = []
        for j, other in enumerate(keys):
            if i == j:
                continue
            scored.append((other, cosine_similarity(vectors[artist], vectors[other])))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        out[artist] = scored[:k]
    return out


def load_peers(conn, *, artist_keys: list[str]) -> dict[str, set[str]]:
    if not artist_keys:
        return {}
    placeholders = ", ".join("?" for _ in artist_keys)
    rows = conn.execute(
        f"""
        SELECT subject_key, peer_key
        FROM core.artist_peer_edges
        WHERE subject_key IN ({placeholders})
        """,
        artist_keys,
    ).fetchall()
    out: dict[str, set[str]] = {}
    for subject, peer in rows:
        out.setdefault(subject, set()).add(peer)
    return out


def evaluate(
    conn,
    *,
    artist_keys: list[str] | None = None,
    k: int = 20,
    min_factor_coverage: int = 2,
) -> dict[str, Any]:
    """Run the pilot: KNN + overlap-vs-random evaluation.

    Returns a verdict payload:
      - overlap_lift: how much more KNN neighbors overlap CO_BILLED peers
        than a random baseline of the same size;
      - recommendation: ADOPT if lift is positive and coverage adequate,
        else REJECT_OVERLAP / INSUFFICIENT_DATA.
    """
    if artist_keys is None:
        rows = conn.execute(
            "SELECT artist_key FROM metrics.artist_factor_observations GROUP BY artist_key"
        ).fetchall()
        artist_keys = [r[0] for r in rows]

    vectors = build_factor_vectors(conn, artist_keys=artist_keys)
    # only artists with at least min_factor_coverage factors participate
    usable = {k: v for k, v in vectors.items() if len(v) >= min_factor_coverage}
    if len(usable) < 3:
        return {
            "status": "INSUFFICIENT_DATA",
            "artists_with_vectors": len(vectors),
            "usable_artists": len(usable),
            "recommendation": "INSUFFICIENT_DATA",
            "reason": "need >= 3 artists with >= min_factor_coverage factors",
        }
    neighbors = knn(usable, k=min(k, len(usable) - 1))
    peers = load_peers(conn, artist_keys=list(usable))

    def overlap(artist: str, candidates: list[str]) -> float:
        peer_set = peers.get(artist, set())
        if not peer_set:
            return 0.0
        hit = sum(1 for c in candidates if c in peer_set)
        return hit / len(peer_set)

    # KNN overlap vs random-overlap of same sample size
    random_overlaps: list[float] = []
    knn_overlaps: list[float] = []
    all_keys = list(usable)
    import random

    rng = random.Random(42)
    for artist in all_keys:
        others = [o for o in all_keys if o != artist]
        if not others:
            continue
        sample = rng.sample(others, min(k, len(others)))
        random_overlaps.append(overlap(artist, sample))
        knn_overlaps.append(overlap(artist, [c for c, _s in neighbors[artist]]))

    mean_random = sum(random_overlaps) / len(random_overlaps) if random_overlaps else 0.0
    mean_knn = sum(knn_overlaps) / len(knn_overlaps) if knn_overlaps else 0.0
    lift = (mean_knn - mean_random) if mean_random > 0 else (mean_knn if mean_knn > 0 else 0.0)

    recommendation = "ADOPT" if lift > 0.02 else ("REJECT_OVERLAP" if lift <= 0 else "INSUFFICIENT_DATA")
    return {
        "status": "COMPLETE",
        "artists_with_vectors": len(vectors),
        "usable_artists": len(usable),
        "k": k,
        "mean_knn_peer_overlap": round(mean_knn, 4),
        "mean_random_peer_overlap": round(mean_random, 4),
        "overlap_lift": round(lift, 4),
        "recommendation": recommendation,
        "reason": (
            "KNN overlaps co-billed peers more than random (retrieval works)"
            if recommendation == "ADOPT"
            else "KNN shows no lift over random on co-billed peers"
        ),
    }


def run_pilot(conn, **kwargs: Any) -> dict[str, Any]:
    """Entry point for the pilot script / tests."""
    return evaluate(conn, **kwargs)
