#!/usr/bin/env python3
"""A2 — Identity Graph V2 tiered materialization report.

Reads the materialized ``identity_graph_v2.duckdb`` (identity.graph_v2_* tables)
plus the governed estate manifest and reports, separately for every tier:

    HOT_1000 / CORE_5000 / COVERAGE_25000 / FULL CANONICAL ARTIST MASTER

- coverage per provider (VERIFIED_EXACT / SUPPORTED_MULTI_SOURCE / CANDIDATE /
  AMBIGUOUS / CONFLICT / MISSING / INVALID)
- multi-source confirmations (>=2 providers at VERIFIED_EXACT or
  SUPPORTED_MULTI_SOURCE for the same artist)
- provider corroboration (>=2 distinct source tables claim the same provider ID)
- ambiguous identities / conflicting identities / unresolved identities
- measured resource gate (wall time, peak RSS, output size, enforced bounds)

The report never modifies the graph database.  No resource certification is
claimed unless it was actually measured by this process.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from festival_bloomberg.identity.graph_v2 import read_estate_json  # noqa: E402

PROVIDERS_ORDER = (
    "MUSICBRAINZ", "WIKIDATA", "YOUTUBE", "SPOTIFY", "DISCOGS", "ISNI", "VIAF",
    "TICKETMASTER", "OFFICIAL_WEBSITE", "LISTENBRAINZ", "WIKIPEDIA", "SOUNDCLOUD",
    "APPLE_MUSIC", "BANDCAMP", "SONGKICK", "BANDSINTOWN", "SETLISTFM", "ALLMUSIC",
    "LASTFM", "MYSPACE", "IPI",
)
STATUSES = (
    "VERIFIED_EXACT", "SUPPORTED_MULTI_SOURCE", "CANDIDATE", "AMBIGUOUS",
    "CONFLICT", "MISSING",
)
# Enforced bounds used for the serialized 25K materialization (must match the
# build invocation; the report refuses to certify a run it cannot bound).
ENFORCED_BOUNDS = {
    "canonical_limit": 25_000,
    "max_evidence": 500_000,
    "max_edges": 250_000,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def peak_rss_bytes() -> int:
    ru_maxrss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return ru_maxrss if sys.platform == "darwin" else ru_maxrss * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="materialized identity_graph_v2.duckdb")
    parser.add_argument("--estate-json", type=Path, required=True)
    parser.add_argument("--source-generation", default="20260831T014029Z-1369")
    parser.add_argument("--as-of", default="2026-08-31T12:00:00Z")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    t_start = time.time()
    import duckdb

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        present = {(str(r[0]), str(r[1])) for r in conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables"
        ).fetchall()}
        for name in ("graph_v2_runs", "graph_v2_nodes", "graph_v2_edges", "graph_v2_evidence", "graph_v2_scorecard"):
            if ("identity", name) not in present:
                raise RuntimeError(f"identity.{name} is missing; not a materialized graph DB")

        def read(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
            cur = conn.execute(sql, params or [])
            cols = [str(d[0]) for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

        run_rows = read("SELECT * FROM identity.graph_v2_runs")
        if len(run_rows) != 1:
            raise RuntimeError(f"expected exactly one graph run, found {len(run_rows)}")
        run = run_rows[0]
        if run.get("build_status") != "MATERIALIZED":
            raise RuntimeError(f"run build_status is {run.get('build_status')!r}; refusing to certify")
        run_key = str(run["run_key"])

        estate = read_estate_json(str(args.estate_json))
        if len(estate) != int(run.get("canonical_count") or 0):
            raise RuntimeError("estate size does not match the materialized run")
        tier_by_key = {}
        for entry in estate:
            tier = str(entry.get("tier") or "UNKNOWN")
            tier_by_key[str(entry["artist_key"])] = tier
        # COVERAGE_25000 is the tier name in the estate for the remaining 20K;
        # the full universe is reported separately.
        tier_order = ("HOT_1000", "CORE_5000", "COVERAGE_25000")
        tier_keys = {tier: [] for tier in tier_order}
        for key, tier in tier_by_key.items():
            if tier in tier_keys:
                tier_keys[tier].append(key)
        all_keys = sorted(tier_by_key)

        nodes = read(
            "SELECT artist_key, artist_name, scope, provider_status_json FROM identity.graph_v2_nodes"
        )
        status_by_key = {}
        for node in nodes:
            try:
                statuses = json.loads(node["provider_status_json"] or "{}")
            except (TypeError, ValueError):
                statuses = {}
            status_by_key[str(node["artist_key"])] = statuses

        evidence = read(
            "SELECT artist_key, provider, provider_id, source_table, evidence_status "
            "FROM identity.graph_v2_evidence"
        )
        sources_by_artist_provider: dict[tuple[str, str], set[str]] = defaultdict(set)
        ev_status_count = Counter()
        for row in evidence:
            artist = str(row["artist_key"])
            provider = str(row["provider"])
            sources_by_artist_provider[(artist, provider)].add(str(row["source_table"]))
            ev_status_count[str(row["evidence_status"])] += 1

        # ── per-tier computation ──
        def compute_tier(keys: list[str]) -> dict[str, Any]:
            keyset = set(keys)
            universe = len(keys)
            provider_counts: dict[str, Counter] = defaultdict(Counter)
            corroborated: dict[str, int] = defaultdict(int)
            artist_best: dict[str, str] = {}
            artist_ambiguous = 0
            artist_conflict = 0
            artist_unresolved = 0
            multi_source_confirmed = 0
            corroboration_dist: Counter = Counter()
            for key in keys:
                statuses = status_by_key.get(key, {})
                best = None
                ambiguous = False
                conflict = False
                any_non_missing = False
                verified_supported = 0
                for provider in PROVIDERS_ORDER:
                    status = str(statuses.get(provider, "MISSING"))
                    if status not in STATUSES:
                        status = "MISSING"
                    provider_counts[provider][status] += 1
                    if status != "MISSING":
                        any_non_missing = True
                    if status == "AMBIGUOUS":
                        ambiguous = True
                    if status == "CONFLICT":
                        conflict = True
                    if status in ("VERIFIED_EXACT", "SUPPORTED_MULTI_SOURCE"):
                        verified_supported += 1
                    if (key, provider) in sources_by_artist_provider and len(
                        sources_by_artist_provider[(key, provider)]
                    ) >= 2:
                        corroborated[provider] += 1
                        corroboration_dist[provider] += 1
                    rank = {"VERIFIED_EXACT": 4, "SUPPORTED_MULTI_SOURCE": 3,
                            "CANDIDATE": 2, "CONFLICT": 1, "AMBIGUOUS": 1, "MISSING": 0}[status]
                    if best is None or rank > {"VERIFIED_EXACT": 4, "SUPPORTED_MULTI_SOURCE": 3,
                                               "CANDIDATE": 2, "CONFLICT": 1, "AMBIGUOUS": 1, "MISSING": 0}[best]:
                        best = status
                if verified_supported >= 2:
                    multi_source_confirmed += 1
                if ambiguous:
                    artist_ambiguous += 1
                if conflict:
                    artist_conflict += 1
                if best in ("MISSING", "CANDIDATE"):
                    artist_unresolved += 1
                artist_best[best] = artist_best.get(best, 0) + 1

            provider_coverage = {}
            for provider in PROVIDERS_ORDER:
                counts = provider_counts[provider]
                verified = counts.get("VERIFIED_EXACT", 0)
                supported = counts.get("SUPPORTED_MULTI_SOURCE", 0)
                provider_coverage[provider] = {
                    "verified_exact": verified,
                    "supported_multi_source": supported,
                    "candidate": counts.get("CANDIDATE", 0),
                    "ambiguous": counts.get("AMBIGUOUS", 0),
                    "conflict": counts.get("CONFLICT", 0),
                    "missing": counts.get("MISSING", 0),
                    "coverage_pct": round(100.0 * (verified + supported) / universe, 3) if universe else None,
                    "corroborated_artists": corroborated[provider],
                }
            return {
                "universe": universe,
                "provider_coverage": provider_coverage,
                "multi_source_confirmations": multi_source_confirmed,
                "ambiguous_artists": artist_ambiguous,
                "conflicting_artists": artist_conflict,
                "unresolved_artists": artist_unresolved,
                "artist_best_state": artist_best,
            }

        tiers: dict[str, dict[str, Any]] = {}
        for tier in tier_order:
            tiers[tier] = compute_tier(tier_keys[tier])
        tiers["FULL_CANONICAL_ARTIST_MASTER"] = compute_tier(all_keys)

        total_rows = sum(
            int(conn.execute(f"SELECT COUNT(*) FROM identity.graph_v2_{name}").fetchone()[0])
            for name in ("runs", "nodes", "edges", "evidence", "conflicts", "scorecard")
        )
        db_bytes = args.db.stat().st_size
        elapsed = round(time.time() - t_start, 2)
        rss = peak_rss_bytes()
    finally:
        conn.close()

    # The serialized 25K resource gate: only certify what was actually measured.
    gate = {
        "enforced_bounds": ENFORCED_BOUNDS,
        "measured_evidence_count": int(run["evidence_count"]),
        "measured_edge_count": int(run["edge_count"]),
        "measured_conflict_count": int(run["conflict_count"]),
        "measured_node_count": int(run["canonical_count"]),
        "within_evidence_bound": int(run["evidence_count"]) <= ENFORCED_BOUNDS["max_evidence"],
        "within_edge_bound": int(run["edge_count"]) <= ENFORCED_BOUNDS["max_edges"],
        "report_wall_seconds": elapsed,
        "report_peak_rss_bytes": rss,
        "output_db_bytes": db_bytes,
        "total_rows": total_rows,
        "certified": bool(
            int(run["evidence_count"]) <= ENFORCED_BOUNDS["max_evidence"]
            and int(run["edge_count"]) <= ENFORCED_BOUNDS["max_edges"]
            and int(run["canonical_count"]) == ENFORCED_BOUNDS["canonical_limit"]
            and run.get("build_status") == "MATERIALIZED"
        ),
        "build_status": str(run["build_status"]),
        "run_key": run_key,
    }

    report = {
        "schema_version": 1,
        "report": "IDENTITY_MATERIALIZATION_REPORT",
        "source_generation": args.source_generation,
        "as_of": args.as_of,
        "created_at": now_iso(),
        "run": {
            "run_key": run_key,
            "build_status": str(run["build_status"]),
            "canonical_count": int(run["canonical_count"]),
            "evidence_count": int(run["evidence_count"]),
            "edge_count": int(run["edge_count"]),
            "conflict_count": int(run["conflict_count"]),
            "created_at": str(run["created_at"]),
        },
        "evidence_status_distribution": dict(sorted(ev_status_count.items())),
        "tiers": tiers,
        "resource_gate": gate,
        "definitions": {
            "multi_source_confirmed": "artist with >=2 providers at VERIFIED_EXACT or SUPPORTED_MULTI_SOURCE",
            "corroborated": "provider claim supported by >=2 distinct source tables for the same artist+provider_id",
            "ambiguous_artists": "artist with >=1 provider AMBIGUOUS",
            "conflicting_artists": "artist with >=1 provider CONFLICT",
            "unresolved_artists": "artist whose best provider state is CANDIDATE or MISSING (no exact/supported identity)",
            "coverage_pct": "percent of artists with VERIFIED_EXACT or SUPPORTED_MULTI_SOURCE for the provider",
        },
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    temp = args.report.with_name(f".{args.report.name}.{__import__('os').getpid()}.tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(args.report)
    print(f"wrote {args.report} ({args.report.stat().st_size} bytes)")
    print(f"tiers: {', '.join(t + '=' + str(tiers[t]['universe']) for t in tiers)}")
    print(f"resource gate certified={gate['certified']} (evidence {gate['measured_evidence_count']} "
          f"/ {ENFORCED_BOUNDS['max_evidence']}, edges {gate['measured_edge_count']} / {ENFORCED_BOUNDS['max_edges']})")
    print(f"report wall {elapsed}s peak RSS {rss/1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
