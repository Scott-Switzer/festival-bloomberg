"""P1/P2 — Affinity-policy sensitivity study on the staged 1% pilot data.

Uses /tmp/lb_pilot_matched.parquet (14.3M matched rows) — NO rescan of the 205 GB
corpus. Answers whether TOP_* per-listener-degree cap is analytically defensible,
not just a memory workaround.

Questions answered:

  P2  — listener-degree distribution and where pair mass comes from.
  P1  — audience-affinity edges under:
            top-K per listener ∈ {10, 25, 50}
            min shared listeners ∈ {3, 5, 10}
        Reporting for each config:
            candidate pairs, persisted edges, artists represented,
            degree distribution (median/P75/P90/P99), median shared listeners,
            top-100 strongest edges,
            and top-20-peer stability for a deterministic sample of 500 artists.

Policy note: top-K is applied AFTER global per-listener listen-count ranking
(never per source shard), so rankings are globally correct.

Memory-bounded: DuckDB PRAGMA memory_limit + temp_directory spill so it does not
contend with the running Wikidata scan on this 8 GB Mac.

Outputs:
    control/lake/listenbrainz_sensitivity_summary.json
    control/lake/listenbrainz_sensitivity/edges_kN.parquet (per top-K, shared>=3)
"""

from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path

import duckdb

SCRATCH = Path("/tmp/lb_pilot_matched.parquet")
OUT_DIR = Path("control/lake/listenbrainz_sensitivity")
SPILL = Path("/tmp/lb_sens_spill")
TOP_KS = [10, 25, 50]
THRESHOLDS = [3, 5, 10]
SAMPLE_SIZE = 500

MEM_LIMIT = "2.5GB"


def build_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"PRAGMA memory_limit='{MEM_LIMIT}'")
    SPILL.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory='{SPILL}'")
    con.execute(f"SET threads=2")  # leave CPU for Wikidata
    return con


