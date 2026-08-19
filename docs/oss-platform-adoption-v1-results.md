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

## 2. Splink — SHADOW QA complete → NAME-ONLY NOT_ADOPTED

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

**Verdict: `SPLINK_NAME_ONLY = NOT_ADOPTED`.**

This is a scoped negative result, NOT a verdict on Splink as a tool. Splink's
own documentation states it is designed for linkage over multiple independent
fields and is not intended for single name/bag-of-words columns. The result
here confirms exactly that: fuzzy string similarity *cannot* distinguish
"Beyoncé"→"Beyonce" (same person, must merge) from "The Killers"→"The
Kellers" (different people, must not merge). That is precisely the
zero-false-positive doctrine the deterministic resolver enforces via
exact external ID → exact name → exact alias → normalized exact → AMBIGUOUS.

Re-evaluation remains open: if a future panel provides name + geography +
external IDs + artist type + lifespan + disambiguation + label metadata,
Splink could be retried as a **shadow** candidate generator. It would still
never be an auto-merge authority. For the current name-only problem it is
rejected.

---

## 3. Dagster — OFFLINE PARITY PASS → REJECTED_FOR_NOW

Dependency added (experiment-only): `dagster==1.13.18` (+ transitive).

A real offline A/B was run via `scripts/oss_dagster_parity.py`:

- One frozen, realistic Ticketmaster Discovery search fixture (2 markets,
  1 event each).
- Two fresh temporary DuckDB warehouses from identical migrations.
- **Legacy** `oa.live_data_activation._run_ticketmaster` (now injectable
  transport) vs a **Dagster asset** calling the SAME function, both fed an
  identical scripted `FakeTransport`. **No network, no live warehouse, no
  quota.**
- Semantic content compared by deterministic SHA-256 digest (event fields,
  acquisition-run fields; wall-clock `retrieved_at`/`knowledge_time`/`run_id`
  are correctly excluded because retrieval time IS when you fetched it).

Result:

```
legacy  digest = 7aae197b841587b30578bf6db2e13d24eae4b5d19144922c2ad693c09b79fdb5
dagster digest = 7aae197b841587b30578bf6db2e13d24eae4b5d19144922c2ad693c09b79fdb5
SEMANTIC_EQUIVALENCE = PASS
idempotency: 2nd run adds 2 snapshots (new retrieval), 0 new distinct events
```

**Verdict: `DAGSTER_PILOT = REJECTED_FOR_NOW`** (parity PASS, adoption deferred).

Parity proves the wrapper is *semantically safe*. But adoption is judged on
whether Dagster "meaningfully improves orchestration" — and for a single-user
local product with exactly one recurring workflow, it does not. The Festival
run/phase ledgers (`audit.provider_acquisition_runs`, `audit.pipeline_phase_runs`)
already provide idempotency, provenance, and freshness. Dagster would add a
scheduler + event log + IO-manager layer (~200 MB of dependencies) that
*duplicates* those ledgers without replacing any bespoke orchestration burden.
Revisit only when the system has multiple interdependent recurring jobs and a
real scheduling/retry problem.

---

## 4. OpenLineage — FILE PROOF PASS → REJECTED_FOR_NOW

Dependency added (experiment-only): `openlineage-python==1.52.0`.

`scripts/oss_openlineage_proof.py` emits START + COMPLETE RunEvents via the
local `FileTransport` (no deployed backend) with Festival facets:
`sourcePolicyStatus`, `commercialUseStatus`, `evidenceClass`,
`knowledgeTimeSemantics`, `pitCutoff`, `parserVersion`, `softwareVersion`,
`identityResolutionVersion`, `inputFingerprint`,
`providerAcquisitionRunId`. Output verified: 2 events, facets survive
serialization.

**Verdict: `OPENLINEAGE = REJECTED_FOR_NOW`** — the mechanism works and is
spec-compliant, but there is no orchestrator to instrument (Dagster deferred).
The audit/evidence model already records lineage; OpenLineage becomes valuable
only if/when a multi-job orchestrator is adopted.

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
| Splink shadow QA | ✅ decisive `SPLINK_NAME_ONLY = NOT_ADOPTED` |
| Dagster offline parity | ✅ parity PASS → **REJECTED_FOR_NOW** (no orchestration win) |
| OpenLineage file proof | ✅ proof PASS → **REJECTED_FOR_NOW** (no orchestrator to instrument) |
| Crawlee | ❌ REJECT_FOR_NOW (no target source) |
| dlt | ❌ REJECT_FOR_NOW (no custom LOC to remove) |
| H3 | ⏸ DEFERRED (design documented) |

New dependencies (experiment/dev only, NOT in `requirements.txt`):
`memray==1.20.0` (profiling), `splink==4.0.16` (shadow experiment),
`dagster==1.13.18`, `openlineage-python==1.52.0` (parity/proof experiments).
None is imported by production code.

One durable production change: `_run_ticketmaster` now accepts an injectable
transport (mirrors the existing `HttpTransport` injection contract), enabling
offline parity/experiment runs. This is a genuine testability improvement, not
an OSS adoption.

**Milestone outcome: this was an EVALUATION, not an adoption.** No commodity
custom code was replaced, because the audit found that (a) the bespoke
infrastructure is thin and already correct, and (b) the two most plausible
adoptions (Splink name linkage, Dagster orchestration) would each add
complexity without reducing a real burden. The correct next label is
**`OSS_PLATFORM_EVALUATION_V1`**: evaluation complete, zero forced adoption.

Standard applied: "less bespoke infrastructure, stronger operational quality,
identical Festival Intelligence semantics." The honest rejections (Crawlee,
dlt, Dagster-for-now, OpenLineage-for-now) and the scoped Splink negative
result are themselves the correct outcome — they prevent replacing explicit
domain semantics with probabilistic/black-box plumbing before the scale
justifies it.
