# Jev Gateway — SPEC v1

**Status:** Draft. Blocked on: Cloudflare API token (deploy) + TypeSafe early-access key (live traffic).
**Goal:** One Cloudflare Worker as the single choke point between Scott's repos and the TypeSafe
System One API. Built provider-agnostic from day one: callers target the gateway's adapter
interface, so Jev vs. the System One Adapter (LLM-backed) is a config swap, not a rewrite.

## Why a gateway instead of direct SDK calls

1. **Key security.** Festival-bloomberg is a public repo. The TypeSafe key lives once, as an
   encrypted Worker secret. Nothing secret ever touches git.
2. **Caching.** Scraper re-runs and repeated states hit KV instead of re-paying per call.
3. **Cost guardrails.** Per-service quotas + a global daily cap. A runaway scraper fails closed,
   it doesn't burn budget.
4. **Audit trail.** Every decision + confidence logged to R2. Raw `state` is never logged —
   only its hash. (Financial/scraped content stays out of the log trail.)

## Architecture

```
festival-bloomberg scrapers ─┐
zion-terminal                ├─→ jev-gateway (Worker) ─→ POST api.typesafe.ai/v1/systemone
ppe research                 │        ├── KV:  response cache
market-fuzzer (later)        │        ├── R2:  decision log (answers+confidence, no raw state)
                             │        └── secrets: TYPESAFE_API_KEY, SERVICE_KEYS
```

## API

### POST /v1/decide

Request:
```json
{
  "service": "festival-scraper",
  "service_key": "<per-service key>",
  "question_set": "festival-extraction@v1",
  "state": "<unstructured text>",
  "cache_ttl_seconds": 86400
}
```

Callers may alternatively send inline `questions` (same shape as the TypeSafe API) instead of
`question_set`. Versioned sets are preferred: they keep prompt text out of every repo and make
question changes reviewable.

Response: the TypeSafe answers object, plus gateway metadata:
```json
{
  "answers": { "...": { "choice": "...", "probabilities": {...}, "confidence": 0.91 } },
  "usage": { "input_tokens": 312, "output_tokens": 48 },
  "gateway": { "cache": "miss", "latency_ms": 214, "question_set": "festival-extraction@v1" }
}
```

### GET /v1/health
Returns gateway status + TypeSafe upstream reachability (no key required).

### GET /v1/usage
Per-service call counts, cache hit rate, token totals. Authenticated.

## Caching policy

- Cache key: `sha256(canonical(state) + canonical(questions) + model)`.
- TTL is caller-specified; sane defaults per question set (scraper extraction: 7 days —
  page content doesn't change; real-time signals: no cache).
- Cache stores the full answers object. Invalidation is by TTL only in v1 (no manual purge API).

## Cost guardrails

- Per-service rate limit (default 60 req/min, configurable per service).
- Global daily token budget; when exceeded the gateway returns 429 and pages via log.
- Every response includes `usage`, so callers can do their own accounting.

## Logging policy

Logged to R2 (one JSONL object per call):
`timestamp, service, question_set (+version), state_hash, answers, confidence values, usage,
latency_ms, cache_hit, upstream_model`.

Explicitly NOT logged: raw `state`, service keys, the TypeSafe API key.

## Question set versioning

Question sets live in `questions/<name>-v<k>.json` in the repo that owns the workload
(festival-bloomberg owns `festival-extraction`, `festival-sentiment`). The gateway loads them
at deploy time from R2 (uploaded by CI) so question text can change without a Worker redeploy.
Callers pin a version: `festival-extraction@v1`. Unversioned requests are rejected.

## Confidence routing (caller-side, not gateway)

The gateway returns calibrated confidence; each caller implements the escalation ladder:

- `confidence >= 0.90` → accept, write to warehouse
- `0.70 – 0.90` → accept, flag `needs_review`
- `< 0.70` → fall back to deterministic parser / escalate to frontier model
- High-stakes decisions → frontier model regardless of confidence

Thresholds are calibrated per question set on the caller's own labeled data — not taken from
TypeSafe's marketing numbers.

## Deployment sketch (wrangler.jsonc)

```jsonc
{
  "name": "jev-gateway",
  "main": "src/index.ts",
  "compatibility_date": "2026-09-18",
  "kv_namespaces": [{ "binding": "JEV_CACHE", "id": "<kv-id>" }],
  "r2_buckets": [{ "binding": "JEV_LOG", "bucket_name": "jev-decision-log" }]
}
```

Secrets (never in repo):
```
wrangler secret put TYPESAFE_API_KEY
wrangler secret put SERVICE_KEYS   # JSON: {"festival-scraper": "...", "zion": "...", "ppe": "..."}
```

## Rollout plan

1. **Now:** build callers against the adapter interface (`src/jev-client.ts` sketch in this dir)
   using the System One Adapter (LLM-backed). No TypeSafe dependency.
2. **Benchmark:** run the festival extraction question set over a labeled sample of scraped pages;
   record accuracy, calibration, latency, cost vs. the hand-written parsers.
3. **Key arrives:** point the gateway at `api.typesafe.ai`, run the identical harness, compare.
4. **Route:** high-confidence, high-volume decisions → Jev; uncertain → frontier; deterministic
   stays deterministic.

## Open blockers

- [ ] Cloudflare API token (deploy the Worker, create KV + R2)
- [ ] TypeSafe early-access API key (`wrangler secret put TYPESAFE_API_KEY`)
- [ ] Labeled evaluation sample from the festival scraper (for calibration)