def main() -> None:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = build_con()
    con.execute("CREATE TABLE matched AS SELECT * FROM read_parquet(?)", [str(SCRATCH)])
    n_rows = con.execute("SELECT COUNT(*) FROM matched").fetchone()[0]
    print(f"matched rows: {n_rows:,}", flush=True)

    # ---- P2: listener-degree distribution + pair mass ----
    print("== P2: listener-degree distribution ==", flush=True)
    deg = con.execute("""
        WITH la AS (SELECT listener_key, artist_key, COUNT(*) AS listens
                    FROM matched GROUP BY 1, 2),
        d AS (SELECT listener_key, COUNT(*) AS k, SUM(listens) AS total
              FROM la GROUP BY 1)
        SELECT approx_quantile(k, 0.50), approx_quantile(k, 0.75),
               approx_quantile(k, 0.90), approx_quantile(k, 0.95),
               approx_quantile(k, 0.99), max(k)
        FROM d
    """).fetchone()
    p2_degree = {
        "matched_artists_per_listener": {
            "p50": round(deg[0], 2), "p75": round(deg[1], 2), "p90": round(deg[2], 2),
            "p95": round(deg[3], 2), "p99": round(deg[4], 2), "max": deg[5],
        }
    }
    print(f"  matched-artists/listener: p50={deg[0]} p75={deg[1]} p90={deg[2]} "
          f"p95={deg[3]} p99={deg[4]} max={deg[5]}", flush=True)

    listen_p = con.execute("""
        WITH la AS (SELECT listener_key, COUNT(*) AS listens
                    FROM matched GROUP BY 1)
        SELECT approx_quantile(listens, 0.5), approx_quantile(listens, 0.9),
               approx_quantile(listens, 0.99), max(listens) FROM la
    """).fetchone()
    p2_degree["listens_per_listener"] = {
        "p50": round(listen_p[0], 1), "p90": round(listen_p[1], 1),
        "p99": round(listen_p[2], 1), "max": listen_p[3],
    }

    pair_mass = con.execute("""
        WITH la AS (SELECT DISTINCT listener_key, artist_key FROM matched),
        d AS (SELECT listener_key, COUNT(*) AS k FROM la GROUP BY 1)
        SELECT
          SUM(CASE WHEN k BETWEEN 1 AND 5 THEN k*(k-1)/2 ELSE 0 END) AS b1_5,
          SUM(CASE WHEN k BETWEEN 6 AND 10 THEN k*(k-1)/2 ELSE 0 END) AS b6_10,
          SUM(CASE WHEN k BETWEEN 11 AND 25 THEN k*(k-1)/2 ELSE 0 END) AS b11_25,
          SUM(CASE WHEN k BETWEEN 26 AND 50 THEN k*(k-1)/2 ELSE 0 END) AS b26_50,
          SUM(CASE WHEN k BETWEEN 51 AND 100 THEN k*(k-1)/2 ELSE 0 END) AS b51_100,
          SUM(CASE WHEN k > 100 THEN k*(k-1)/2 ELSE 0 END) AS b100p
        FROM d
    """).fetchone()
    p2_degree["pair_mass_by_listener_degree"] = {
        "1-5": int(pair_mass[0] or 0), "6-10": int(pair_mass[1] or 0),
        "11-25": int(pair_mass[2] or 0), "26-50": int(pair_mass[3] or 0),
        "51-100": int(pair_mass[4] or 0), "100+": int(pair_mass[5] or 0),
        "total": int(sum(x or 0 for x in pair_mass)),
    }
    print(f"  pair mass: {p2_degree['pair_mass_by_listener_degree']}", flush=True)

    # artist listener totals + global listener total (for jaccard/cosine/lift)
    con.execute("""
        CREATE OR REPLACE TABLE artist_totals AS
        SELECT artist_key, COUNT(DISTINCT listener_key) AS listeners
        FROM matched GROUP BY 1
    """)
    total_listeners = con.execute(
        "SELECT COUNT(DISTINCT listener_key) FROM matched").fetchone()[0]

    sample = con.execute(
        "SELECT DISTINCT artist_key FROM matched").fetchall()
    # Python's hash is process-randomized; use a stable digest so the reported
    # peer-stability sample is reproducible across machines and reruns.
    sample = sorted(
        {r[0] for r in sample},
        key=lambda k: hashlib.sha256(str(k).encode("utf-8")).hexdigest(),
    )[:SAMPLE_SIZE]
    print(f"  peer-stability sample: {len(sample)} artists", flush=True)

    # ---- P1: affinity edges per top-K ----
    summary = {"p2_listener_policy": p2_degree, "configs": {}, "peer_stability": {},
               "top100_edges": {}}
    for k in TOP_KS:
        print(f"== top-{k} affinity ==", flush=True)
        edge_path = OUT_DIR / f"edges_k{k}.parquet"
        pair_est = con.execute("""
            WITH la AS (SELECT listener_key, artist_key, COUNT(*) AS listens
                        FROM matched GROUP BY 1, 2),
            r AS (SELECT listener_key, artist_key,
                         ROW_NUMBER() OVER (PARTITION BY listener_key
                                            ORDER BY listens DESC, artist_key) AS rn
                  FROM la)
            SELECT COUNT(*) FROM r WHERE rn <= ?
        """, [k]).fetchone()[0]
        con.execute(f"""
            COPY (
              WITH la AS (SELECT listener_key, artist_key, COUNT(*) AS listens
                          FROM matched GROUP BY 1, 2),
              r AS (SELECT listener_key, artist_key,
                           ROW_NUMBER() OVER (PARTITION BY listener_key
                                              ORDER BY listens DESC, artist_key) AS rn
                    FROM la),
              b AS (SELECT listener_key, artist_key FROM r WHERE rn <= {k}),
              pairs AS (
                  SELECT a.artist_key AS a, b2.artist_key AS b, COUNT(*) AS sh
                  FROM b a JOIN b b2 ON a.listener_key = b2.listener_key
                       AND a.artist_key < b2.artist_key
                  GROUP BY 1, 2 HAVING COUNT(*) >= 3
              )
              SELECT p.a AS artist_key_a, p.b AS artist_key_b, p.sh AS shared_listeners,
                     ROUND(p.sh::DOUBLE / (t1.listeners + t2.listeners - p.sh), 5) AS jaccard,
                     ROUND(p.sh::DOUBLE / SQRT(t1.listeners * t2.listeners), 5) AS cosine,
                     ROUND(p.sh::DOUBLE / ((t1.listeners * t2.listeners)::DOUBLE / {total_listeners}), 3) AS lift,
                     ROUND(p.sh::DOUBLE / GREATEST(t1.listeners, t2.listeners), 5) AS overlap
              FROM pairs p
              JOIN artist_totals t1 ON t1.artist_key = p.a
              JOIN artist_totals t2 ON t2.artist_key = p.b
            ) TO '{edge_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        con.execute(f"CREATE OR REPLACE TABLE edges AS SELECT * FROM read_parquet('{edge_path}')")
        n_all = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        print(f"  edges (shared>=3): {n_all:,}  (candidate bounded pairs: {pair_est:,})",
              flush=True)

        # threshold stats
        th_stats = {}
        for th in THRESHOLDS:
            r = con.execute("""
                WITH e AS (SELECT * FROM edges WHERE shared_listeners >= ?),
                d AS (SELECT artist_key_a AS a FROM e)  -- degree: count edges per endpoint
                SELECT (SELECT COUNT(*) FROM e) AS edges,
                       (SELECT COUNT(DISTINCT artist_key_a) FROM e) AS arts
            """, [th]).fetchone()
            deg_dist = con.execute("""
                WITH e AS (SELECT * FROM edges WHERE shared_listeners >= ?),
                deg AS (SELECT a, COUNT(*) AS d FROM (
                          SELECT artist_key_a AS a FROM e
                          UNION ALL
                          SELECT artist_key_b AS a FROM e)
                        GROUP BY a)
                SELECT approx_quantile(d, 0.50), approx_quantile(d, 0.75),
                       approx_quantile(d, 0.90), approx_quantile(d, 0.99), max(d)
                FROM deg
            """, [th]).fetchone()
            med_shared = con.execute(
                "SELECT approx_quantile(shared_listeners, 0.5) FROM edges WHERE shared_listeners >= ?",
                [th]).fetchone()[0]
            artists = con.execute(
                "SELECT COUNT(DISTINCT artist_key_a) + 0 FROM edges WHERE shared_listeners >= ?",
                [th]).fetchone()[0]
            # distinct artists with >=1 edge (either endpoint)
            artists_d = con.execute(f"""
                SELECT COUNT(DISTINCT a) FROM (
                  SELECT artist_key_a AS a FROM edges WHERE shared_listeners >= {th}
                  UNION SELECT artist_key_b FROM edges WHERE shared_listeners >= {th})
            """).fetchone()[0]
            th_stats[th] = {
                "persisted_edges": int(deg_dist and r[0] or 0),
                "artists_represented": int(artists_d),
                "degree_median": round(deg_dist[0] or 0, 1),
                "degree_p75": round(deg_dist[1] or 0, 1),
                "degree_p90": round(deg_dist[2] or 0, 1),
                "degree_p99": round(deg_dist[3] or 0, 1),
                "degree_max": int(deg_dist[4] or 0),
                "median_shared_listeners": round(med_shared, 1),
            }
            print(f"    threshold>={th}: edges={th_stats[th]['persisted_edges']:,} "
                  f"artists={th_stats[th]['artists_represented']:,} "
                  f"deg p90={th_stats[th]['degree_p90']}", flush=True)
        summary["configs"][k] = {
            "candidate_bounded_pairs": int(pair_est), "thresholds": th_stats,
        }

        # top-100 strongest edges for this config (by shared, tie-break jaccard)
        top = con.execute("""
            SELECT artist_key_a, artist_key_b, shared_listeners, jaccard, cosine, lift
            FROM edges ORDER BY shared_listeners DESC, jaccard DESC LIMIT 100
        """).fetchall()
        summary["top100_edges"][k] = [
            {"a": r[0], "b": r[1], "shared": int(r[2]), "jaccard": r[3],
             "cosine": r[4], "lift": r[5]} for r in top]

    # ---- P1: peer-set stability across configs (top-20 peers per artist) ----
    print("== peer-set stability (top-20 peers per sampled artist) ==", flush=True)
    peers = {}
    for k in TOP_KS:
        con.execute("CREATE OR REPLACE TABLE e AS SELECT * FROM read_parquet(?)",
                    [str(OUT_DIR / f"edges_k{k}.parquet")])
        rows = con.execute("""
            WITH r AS (
                SELECT artist_key_a AS a, artist_key_b AS b,
                       ROW_NUMBER() OVER (PARTITION BY artist_key_a
                                          ORDER BY shared_listeners DESC, jaccard DESC, b) AS rn
                FROM e)
            SELECT a, b, rn FROM r WHERE rn <= 20
            UNION ALL
            SELECT artist_key_b AS a, artist_key_a AS b,
                   ROW_NUMBER() OVER (PARTITION BY artist_key_b
                                      ORDER BY shared_listeners DESC, jaccard DESC, artist_key_a) AS rn
            FROM e
            QUALIFY rn <= 20
        """).fetchall()
        d = {}
        for a, b, rn in rows:
            d.setdefault(a, {})[b] = rn
        peers[k] = d
    # per sampled artist, overlap of top-20 peer sets between configs
    overlap_stats = {}
    for (ka, kb) in [(10, 25), (25, 50), (10, 50)]:
        overlaps = []
        for a in sample:
            pa = set(peers[ka].get(a, {}).keys())
            pb = set(peers[kb].get(a, {}).keys())
            if not pa and not pb:
                continue
            inter = len(pa & pb)
            union = len(pa | pb)
            overlaps.append(inter / union if union else 1.0)
        if overlaps:
            overlaps.sort()
            n = len(overlaps)
            overlap_stats[f"top{kb}_vs_top{ka}"] = {
                "p25": round(overlaps[n // 4], 3),
                "p50": round(overlaps[n // 2], 3),
                "p75": round(overlaps[(3 * n) // 4], 3),
                "mean": round(sum(overlaps) / n, 3),
                "artists_sampled": n,
            }
            print(f"  top-{kb} vs top-{ka}: p50 overlap={overlap_stats[f'top{kb}_vs_top{ka}']['p50']}",
                  flush=True)
    summary["peer_stability"] = overlap_stats

    summary["runtime_seconds"] = round(time.time() - t0, 1)
    summary["matched_rows_used"] = n_rows
    summary["source"] = "listenbrainz pilot (staged matched rows)"
    if Path("control/lake/lb_scan_phase.json").exists():
        try:
            scan_phase = json.loads(Path("control/lake/lb_scan_phase.json").read_text())
            summary["source"] = (
                f"listenbrainz pilot ({scan_phase.get('shards_selected')}"
                f"/{scan_phase.get('shards_total')} shards)"
            )
        except (OSError, ValueError, TypeError):
            pass
    summary["memory_limit"] = MEM_LIMIT

    out = Path("control/lake/listenbrainz_sensitivity_summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\nreport → {out}")
    con.close()


if __name__ == "__main__":
    main()
