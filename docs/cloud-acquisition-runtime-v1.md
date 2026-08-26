# CLOUD_ACQUISITION_RUNTIME_V1

**Status:** IN PROGRESS
**Branch:** `feat/cloud-acquisition-runtime-v1`
**Date:** 2026-08-26

---

## Architecture

```
                   CLOUDFLARE WORKFLOW
                  scheduled acquisition
                           │
                           ▼
                  ACQUISITION PLANNER
                  (lifecycle-aware cadence)
                           │
                           ▼
                 Cloudflare Queues
                ┌──────────┴──────────┐
                │                     │
          FAST QUEUE            DEEP QUEUE
          (Monid,               (tickets.dev,
           event-state)          listing-level)
                │                     │
                └──────────┬──────────┘
                           ▼
                 CLOUDFLARE CONTAINER
                 existing Python collectors
                           │
            ┌──────────────┼───────────────┐
            ▼              ▼               ▼
          Monid       Ticketmaster      tickets.dev
            │              │               │
            └──────────────┼───────────────┘
                           ▼
                     R2 RAW BUCKET
                   (immutable evidence)
                           │
                           ▼
                  PROCESSING QUEUE
                  (parse/materialize)
                           │
                           ▼
                     R2 LAKE BUCKET
                    (Parquet event tape)
```

## Cloudflare Resources

| Resource | Name | Purpose |
|---|---|---|
| Worker | `fi-acquisition-runtime` | Control plane entry point |
| Workflow | `fi-acquisition-workflow` | Scheduled acquisition cycles |
| FAST Queue | `fi-acquisition-fast` | Cheap/high-volume tasks |
| DEEP Queue | `fi-acquisition-deep` | Expensive/selective tasks |
| Processing Queue | `fi-acquisition-processing` | Raw → normalized observations |
| DLQ | `fi-acquisition-dlq` | Permanent failures |
| Durable Object | `AcquisitionGovernor` | Budget/rate/concurrency state |
| Container | `fi-acquisition-collector` | Python collector execution |
| R2 Raw | `festival-intelligence-raw` | Immutable raw evidence |
| R2 Lake | `festival-intelligence-lake` | Parquet event tape |
| R2 Backups | `festival-intelligence-backups` | Canonical DB backups |

## Queue Configuration

| Queue | Batch Size | Batch Timeout | Max Retries | DLQ |
|---|---|---|---|---|
| FAST | 5 | 60s | 3 | fi-acquisition-dlq |
| DEEP | 2 | 120s | 3 | fi-acquisition-dlq |
| Processing | 10 | 30s | 5 | fi-acquisition-dlq |

## Workflow Schedule

The workflow runs on a cron schedule (configurable, default: every 6 hours).
Each cycle:
1. Loads acquisition universe from R2
2. Classifies lifecycle stage for each event
3. Determines observation cadence per lifecycle policy
4. Enforces source rights/status
5. Calculates expected cost
6. Submits tasks to appropriate Queue
7. Persists run summary

## Container Runtime

- Python 3.12 slim
- DuckDB, boto3, zstandard, httpx
- No dependency on `/Users/...` or local `data/`
- No embedded credentials
- Health check: `python -c "import duckdb; print('ok')"`
- Structured JSON logs

Container sizes (Cloudflare):
- `standard-1`: 0.5 vCPU, 4 GiB RAM, 8 GB disk — sufficient for FAST tasks
- `standard-2`: 0.5 vCPU, 4 GiB RAM, 12 GB disk — needed for DEEP + large responses

## Secret Handling

| Secret | Storage | In Code | In Logs | In R2 | In Queue |
|---|---|---|---|---|---|
| MONID_API_KEY | Cloudflare Secrets | ❌ | ❌ | ❌ | ❌ |
| TICKETMASTER_API_KEY | Cloudflare Secrets | ❌ | ❌ | ❌ | ❌ |
| TICKETS_DEV_API_KEY | Cloudflare Secrets | ❌ | ❌ | ❌ | ❌ |
| R2 credentials | Container env (runtime) | ❌ | ❌ | ❌ | ❌ |

