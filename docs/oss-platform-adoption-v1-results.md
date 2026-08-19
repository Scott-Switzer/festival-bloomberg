# OSS Platform Adoption V1 — Results

Branch: `feat/oss-platform-adoption-v1`
Base: `main @ 94dda61` (PR #31 merged, post-merge CI green)

Companion to `docs/oss-platform-adoption-v1.md` (the inventory/classification).
This file records what was actually **run** versus planned.

---

## 1. Memray — ADOPTED (profiling), results recorded

Dependency added: `memray==1.20.0` (dev/profiling only, no runtime import).

Profiled two representative Python-heavy paths (the 4.87M-term search index
build was excluded because it is already set-based SQL
`INSERT … SELECT … json_each`, not a Python loop):

1. `normalize_name` (called once per name across bootstrap/resolution),
   exercised at 1M calls over a 20-name corpus including diacritics,
   punctuation, and leading-"the" cases.
2. `persist_canonical_artist` + `persist_credit_aliases` — the per-row
   SELECT-then-INSERT bootstrap loop, exercised over 5,000 synthetic MBIDs
   against an in-memory DuckDB (the live 5.3 GB warehouse is write-locked by
   the long-running terminal server, so the loop was profiled on the same
   schema).

Measurements (memray `stats` / `summary`):

| Metric | Value |
|---|---|
| Peak memory (whole workload) | **8.09 MB** |
| Total transient allocations | 5,272,305 |
| Total memory churn | 3.504 GB (cumulative, reclaimed) |
| Dominant allocation | `import duckdb` (~68% of total — one-time startup) |
| Workload own-memory share | ~31% of peak |

Findings:

- **No leak, bounded peak (~8 MB)** even at 1M normalize calls + 5,000
  bootstrap inserts. The identity/bootstrap paths are *latency-bound*, not
  memory-bound.
- **`normalize_name` is the remaining pure-Python CPU hotspot** (two regex
  subs + a leading-"the" strip per call). It is called in Python loops
  (`bootstrap_canonical_artists`, `build_rows`, alias extraction). If a
  future 2M+ bootstrap ever becomes slow, the fix is to push normalization
  into DuckDB (a `normalize_name` UDF or SQL `regexp_replace`) or pre-compile
  the regex — **not** to add more infrastructure.
- **The per-row `persist_canonical_artist` loop** is the classic
  "Python loop → SELECT → INSERT" antipattern the goal flags, but it is
  one-time, idempotent, and already runs to completion (113k+ artists). No
  optimization is justified until a re-run becomes a measured bottleneck.

Recommendation: **no optimization this milestone** (no confirmed hotspot that
justifies the correctness risk). Memray is now available for future
regressions.

---

## 2. Splink — SHADOW QA complete → NOT_ADOPTED

Dependency added (experiment-only): `splink==4.0.16` (+ igraph, altair, narwhals).

Experiment: a labeled 58-record identity universe — 14 true identity groups
with case/diacritic/punctuation variants ("Beyoncé"/"Beyonce",
"Die Ärzte"/"Die Aerzte", "AC/DC"/"AC DC") plus 16 decoys engineered to be
fuzzy-similar but *distinct* identities ("Queen"/"Queen Tribute",
"The Killers"/"The Kellers", "Drake"/"Drakes", "Ed Sheeran"/"Ed Sheeran
Tribute", "Metallica"/"Metallica Tribute Band", "Coldplay"/"Coldplay Cover
Band"). Ran Splink's `DuckDBAPI` backend with first-letter blocking and
`JaroWinklerAtThresholds` at 0.8/0.9/0.95, then evaluated candidate pairs
against ground-truth identity groups.

Result (Splink's own Jaro-Winkler comparison output):

| Threshold | True-variant pairs | Cross-group (false) pairs | Candidate precision |
|---|---|---|---|
| ≥ 0.80 | 26 | 24 | 0.52 |
| ≥ 0.90 | 26 | 24 | 0.52 |
| ≥ 0.95 | 26 | 24 | 0.52 |

**There is no clean threshold.** At every similarity level Splink merges
decoys that the deterministic resolver correctly keeps separate:

```
Queen            ↔  Queen Tribute        (tribute act)
The Killers      ↔  The Kellers          (near-name)
Drake            ↔  Drakes               (different identity)
Ed Sheeran       ↔  Ed Sheeran Tribute   (tribute act)
Metallica        ↔  Metallica Tribute Band
Coldplay         ↔  Coldplay Cover Band
```

**Verdict: `SPLINK_SHADOW = NOT_ADOPTED`.**

Why this matters: fuzzy string similarity *cannot* distinguish
"Beyoncé"→"Beyonce" (same person, must merge) from "The Killers"→"The
Kellers" (different people, must not merge). That is precisely the
zero-false-positive doctrine the deterministic resolver enforces via
exact external ID → exact name → exact alias → normalized exact → AMBIGUOUS.

Splink could only become useful as a candidate generator if blocked on
**multi-signal** keys (external IDs, area, disambiguation) — which the
deterministic resolver already handles explicitly. Name-only probabilistic
linkage is a false-merge risk and is rejected. This is a successful
experimental outcome (a negative result that protects the product).

---

## 3. Dagster — PILOT DEFERRED (plan in inventory §8)

Not installed. A genuine pilot requires:

1. write access to the live warehouse (currently write-locked by the running
   terminal server), and
2. a second real Ticketmaster acquisition for the A/B equivalence test
   (additional provider quota).

The inventory documents the exact contract: Dagster may own scheduling/retry/
dependency execution/freshness; Festival stays authoritative for
`audit.provider_acquisition_runs`, `audit.pipeline_phase_runs`, observations,
claims, `knowledge_time`, PIT, rights, canonical entities, and alerts, with
`Dagster run ID ↔ provider_acquisition_runs.run_id` mapping. The A/B gate is
`SEMANTIC_EQUIVALENCE = PASS` before any expansion.

**Status: `DAGSTER_PILOT = DEFERRED`** (blocker is a write-locked DB + quota,
not a semantic failure). Do not claim parity until the A/B test runs.

---

## 4. OpenLineage — DEFERRED (after Dagster parity)

Not installed. Per inventory §9, it is gated on Dagster equivalence. It emits
lineage *in addition to* the audit/evidence model, never instead of it.

---

## 5. Crawlee — REJECTED FOR NOW (no target source)

No browser-based crawler exists in the repo (all acquisition is API-based via
urllib `HttpTransport`), and no rights-approved scraping source has crawler
plumbing. Adopting Crawlee would mean *adding* a scraping surface, which
contradicts the rights policy. **`CRAWLEE = REJECT_FOR_NOW`** (an honest
rejection, not a failure).

---

## 6. dlt — REJECT_FOR_NOW

Per inventory §7: the "extract" side is already provider-specific and
rights-aware; the "load" side is already DuckDB `INSERT … SELECT`. dlt would
add a normalization layer risking `knowledge_time`/claim semantics for no
custom-LOC reduction. **`DLT = REJECT_FOR_NOW`**.

---

## 7. H3 — DEFERRED (design only)

No code added. The inventory §10 documents the versioned derived-geography
design (`venue_key, lat, lon, coordinate_source, coordinate_confidence,
h3_r5..r8, h3_derivation_version`) and the rule that H3 cells never imply
local demand. Deferred until after the platform pilots; the design is ready.

---

## 8. Net effect this milestone

| Item | Status |
|---|---|
| PR #31 merged, post-merge main CI green | ✅ |
| OSS inventory (moat/commodity/mixed) | ✅ (`oss-platform-adoption-v1.md`) |
| Memray profiling | ✅ real measurements, no hotspot to fix |
| Splink shadow QA | ✅ decisive `NOT_ADOPTED` |
| Dagster pilot | ⏸ DEFERRED (locked DB + quota) |
| OpenLineage | ⏸ DEFERRED (gated on Dagster) |
| Crawlee | ❌ REJECT_FOR_NOW (no target source) |
| dlt | ❌ REJECT_FOR_NOW (no custom LOC to remove) |
| H3 | ⏸ DEFERRED (design documented) |

New dependencies (experiment/dev): `memray==1.20.0` (profiling only),
`splink==4.0.16` + transitive (shadow experiment only). Neither is imported by
production code. The inventory recommends the only *production* adoption that
is currently justified: **`tenacity` for bounded, opt-in retry/backoff**, and
that only after Dagster parity.

Standard applied: "less bespoke infrastructure, stronger operational quality,
identical Festival Intelligence semantics." The two honest rejections (Crawlee,
dlt) and the Splink negative result are themselves the correct outcome — they
prevent replacing explicit domain semantics with probabilistic/black-box
plumbing.
