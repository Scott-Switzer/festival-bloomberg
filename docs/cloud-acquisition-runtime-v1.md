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

---

# CLOUDFLARE_ACQUISITION_ROUTER_V1 (PR #50)

## Architecture

The acquisition router replaces always-Monid FAST collection with a
cheapest-acceptable-rail hierarchy, all producing ONE shared `AcquisitionResult`
contract. A cheaper rail is used only when it yields usable extraction quality;
otherwise the router escalates.

```
RAIL_0_DIRECT_HTTP      Worker fetch()           INCLUDED_WORKER_USAGE
RAIL_1_BROWSER_CONTENT  Browser Run /content     CLOUDFLARE_BROWSER_INCLUDED_OR_METERED
RAIL_2_BROWSER_SCRAPE   Browser Run /scrape      CLOUDFLARE_BROWSER_INCLUDED_OR_METERED
RAIL_3_PLAYWRIGHT       @cloudflare/playwright   CLOUDFLARE_BROWSER_INCLUDED_OR_METERED (hook, not wired)
RAIL_4_MONID            context.dev              $0.0009 (MEASURED_PAID_PROVIDER)
RAIL_5_SPECIALIZED      Container/Crawlee        future fallback
```

Escalation triggers: FAILED / UNSUPPORTED / RIGHTS_BLOCKED / extraction-quality
failure (no identity evidence AND no economically relevant field extracted).

## New Endpoints (ADMIN_TOKEN protected)

| Endpoint | Purpose |
|---|---|
| `POST /admin/bootstrap-wave` | Queue never-observed accepted pairs immediately; `never_observed_only=true`, `max_cost_usd`, `dry_run` |
| `POST /admin/mapping-wave` | Sitemap + Browser `/links` discovery; deterministic artist+date+venue+city match; artist-only rejected |
| `GET /scorecard` | Per-marketplace × rail telemetry (success, useful rate, cost, latency, 403/429/5xx) |

## Cost Semantics

Browser Run is NOT free. Workers Paid includes **10 browser-hours/month**
(Quick Actions consume browser time too); usage beyond that is metered at
**$0.09 per browser-hour**.

Cost basis per rail (`cloud-runtime/src/cost-model.ts`):

| Rail | cost_basis | Governor ledger | Scorecard |
|---|---|---|---|
| RAIL_0_DIRECT_HTTP | `INCLUDED_WORKER_USAGE` | $0 cash | — |
| RAIL_1/2/3 Browser | `CLOUDFLARE_BROWSER_INCLUDED_OR_METERED` | $0 cash | `browser_ms` + `estimated_browser_marginal_usd` ($0.09/hr) |
| RAIL_4_MONID | `MEASURED_PAID_PROVIDER` | measured provider cost (tinyfish `$0`, context.dev `$0.0009`) | — |

Only Monid is provider cash spend in the Governor budget ledger. Browser
time is tracked as allowance usage + estimated marginal cost in the scorecard,
so `cost/useful-observation` never falsely reports $0 as the dataset grows
past the monthly browser allowance. The Governor reserves worst-case cost up
front and commits exact accounted cost — never more than reserved, never a
fabricated zero. `FREE_RAIL` is no longer a valid cost basis.

## Bootstrap vs Lifecycle

- **Bootstrap** (`never_observed_only=true`): a pair with no prior successful
  observation is immediately eligible — no cadence wait.
- **Lifecycle refresh** (scheduled cron): a pair is due only when its
  last-successful-observation age exceeds the lifecycle cadence threshold.

## Live Pilot Evidence (2026-08-26)

| Metric | Value |
|---|---|
| Bootstrap tasks queued | 80 |
| Fetch success | 100% (97 rows) |
| Useful-observation rate | 30.9% |
| Monid spend | $0.00 (bootstrap); +$0.0054 (6 escalations after quality fix) |
| Rail mix | 30 direct HTTP, 67 browser content (then 15 direct / 6 Monid after fix) |
| Raw objects | 114 |
| Normalized staging | 178 |
| Scorecard rows | 103 |
| Governor state | 0 reserved, 0 leases (no leaks) |## Mapping Factory Status


---

# EVENT_MAPPING_FACTORY_V2 (PR #51)

## Architecture

Three discovery sources, one identity contract (artist + date + venue + city;
artist-only forbidden; AMBIGUOUS fails closed). Only `EXACT_PROVIDER_ID` /
`EXACT_PAGE_MATCH` / `HIGH_CONFIDENCE` enter automated acquisition.

```
SOURCE 1  provider-ID promotion   canonical_url + provider_event_id → EXACT_PROVIDER_ID
SOURCE 2  venue/promoter calendars  bounded /links + sitemaps → ticket links
SOURCE 3  Common Crawl URL index    bounded domain/pattern queries → candidate evidence
          ↓
          deterministic match
          ↓
          canonical/event_identifiers/<event_key>.json  (security master)
          control/mappings/current.json                  (mapping ledger)
```

## Source 1 — Provider-ID promotion (zero scraper cost)