All secrets are injected at runtime via Cloudflare bindings.
No secret in git, image layers, logs, reports, Queue payloads, or R2 objects.

## Budget Policy

| Parameter | Default | Configurable |
|---|---|---|
| Daily budget | $10.00 | ✅ |
| Monthly budget | $200.00 | ✅ |
| Max concurrent containers | 3 | ✅ |

Hard invariant: `spent + expected_next_cost <= authorized_budget`
checked BEFORE any paid network request.

## Retry Policy

| Error | Behavior |
|---|---|
| Timeout | Bounded retry (max 3) |
| HTTP 429 | Respect Retry-After, cooldown |
| HTTP 502/503 | Retry with backoff |
| Auth failure | Fail, no retry storm |
| Budget exceeded | No retry |
| Rights blocked | No retry |
| Parser failure | Retry, then DLQ |
| Permanent failure | DLQ after max retries |

## DLQ Semantics

Permanent failures after bounded attempts are moved to `fi-acquisition-dlq`.
Failure context is preserved (task_key, error_category, error_detail).
No secrets in DLQ payloads.

## Idempotency Contract

Every task has a deterministic `task_key`:
```
event_key × marketplace × rail × scheduled_window × mapping_version
```

Cloudflare Queues are at-least-once delivery.
Duplicate delivery detection:
- Governor tracks `recent_task_keys`
- Same task_key within dedup window → `SUPPRESSED`
- No duplicate economic truth
- No double cost accounting

## Rights Behavior

- All marketplace observation is `TERMS_REVIEW_REQUIRED`
- Stored as `PROTOTYPE_ONLY` unless cleared
- Sandbox evidence NEVER enters production
- No CAPTCHA bypass
- No rights restrictions weakened

## R2 Layouts

### Raw Evidence
```
raw/<provider>/<sha[0:2]>/<sha[2:4]>/<sha>.<ext>.zst
```

### Parquet Lake
```
events/provider_event_snapshots/
metrics/artist_attention_observations/
core/artists/
core/venues/
...
```

## Lifecycle-Aware Cadence

| Time to Show | Collections/Day | Label |
|---|---|---|
| >120 days | 1/7 | weekly |
| 120–60 days | 2/7 | 2x/week |
| 60–30 days | 1 | daily |
| 30–14 days | 1 | daily |
| 14–7 days | 2 | 2x/day |
| 7–3 days | 3 | 3x/day |
| 3–1 days | 4 | 4x/day |
| Show day | 6 | 4-6x/day |
| Post-show | 0 | stop |

Policy version: 1.0.0
Configurable via `DEFAULT_CADENCE_POLICY`.

## Mac-Independence Status

| Capability | Mac Required | Cloud Status |
|---|---|---|
| Scheduling | ❌ | Workflow cron |
| Queue dispatch | ❌ | Cloudflare Queues |
| Provider network calls | ❌ | Container execution |
| Raw persistence | ❌ | R2 writes |
| Observation persistence | ❌ | R2 Parquet |
| Cost accounting | ❌ | Governor DO |

**Verdict:** `REMOTE_ACQUISITION_INDEPENDENCE = PENDING`
(framework deployed, awaiting live pilot cycles)

## Scaling Recommendation

After `CLOUD_ACQUISITION_RUNTIME_V1` is stable:

1. **COHORT_100**: 48h stable → next stage
2. **COHORT_500**: 7d stable → next stage
3. **COHORT_1000**: 14d stable → operational

Review cost, rights, parser health between stages.

## Measured Cost (Estimated)

| Resource | Est. Monthly |
|---|---|
| Workers Paid | $5.00 |
| Queues (1M ops) | Included |
| Containers (bursty) | ~$5–15 |
| R2 storage (6 GB) | ~$0.09 |
| R2 operations | ~$0.01 |
| **Total** | **~$10–20** |
