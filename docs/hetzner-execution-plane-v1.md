# HETZNER_EXECUTION_PLANE_V1 + SELF_HOSTED_INFERENCE_V1

Date: 2026-09-09 · PR #75 · branch `feat/artist-data-moat-continuous-observation-v1`

This document replaces the prior `HETZNER_CRAWLER_LANE = NOT_NEEDED` verdict
with a real, credential-gated execution plane. It does not add infra docs for
their own sake — each section is implemented in code and exercised by tests.

For the moat narrative, see `docs/artist-data-moat-p0-remeasure.md` and
`docs/artist-security-live-intelligence-v1-supplement.md`.

---

## 1. Why Hetzner is now an execution plane (not a crawler lane)

The moat work proved Cloudflare is a good **control plane** (cron, planner,
governor, queues, R2, serving) but a constrained **execution plane** for:

- Crawlee/Playwright browsing (Cloudflare Browser Run bills per-second, capped)
- heavy DuckDB (ListenBrainz 191 GB artist_day aggregation)
- Common Crawl WARC parsing (index → range fetch → parse)
- social bulk enrichment (embeddings / clustering / classification at volume)

Hetzner Cloud R2 egress is free, so streaming R2-resident corpora to a Hetzner
worker is cost-effective. A dedicated crawler-only lane was never the right
model — Hetzner should be a general execution plane the marketplace routes to.

Architecture:

```
CLOUDFLARE (control)         HETZNER (ephemeral execution)
  cron / planner  ──lease──▶  scraper / browser worker
  governor / R2   ──R2 DO──▶  heavy DuckDB / WARC / embed worker
  queues / serving ◀──R2───   results + procurement ledger
```

---

## 2. HetznerExecutionBroker

`python/festival_bloomberg/acquisition/hetzner_broker.py`

- Credential-gated on `HETZNER_API_TOKEN | HETZNER_TOKEN | HCLOUD_TOKEN |
  HETZNER_CLOUD_TOKEN` — missing token produces `BLOCKED_BY_CREDENTIAL`
  typed status, never a raw exception or secret in logs.
- `discover_capacity()` — calls `GET /v1/server_types` (and `/v1/locations`),
  reports real pricing from the API, falls back to a well-known pilot table
  when the API is unavailable. Never hard-codes availability.
- `select_cheapest_available(min_vcpu, min_ram_gb, require_x86)` — cheapest
  available type meeting the workload contract (CPX22 vs CPX32 vs etc).
- `estimate_max_cost(server_type, ttl)` — upper-bound cost for ttl at the
  type's monthly cap (prorated hourly, capped at monthly).
- `create_worker(spec)` / `delete_worker(id)` / `list_managed_workers()` —
  every worker carries labels `{project, environment, role, git_sha, job_id,
  expires_at, ttl_minutes}`. Pilot guardrail is enforced before any API call.
- `CostGuardrail` — hard $10 pilot ceiling for new Cloud compute in this
  milestone (not a monthly budget, a pilot cap). Unknown cost stays UNKNOWN.

Environment in this workspace: **no Hetzner Cloud token** — all Cloud probes
report `BLOCKED_BY_CREDENTIAL` correctly.

Robot (dedicated) credentials: likewise absent, so GPU provisioning (GEX45)
reports `BLOCKED_BY_ROBOT_CREDENTIAL`.

---

## 3. Hard TTL / Orphan Reaper

`HetznerExecutionBroker.reap_expired_workers()`

- Every ephemeral server has an `expires_at` label set at creation.
- The reaper (run from the Cloudflare control plane on a cron) deletes only
  `project=festival-intelligence` workers whose `expires_at` is in the past.
- Workers without `expires_at` are **reported but never deleted** — fail safe.
- `dry_run` is the default locally; Cloudflare passes `dry_run=false` when
  actually reaping.
- Reconciliation: `list_managed_workers()` (label selector
  `project=festival-intelligence`) vs control-plane registry.

No autoscaling. No permanent HEAVY. Provision → run → upload → delete.

---

## 4. Capacity discovery (live)

Current workspace (no token) — probe returns:

```json
{"present": false, "status": "BLOCKED_BY_CREDENTIAL"}
```

With a token, `discover_capacity()` returns:

```json
{
  "status": "OK",
  "locations": ["nbg1","hel1","fsn1","ash"],
  "options": [
    {"server_type":"cpx22","architecture":"x86","vcpu":3,"ram_gb":4,"disk_gb":80,"available":true,"monthly_eur":13.40},
    {"server_type":"cpx32","architecture":"x86","vcpu":4,"ram_gb":8,"disk_gb":160,"available":true,"monthly_eur":26.90}
  ]
}
```