**Key insight:** the canonical estate ALREADY carries `provider_event_id` +
`canonical_url` for every event. There is no reason to crawl a sitemap to
rediscover an identity the provider already supplied. Promotion is a pure
deterministic transform:

- `scripts/export_identity_estate.py` exports future on-sale events with full
  identity from the lake parquet →
  `control/event_estate/identity_estate_v1.json` (20,000 events).
- `cloud-runtime/src/mapping-factory-v2.ts` reads the estate, promotes each
  event to `EXACT_PROVIDER_ID` (confidence 1.0), and persists per-event
  records + the mapping ledger.
- White-label hosts (universe.com, venue sites) keep the provider's primary
  marketplace (ticketmaster.com) — never invent a marketplace from an unknown
  host.

## Source 3 — Common Crawl URL index

- `cloud-runtime/src/common-crawl.ts` — bounded domain/pattern queries against
  the official URL index (never bulk CDX dumps).
- `latestCrawlId()` resolves the current crawl from `collinfo.json` (1h cache).
- Output is CANDIDATE EVIDENCE ONLY; deterministic validation runs afterward.
- PIT semantics preserved: `source_as_of` = capture timestamp, `retrieved_at` =
  query time. ARCHIVE_CAPTURE != PUBLICATION_TIME.

## Endpoint

`POST /admin/mapping-factory-v2` (ADMIN_TOKEN protected)

| Param | Default | Meaning |
|---|---|---|
| `max_events` | 100 | Wave size (Worker subrequest limit ~1000; use 500/wave) |
| `offset` | 0 | Estate offset for chunked waves |
| `dry_run` | false | Plan without persisting |
| `include_provider_id` | true | Source 1 promotion |
| `include_calendars` | true | Source 2 venue/promoter calendars |
| `include_common_crawl` | false | Source 3 CC index (candidate only) |

## Live results (2026-08-26)

| Metric | Value |
|---|---|
| Estate exported | 20,000 provider-native events |
| Accepted mappings persisted | **2,993 EXACT_PROVIDER_ID** |
| Marketplaces | ticketmaster.com 1,911 / ticketweb.com 78 / axs.com 4 (+ ~1,000 more in later waves) |
| Mapping method | provider_id_promotion, confidence 1.0 |
| Scraper cost | $0.00 (pure deterministic promotion) |
| Common Crawl probe | bounded query works; NOT_FOUND for future 2026 events (correct fail-closed) |

Next: activate a bounded COHORT of accepted pairs for repeated observation
(EVENT_TAPE_SCALE_V2).



Discovery via marketplace sitemaps returned NOT_FOUND for current canonical
events — the factory fails closed rather than accepting artist-only matches.
Next iteration should target venue/promoter calendars with title-rich pages.

## Verdict

`CLOUDFLARE_ACQUISITION_ROUTER_V1 = LIVE` (deployed + proven on real
infrastructure). `COHORT_500` expansion requires better mapping discovery
sources and a 48h+ stability gate before scaling beyond 100 pairs.
| **Total** | **~$10–20** |

---

# ARTIST_SECURITY_MASTER_V1 (PR #52)

## Architecture

