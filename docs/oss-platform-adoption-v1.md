# OSS Platform Adoption — Inventory V1

Milestone: `OSS_PLATFORM_ADOPTION_V1`
Branch: `feat/oss-platform-adoption-v1`
Base: `main @ 94dda61b91738b43dc1e4df550abbfb080d1e217` (post-merge of PR #31, CI green)

> Doctrine: mature OSS may replace **commodity infrastructure**. It may not
> redefine Festival Intelligence's evidence, temporal, rights, identity, or
> economic semantics. LOC reduction is NOT success if semantics become less
> explicit.

---

## 1. Classification summary

| Category | Subsystem | Approx LOC | Verdict |
|---|---|---|---|
| MOAT | `governance/` (rights/policy profiles) | ~940 | KEEP CUSTOM |
| MOAT | `evidence/` (claim ledger, evidence classes) | ~780 | KEEP CUSTOM |
| MOAT | `flywheel/pit.py` (PIT admissibility) | ~(part of 6.5k) | KEEP CUSTOM |
| MOAT | `flywheel/cutoffs.py`, `evidence_verification.py` | ~(part of 6.5k) | KEEP CUSTOM |
| MOAT | `identity/` (canonical identity, conflict semantics) | ~1,250 | KEEP CUSTOM (candidate gen = OSS pilot) |
| MOAT | `events/` (booking/announcement/onsale distinctions) | ~1,060 | KEEP CUSTOM |
| MOAT | `economics/` (future outcomes/underwriting research) | ~6,590 | KEEP CUSTOM (research, deferred) |
| MOAT | `product/workflow.py` (alerts, acquisition runs) | ~870 | KEEP CUSTOM |
| MOAT | `markets/`, `festivals/`, `entities/` | ~1,150 | KEEP CUSTOM |
| MIXED | `acquisition/providers/*` (provider semantics) | ~5,000 | WRAP CAREFULLY |
| MIXED | `musicbrainz/dumps.py` (streaming ingest + reference mapping) | ~1,250 | WRAP CAREFULLY |
| MIXED | `warehouse/repository.py` (DuckDB lifecycle) | ~2,130 | WRAP CAREFULLY (DuckDB already OSS) |
| MIXED | `terminal/server.py` (HTTP server + routes) | ~380 | WRAP CAREFULLY |
| COMMODITY | `acquisition/transport.py` (urllib HTTP) | ~150 | DEFER (thin, injectable, correct) |
| COMMODITY | retry/backoff (currently absent — providers stop on 429) | 0 | PILOT (`tenacity`) |
| COMMODITY | orchestration/scheduling (custom `oa/*` phase runners) | ~8,680 | PILOT (Dagster wrapper) |
| COMMODITY | pipeline lineage (currently audit tables only) | 0 | PILOT (OpenLineage) |
| COMMODITY | probabilistic linkage candidate gen (currently none) | 0 | PILOT (Splink, shadow only) |
| COMMODITY | memory profiling (currently none) | 0 | ADOPT (Memray, profiling only) |
| COMMODITY | browser-based crawling (currently none — all API via urllib) | 0 | REJECT FOR NOW (no target source) |
| COMMODITY | generic ingestion (dlt) | 0 | REJECT FOR NOW (see §7) |

---

## 2. FESTIVAL MOAT — KEEP CUSTOM (detail)

These subsystems encode the project's core invariants. No OSS project owns these
semantics, and substituting them would silently weaken correctness.

### 2.1 Source rights / policy (`governance/`, `acquisition/policy.py`)
- `governance/policy.py` + `acquisition/policy.py` hold `RightsProfile`,
  `PolicyStatus`, and per-source profiles (CC0, ODbL, ToS-restricted).
- **Why moat:** the "commercial vs research" gate and fail-closed behavior are
  the project's legal/rights posture. An OSS scheduler must NOT own this.
- Verdict: **KEEP CUSTOM**.

### 2.2 Evidence + claims (`evidence/`)
- Claim ledger, evidence classes, source publication vs archive capture time.
- **Why moat:** `knowledge_time`, `evidence_class`, `PIT admissibility` are the
  project's determinism contract.
- Verdict: **KEEP CUSTOM**.

### 2.3 Point-in-time semantics (`flywheel/pit.py`, `cutoffs.py`)
- `_two_snapshot_pit` and cutoff taxonomy. Already fixed once (wall-clock bug);
  this is the highest-sensitivity code in the repo.
- Verdict: **KEEP CUSTOM**. Any change requires exact PIT regression tests.

### 2.4 Canonical identity + conflicts (`identity/`)
- `artist_master.py`, `ticketmaster_resolution.py` enforce:
  - internal `artist_key` (never the MBID),
  - deterministic resolution ladder (external ID → mapping → exact name → alias → normalized → multi-signal → fuzzy candidate),
  - NO LLM auto-merge, `AMBIGUOUS`/`NO_MATCH`/`REJECTED_NON_ARTIST` preserved,
  - external-ID conflict ledger (never silently overwrite).
- **Why moat:** zero-false-positive identity doctrine.
- Verdict: **KEEP CUSTOM** for the ladder and merge rules. The *candidate
  generation* step (fuzzy retrieval) is a bounded OSS pilot candidate (Splink,
  shadow only — §6).

### 2.5 Live-event entity semantics (`events/`, `flywheel/forward_*`)
- booking vs announcement vs onsale vs presale vs status transitions; the
  activity tape derives change events from consecutive acquisition runs.
- Verdict: **KEEP CUSTOM**.

### 2.6 Economics / future outcomes (`economics/`)
- Historical outcome semantics + future underwriting research. Preserved
  negative result: `BASELINE_RESEARCH_V1 = COMPS_SIGNAL_ONLY`.
- Verdict: **KEEP CUSTOM / DEFER**. No predictive modeling in this milestone.

### 2.7 Alert + acquisition-run semantics (`product/workflow.py`)
- `NEW_EVENT`/`PRESALE_DISCOVERED`/`ONSALE_DISCOVERED`/`PRICE_RANGE_CHANGED`/
  status transitions; idempotency via acquisition-run scoping.
- Verdict: **KEEP CUSTOM**.

---

## 3. MIXED — WRAP CAREFULLY

### 3.1 Provider acquisition (`acquisition/providers/*`)
- Each provider has domain-specific parsing (Ticketmaster attractions JSON,
  Spotify 2026 catalog-only fields, NWS forecast generation-time semantics).
- **Commodity part:** HTTP transport, User-Agent, redaction.
- **Moat part:** provider semantics, rights checks, cost accounting.
- Verdict: **WRAP CAREFULLY** — Dagster may wrap `_run_ticketmaster` etc., but
  the provider contract (`AcquisitionProvider` protocol) stays authoritative.

### 3.2 MusicBrainz dump ingest (`musicbrainz/dumps.py`)
- **Commodity part:** xz streaming, bounded transactions, resume offset.
- **Moat part:** which fields become `reference.musicbrainz_artists` vs
  promoted `core.artists`; snapshot provenance.
- Verdict: **WRAP CAREFULLY** — the streaming/resume loop is already correct
  (fixed from `getmembers()` OOM); do not rewrite merely to use Arrow/Parquet.

### 3.3 Warehouse lifecycle (`warehouse/repository.py`)
- DuckDB connection management + migration application. DuckDB is already the
  OSS engine; this layer is thin.
- Verdict: **WRAP CAREFULLY** — keep the single-connection + migration path;
  do not introduce ORMs.

### 3.4 Terminal server (`terminal/server.py`)
- `ThreadingHTTPServer` + route dispatch, serialized DuckDB access (lock added
  in PR #31 acceptance). SPA is static in `apps/terminal/static/`.
- **Commodity part:** HTTP server, JSON routing (could be FastAPI).
- **Moat part:** the read-model/route contract + provenance fields.
- Verdict: **WRAP CAREFULLY / DEFER** — replacing `http.server` with FastAPI is
  possible but the current server is correct and small; no semantic gain. Do
  not churn it in this milestone.

---

## 4. COMMODITY — OSS CANDIDATES (evaluation)

### 4.1 HTTP transport (`acquisition/transport.py`, ~150 LOC)
- Current: `urllib` with secret redaction, injectable for test fakes.
- Candidate: `httpx` / `requests`.
- **Risk:** the injectable-transport + fail-closed fake pattern is load-bearing
  for provider tests. A swap must preserve `HttpResponse` shape and redaction.
- **Benefit:** minimal (urllib is stdlib, no dependency).
- Verdict: **DEFER** — not a bottleneck, already testable.

### 4.2 Retry/backoff (currently absent)
- Current: providers return `RATE_LIMITED` and the runner stops; no automatic
  retry. This is conservative and safe but leaves quota unused.
- Candidate: `tenacity` (small, no new runtime).
- **Risk:** automatic retry could double-spend paid calls or violate provider
  pacing (GDELT `>=1 req/5s`). Must be opt-in, per-provider, with a hard cap
  and rights-aware backoff.
- Verdict: **PILOT** (only after Dagster parity; scope to read-only GETs).

### 4.3 Orchestration (`oa/*`, ~8,680 LOC)
- Current: synchronous phase functions, `audit.provider_acquisition_runs` +
  `audit.pipeline_phase_runs` ledgers.
- Candidate: `dagster`.
- Verdict: **PILOT** on ONE workflow (Ticketmaster forward acquisition) — §8.
  Festival keeps the run/phase ledgers as source of truth.

### 4.4 Lineage (currently audit tables only)
- Candidate: `openlineage-python`.
- Verdict: **PILOT** after Dagster parity — §9.

### 4.5 Probabilistic linkage (currently none)
- Candidate: `splink` (DuckDB backend).
- Verdict: **PILOT, shadow only** against the 59-case MBID fixture — §6. Never
  an auto-merge authority.

### 4.6 Memory profiling (currently none)
- Candidate: `memray`.
- Verdict: **ADOPT** (profiling only; no runtime dependency) — §5.

### 4.7 Browser crawling (currently none)
- All acquisition is API-based via urllib. There is no Selenium/Playwright
  crawler in the repo, and no rights-approved scraping source with crawler
  plumbing.
- Candidate: `crawlee-python`.
- Verdict: **REJECT FOR NOW** — no target source exists; adopting Crawlee
  would mean adding a new scraping surface, which contradicts the rights
  policy. Revisit only if a rights-approved crawler source is added.

### 4.8 Generic ingestion (dlt)
- Candidate: `dlt`.
- Verdict: **REJECT FOR NOW** — see §7.

---

## 5. Memray (ADOPT — profiling only)

Target: profile representative workflows, NOT giant archive re-runs:
- artist search-index build (`intelligence/readmodels.py`)
- artist bootstrap (`identity/artist_master.py`)
- alias + external-ID extraction
- identity collision audit
- `ART` / `TODAY` read models

Memray is a `bloomberg/memray` dev dependency (no runtime cost). It does not
change production semantics.

---

## 6. Splink shadow QA (PILOT — shadow only)

- Splink operates ONLY as a **candidate generator / challenger** for the
  unresolved/ambiguous candidate universe.
- The deterministic ladder in `identity/ticketmaster_resolution.py` remains
  authoritative and runs FIRST.
- Pipeline order:
  1. exact external ID → deterministic resolver (already authoritative)
  2. unresolved candidate universe → Splink score
  3. review / ambiguous candidate (never auto-merge)
- Success question: does Splink recover useful candidates the resolver misses,
  without threatening the **zero-FP** doctrine?
- Both `USEFUL` and `NOT_WORTH_ADOPTING` are valid outcomes.

---

## 7. dlt decision — REJECT FOR NOW

After the Dagster/Crawlee pilots, remaining generic ingestion plumbing is:
- `acquisition/transport.py` (thin, injectable)
- provider `acquire()` methods (domain-specific)
- `musicbrainz/dumps.py` (streaming + reference mapping — domain-specific)

dlt's value is generic extract/load normalization. Here the "extract" side is
already provider-specific and rights-aware; the "load" side is already DuckDB
`INSERT ... SELECT`. dlt would add a normalization layer that risks weakening
`knowledge_time` / claim semantics.

**Decision: `REJECT_FOR_NOW`** — no meaningful custom LOC removed, semantic
risk non-zero. Revisit only if a new source adds heavy generic normalization.

---

## 8. Dagster pilot plan (Ticketmaster forward acquisition)

- Wrap `_run_ticketmaster` (from `oa/live_data_activation.py`) as a Dagster
  asset/job.
- Dagster may own: schedule, retry orchestration, dependency graph, asset
  materialization status, freshness.
- Festival remains authoritative for: `audit.provider_acquisition_runs`,
  `audit.pipeline_phase_runs`, raw observations, claims, `knowledge_time`, PIT,
  rights, canonical entities, alerts.
- Map `Dagster run ID ↔ Festival provider_acquisition_runs.run_id`.
- A/B equivalence required: same observations, same canonical rows, same
  `knowledge_time`, same `acquisition_run_id` semantics, same alerts, same PIT
  visibility, same idempotency. Otherwise `DAGSTER_PILOT = FAIL`.

---

## 9. OpenLineage plan (after Dagster parity)

Emit standard lineage (job, run, input/output dataset, code version, parent
run) + Festival facets:
`source_policy_status`, `commercial_use_status`, `evidence_class`,
`knowledge_time`, `PIT cutoff`, parser/normalization/identity-resolution
versions, input fingerprint.

OpenLineage augments — never replaces — the audit/evidence model.

---

## 10. H3 design (defer code unless cheap)

Add ONLY derived geography, never overwrite raw coordinates:
`venue_key, lat, lon, coordinate_source, coordinate_confidence, h3_r5..r8,
h3_derivation_version`. Future uses: catchment, competitive density, radius
clauses, routing, market overlap. Never infer "H3 cell = local demand".

---

## 11. Exclusions (DO NOT BUILD this milestone)

Feast, MLflow, Voyager, OR-Tools production, DataHub, Superset, Kafka,
BlazingMQ, Chronon, ClickHouse, Trino, XGBoost, LightGBM, neural nets,
attendance forecast product, guarantee recommendation, GO/HOLD/PASS, VaR/CVaR,
lineup optimizer. The preserved research result `COMPS_SIGNAL_ONLY` governs.