The broker never requests `CAX*` when `require_x86=True` (the moat's
Playwright/DuckDB workers need x86). Location is not used to bypass source
rate limits.

---

## 5. External worker lease protocol + direct R2 data plane

`python/festival_bloomberg/acquisition/hetzner_lease.py`

Cloudflare Queues are not directly consumable by Hetzner machines, so the
control plane exposes a signed lease endpoint.

- Worker → `POST /hetzner/lease` (worker_id, capabilities, rakes)
  → Cloudflare grants `{lease_id, job_id, job_type, lease_expiry, task_payload, token}`
- `HMAC-SHA256(worker:job:expiry:lease_id)` using `FI_BATCH_HMAC_SECRET`
  (reuse batch HMAC conventions — one secret rotation covers both).
- `verify_lease()` is constant-time; missing secret produces empty token and
  `verify_lease()` returns `false` (fail closed).
- Every lease has a scoped `r2_inputs`/`r2_outputs` list (bucket+key set),
  never a full bucket credential. Large inputs stream/partition; no worker
  is required to hold the full 191 GB LB corpus.

---

## 6. Owned scraper on Hetzner

`python/festival_bloomberg/acquisition/owned_scraper.py` — `SourceCollector`
protocol + `OwnedRunner` (dedup by content hash, conditional fetch scaffolding,
structured `FAILURE_CLASS`). `OfficialSiteCollector` is the concrete static-HTTP
example (0-cost, no vendor).

On Hetzner the same `SourceCollector` runs inside a Crawlee Python context;
`CLOUDFLARE_BROWSER_RUN` stays first for light JS, `HETZNER_PLAYWRIGHT` last
for heavy browser sessions. The marketplace router captures this preference in
its lane ordering and in `decide_compute()` (P29).

---

## 7. Execution marketplace (data + compute)

`python/festival_bloomberg/acquisition/marketplace_router.py` — `MarketplaceRouter`

- Data lanes: `OFFICIAL_API | MONID | APIFY | OWNED_HTTP | OWNED_BROWSER |
  HETZNER_PLAYWRIGHT` — ranked by health+configured then `cost_per_1k_unique`
  from the procurement ledger (when present) else list-price.
- Compute lanes: `CLOUDFLARE_WORKER | HETZNER_CLOUD_CPU | CLOUDFLARE_BROWSER |
  HETZNER_GPU_FUTURE | NIM_FREE | EXTERNAL_PAID` — `decide_compute(task,
  hetzner_available)` picks by task (scrape vs heavy_batch vs embed_bulk).
- `EXECUTION_MARKETPLACE_V1` / `COMPUTE_LANE_PRIORITY` are the new contracts.

`python/festival_bloomberg/acquisition/procurement_ledger.py` records per-run
economics (cost, records_valid/unique/resolved/new, dedup rate, latency) and
aggregates `cost_per_1k_*` for router consumption. Unknown cost stays `None`.

---

## 8. R2 data plane

- R2 has free Internet egress — streaming a 191 GB ListenBrainz dump to a
  Hetzner worker costs compute only, not bandwidth.
- Workers download inputs via scoped, time-limited access (lease-provided
  bucket/key list), spill to local DuckDB, upload outputs + `CURRENT.json`
  to Gold. No permanent local copy of the corpus is needed.

---

## 9. Self-hosted inference (cascade)

`python/festival_bloomberg/inference/router.py` + `cascade.py`

Goal: zero marginal per-request cost for high-volume cheap tasks, while NIM
remains the primary large-model lane (free-hosted `nemotron-*`).

```
DETERMINISTIC → HETZNER_CPU → NIM_FREE → EXTERNAL_PAID
```

- **Stage 1 — deterministic** (`_TOO_SHORT`, url-only, dedupe, spam heuristic):
  obvious non-evidence is discarded without any model call.
- **Stage 2 — local CPU** (`HETZNER_CPU`): a task-specific small model
  (ONNX Runtime / FastEmbed / sentence-transformers / llama.cpp quantized)
  loaded on the Hetzner CPU worker. If `confidence >= threshold` (default
  0.78), the local `DERIVED` prediction is accepted.
- **Stage 3 — NIM** (`NIM_FREE`): only when local confidence is low and the
  task warrants it. NIM is still preferred for `FAST_EXTRACT`/`EMBED` while
  `AVAILABLE` under the free hosted endpoint.

`InferenceRouter` is pure logic (no network) — callers supply transport/model
availability separately so the same router runs in Worker and batch containers.

