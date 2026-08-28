"""Factor coverage density for ARTIST_SECURITY_25000.

For every artist in the universe, count real observations by source/family,
then report P10/P25/P50/P75/P90 distributions. Sparse securities are never
hidden behind averages.
"""
from __future__ import annotations

from datetime import date
from typing import Any


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int((p / 100.0) * (len(sorted_vals) - 1))
    return sorted_vals[idx]


def compute_factor_coverage(conn, *, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    artists = conn.execute(
        "SELECT artist_key FROM security.artist_security_universe_25000 ORDER BY artist_key"
    ).fetchall()
    keys = [r[0] for r in artists]
    n = len(keys)

    # Per-artist counts by family/source.
    factor_rows = conn.execute(
        "SELECT artist_key, factor_family, COUNT(*) FROM metrics.artist_factor_observations "
        "WHERE artist_key IN (SELECT artist_key FROM security.artist_security_universe_25000) "
        "GROUP BY artist_key, factor_family"
    ).fetchall()
    factor_total = {}
    factor_by_family = {}
    for k, fam, cnt in factor_rows:
        factor_total[k] = factor_total.get(k, 0) + cnt
        factor_by_family[(k, fam)] = factor_by_family.get((k, fam), 0) + cnt
    attention_days = dict(conn.execute(
        "SELECT artist_key, COUNT(DISTINCT period_start) FROM metrics.artist_attention_observations "
        "WHERE artist_key IN (SELECT artist_key FROM security.artist_security_universe_25000) AND status='ok' "
        "GROUP BY artist_key"
    ).fetchall())
    yt_counts = dict(conn.execute(
        "SELECT artist_key, COUNT(*) FROM metrics.artist_attention_observations "
        "WHERE artist_key IN (SELECT artist_key FROM security.artist_security_universe_25000) "
        "AND metric_kind LIKE 'YT_%' GROUP BY artist_key"
    ).fetchall())
    lb_counts = dict(conn.execute(
        "SELECT artist_key, COUNT(*) FROM metrics.artist_attention_observations "
        "WHERE artist_key IN (SELECT artist_key FROM security.artist_security_universe_25000) "
        "AND metric_kind LIKE 'LISTENBRAINZ_%' GROUP BY artist_key"
    ).fetchall())
    live_counts = dict(conn.execute(
        "SELECT artist_key, COUNT(*) FROM metrics.artist_performance_observations "
        "WHERE artist_key IN (SELECT artist_key FROM security.artist_security_universe_25000) "
        "GROUP BY artist_key"
    ).fetchall())
    market_counts = dict(conn.execute(
        "SELECT artist_key, COUNT(*) FROM asm.artist_market_security_v1 "
        "WHERE artist_key IN (SELECT artist_key FROM security.artist_security_universe_25000) "
        "GROUP BY artist_key"
    ).fetchall())
    id_counts = dict(conn.execute(
        "SELECT entity_key, COUNT(*) FROM core.entity_external_ids "
        "WHERE entity_type='artist' AND entity_key IN (SELECT artist_key FROM security.artist_security_universe_25000) "
        "GROUP BY entity_key"
    ).fetchall())
    event_counts = dict(conn.execute(
        "SELECT a.artist_key, COUNT(*) FROM core.event_performers ep "
        "JOIN core.artists a ON a.musicbrainz_id = ep.artist_mbid "
        "JOIN security.artist_security_universe_25000 u ON u.artist_key = a.artist_key "
        "GROUP BY a.artist_key"
    ).fetchall())

    def dist(getter):
        vals = sorted(float(getter(k) or 0) for k in keys)
        return {
            "p10": _percentile(vals, 10), "p25": _percentile(vals, 25),
            "p50": _percentile(vals, 50), "p75": _percentile(vals, 75),
            "p90": _percentile(vals, 90), "mean": round(sum(vals) / max(n, 1), 2),
            "nonzero_artists": sum(1 for v in vals if v > 0),
        }

    coverage = {
        "as_of": as_of.isoformat(), "universe_size": n,
        "factor_observations": dist(lambda k: factor_total.get(k, 0)),
        "attention_history_days": dist(lambda k: attention_days.get(k, 0)),
        "youtube_observations": dist(lambda k: yt_counts.get(k, 0)),
        "listenbrainz_observations": dist(lambda k: lb_counts.get(k, 0)),
        "live_events": dist(lambda k: live_counts.get(k, 0)),
        "markets": dist(lambda k: market_counts.get(k, 0)),
        "identity_sources": dist(lambda k: id_counts.get(k, 0)),
        "mb_event_performances": dist(lambda k: event_counts.get(k, 0)),
    }
    return coverage