The artist as a tradable security. Migration 043 adds the `asm` schema (named
`asm` — Artist Security Master — because DuckDB names its catalog after the
DB file, so a schema named `artist_security` would be ambiguous, and
`security` clashes with DuckDB's built-in catalog):

| Table | Purpose |
|---|---|
| `asm.artist_security_master` | canonical security object per artist |
| `metrics.artist_factor_observations` | the factor tape (one row per factor per as_of) |
| `metrics.artist_market_factor_observations` | ARTIST × MARKET snapshot object |
| `core.artist_peer_edges` | CO_BILLED comparable universe |
| `core.artist_collaboration_edges` | network family |
| `metrics.artist_live_statistics` | SHOWS_30/90/365D, festivals, days-since-last-show |
| `metrics.artist_catalog_statistics` | RELEASES_12M/36M, catalog depth, recency |
| `metrics.artist_security_snapshots` | terminal display object (factor_summary JSON) |

Every observation carries the full PIT contract (as_of ≠ available_at ≠
retrieved_at ≠ knowledge_time) with required rights/commercial status.
UNKNOWN stays NULL — never a fabricated zero. No opaque artist score, no
GO/HOLD/PASS, no demand prediction.

## Universe selection (ARTIST_SECURITY_1000)

Deterministic + explicitly NON-PREDICTIVE: ticket-market presence first
(event performers), then identity-linkage + attention depth, lexicographic
tie-break. No demand score.

## Derivation

- DEMAND/MOMENTUM from ListenBrainz totals + week/month ranges →
  LB_TOTAL_LISTENS/LISTENERS, LB_LISTENS_7D/28D, LB_LISTEN_VELOCITY.
- Wikimedia DAILY pageviews → WIKI_VIEWS_1D/7D/28D/90D, WIKI_MOMENTUM,
  WIKI_ZSCORE (trailing 180d), WIKI_ATTENTION_SHOCK (1d vs 90d mean).
- YouTube snapshots → YT_SUBSCRIBERS/CHANNEL_VIEWS/VIDEO_COUNT (latest real
  snapshot only; deltas only from two real snapshots, never reconstructed).
- LIVE / CATALOG from the MusicBrainz event/release graph; CO_BILLED peers.

## Verdict

`ARTIST_SECURITY_MASTER_V1 = DEPLOYED` (schema + computation + 17 tests;
merged in PR #52).

---

# OPEN_ARTIST_MARKET_DATA_V1 (PR #53)

## Goal

STOP adding abstract factor schemas — POPULATE the Artist Security Master.
Target: ARTIST_SECURITY_1000 with deep, historical, evidence-backed factor
histories. No fabricated volume; coverage is reported honestly.

## Collectors (all key-free except where noted)

| Source | Collector | Output | Key |
|---|---|---|---|
| ListenBrainz bulk | `attention/listenbrainz_bulk.py` | LB totals (1 POST / 1000 MBIDs) + week/month/all_time ranges | none |
| Wikimedia historical | `attention/wikimedia_historical.py` | WIKI daily rows (one per artist per day, full history or bounded) | none |
| YouTube forward tape | `attention/youtube_forward.py` | YT_SUBSCRIBERS/CHANNEL_VIEWS/VIDEO_COUNT + recent-video stats daily | YOUTUBE_API_KEY |
| Spotify catalog | `attention/spotify_catalog.py` | SPOTIFY_CATALOG_IDENTITY (identity+catalog only, API mode recorded) | SPOTIFY_CLIENT_ID/SECRET |

Every collector fails closed: missing/invalid credentials → NOT_CONFIGURED
(never a fabricated zero); rate limits stop the batch cleanly. YouTube never
reconstructs historical values from current state.

## Populate orchestrator

`security/populate.py` — universe → collectors → `run_security_master` →
honest coverage report (`compute_coverage`). Re-running is idempotent (stable
observation keys + INSERT-OR-IGNORE).

## Live population (2026-08-26, bounded: 200-artist universe, 120d wiki)

| Metric | Value |
|---|---|
| Universe | 200 artists (100% MusicBrainz-backed) |
| ListenBrainz usable | 200/200 (100%) |
| Wikimedia usable | 189/200 (94.5%) |
| WIKI daily rows | 22,606 (2026-04-27 → 2026-08-25) |
| Artist factor rows | 2,044 (DEMAND 1,073 / MOMENTUM 971) |
| YouTube | NOT_CONFIGURED-ish (key in .env invalid → collector errored honestly; 38 rows persisted as missing) |
| Spotify | 200 rows persisted as missing (lake has only 118 name-keyed spotify IDs; none join the mbid-keyed universe) |
| Governor | n/a (no paid calls) |

WIKI factor sample: WIKI_VIEWS_1D=6159, 7D=47232, 28D=156549, 90D=521415,
WIKI_MOMENTUM=-0.03, WIKI_ZSCORE=0.30, WIKI_ATTENTION_SHOCK=1.06.
LB factor sample: LB_TOTAL_LISTENS=26.6M, LB_TOTAL_LISTENERS=246K,
LB_LISTENS_7D=7140, LB_LISTENS_28D=23068, LB_LISTEN_VELOCITY=0.31.

## Open-source adoption registry

`docs/open_source_adoption_registry.yaml` — every evaluated project with
license classification + integration strategy BEFORE any code is copied.

| Project | License | Status | Pilot |
|---|---|---|---|
| spotify/voyager | Apache-2.0 | APPROVED_DEPENDENCY | PILOT 1: KNN over factor vectors; overlap-lift 0.0186 vs random (needs more peer density → INSUFFICIENT_DATA verdict, honest) |
| feast-dev/feast | Apache-2.0 | LICENSE_REVIEW | PILOT 2: PIT equivalence — 9/9 comparisons match, semantics COMPATIBLE → ADOPT gate passed |
| perspective-dev/perspective | Apache-2.0 | APPROVED_DEPENDENCY (display) | PILOT 3: snapshot export carries required columns, sort/filter/pivot measurable → ADOPT gate passed |
| bloomberg/memray | Apache-2.0 | APPROVED_DEPENDENCY (dev) | PILOT 4: dev-only profiler, fails closed when absent |
| OpenBB | AGPLv3 | REFERENCE_ONLY | study provider abstraction; no code copied |
| listenbrainz-server / troi | GPL | REFERENCE_ONLY | use CC0 dumps/APIs independently |
| last.fm / setlist.fm / bandsintown | various | LICENSE_REVIEW | COMMERCIAL_RIGHTS_PENDING / PARTNERSHIP_TARGET |

## Verdict

`OPEN_ARTIST_MARKET_DATA_V1 = LIVE` — real daily WIKI + LB factor histories
now populate the security master; the 1M-factor target scales naturally by
lengthening the wiki lookback (2015→today ≈ 4K days/artist × 1,000 artists ≈
4M daily rows, all key-free). YouTube/Spotify coverage is honest: requires a
valid YOUTUBE_API_KEY and mbid-keyed spotify IDs in the identity layer.

Next: EVENT_TAPE_SCALE / ARTIST_SECURITY_1000 full backfill pass + live
YouTube forward tape once a valid key is provisioned.