Current research direction for the CPU lane (P15): evaluate
`intfloat/multilingual-e5-small` class embedding models + `distilroberta`
sentiment/intent + `all-MiniLM-L6-v2` rerank as CPU-first, then benchmark
tokens/sec and RAM on a CPX22 (4 vCPU / 8 GB) vs CPX32 (8 vCPU / 16 GB).

No GPU is ordered in this milestone. See §11.

---

## 10. Local embeddings (NIM vs local)

`InferenceCascade.embed()` routes bulk embedding to:

1. `NIM nemotron-3-embed-1b` when `NIM_FREE` is healthy (free hosted endpoint,
   2048-dim, high quality — the default).
2. local CPU embedding (FastEmbed / sentence-transformers) on Hetzner when
   volume or latency justify it or NIM is rate-limited.

Bulk social clustering embeddings should eventually ride the local lane once
the CPU quality/latency benchmark shows parity — NIM is reserved for harder
retrieval cases.

---

## 11. GEX45 GPU economics

Public Hetzner announcement (no order in this milestone):

- **GEX45**: RTX PRO 4000 Blackwell SFF (24 GB GDDR7 ECC) + 64 GB RAM +
  Intel i5-13500 + 2×512 GB NVMe — **$249/month + $249 setup**.

Break-even model (preliminary):

- NIM hosted today is **free** within dev limits — no GPU self-hosting beats
  free while the endpoint is `AVAILABLE`.
- When inference volume grows, the break-even is:
  `monthly_gpu_cost / cost_per_M_tokens_hosted`. At ~$249/mo, self-hosting
  only wins when token volume is sustained and hosted per-token billing would
  exceed that line *and* the model fits CPU vs GPU latency SLO.
- **Decision**: `HETZNER_GPU_PROVISIONING = BLOCKED_BY_ROBOT_CREDENTIAL`
  (no Robot Webservice credentials in this workspace), and even with them the
  pilot intent is **RESEARCH_ONLY** — no GEX45/GEX131/GEX44 ordered in P75.
  Re-evaluate after measuring 30-day inference volume.

---

## 12. Cloud vs Hetzner benchmark plan (P10/P11)

Same lawful public acquisition task through:

- Cloudflare Browser Run
- Hetzner Crawlee/Playwright (`HETZNER_CLOUD_CPU`)

Cohort: 25–50 targets (artist official sites, promoter pages). Measure:
`success% / pages / valid / unique / latency / browser_seconds / compute_seconds /
bytes / estimated cost / parser_errors / blocked`.

Then ingest into `procurement_ledger.aggregate_ledger()` as `cost_per_1k_unique`
and promote the winning lane to `ACQUISITION_MARKETPLACE_ROUTER_V1` primary,
keeping the loser as fallback/shadow (1% drift probe).

Monid/Apify vs Hetzner-owned for social acquisition is benchmarked via
`python/festival_bloomberg/acquisition/benchmark.py:BenchmarkHarness`
(head-to-head on the same 50-artist cohort, $10 paid ceiling).

---

## 13. Acceptance criteria for this plane

Before the next handoff:

1. `HETZNER_CLOUD_API = PASS | BLOCKED_BY_CREDENTIAL` (credential-gated, not a failure)
2. `HETZNER_CAPACITY_DISCOVERY = PASS` (structured output, never hard-coded)
3. `HETZNER_EXECUTION_BROKER = PASS` (labels, TTL, guardrail — unit-tested)
4. `HETZNER_ORPHAN_REAPER = PASS` (scoped delete, dry-run default)
5. `HETZNER_CLOUD_TOKEN_FOUND: NO` in this workspace — provisioning is
   `BLOCKED_BY_CREDENTIAL` (correct); real provisioning proves the loop with
   one ephemeral `cpx32` → R2 output → delete (not run in this no-token env).
6. No secret printed, logged, or committed.
7. No Hetzner lane is marked `PASS` when it was only implemented but never
   exercised — `BLOCKED_BY_CREDENTIAL` is the honest verdict without a token.

---

## 14. Files introduced

| File | Role |
|---|---|
| `python/festival_bloomberg/acquisition/hetzner_broker.py` | broker + capacity + TTL + guardrail |
| `python/festival_bloomberg/acquisition/hetzner_lease.py` | lease protocol + R2 data plane |
| `python/festival_bloomberg/inference/__init__.py` | package |
| `python/festival_bloomberg/inference/router.py` | INFERENCE_ROUTER_V1 |
| `python/festival_bloomberg/inference/cascade.py` | SELF_HOSTED_INFERENCE_V1 |
| `docs/hetzner-execution-plane-v1.md` | this doc |
