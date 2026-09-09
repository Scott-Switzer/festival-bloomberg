# ARTIST_SECURITY Live Intelligence — Supplement (Phases 1–11)

*Date: 2026-09-08 · Origin/main post ListenBrainz full-corpus closure (`terminal_v1_20260908T194538Z`)*
*Companion to [Artist Security Activation Matrix v1](./artist-security-activation-matrix-v1.md) — that file remains the Phase 0 snapshot (frozen serving counts). This supplement records what was newly productized, scaffolded, and verified in the same session without re-freezing the generation.*

---

## Phase 1 — Artist Security Product

### What changed

* **Backend** (`python/festival_bloomberg/terminal/artist_security.py`):
  * New `overview` panel — deterministic synthesis of only currently supported evidence (identity, freshness, latest comparable factor shift, forward-event horizon, top market, latest catalyst/peers/sentiment/ticket evidence). No score. Missing panels produce an explicit **UNKNOWN** bullet, never a zero.
  * New `momentum` panel — deterministic, PIT-admissible baselines per dated series:
    1D / 7D / 30D time-windowed change, EMA(7/30), linear slope (last ≤30 points), historical z-score, volatility, acceleration (second-order), and a change-point flag (|last step| > 2 σ of step changes). Series with <2 dated points emit `INSUFFICIENT_HISTORY`. Uses only observations at or before the latest `observation_time` / `period_end`. Labelled **ATTENTION / CONSUMPTION MOMENTUM — not ticket demand**.
  * New `news` panel — metadata-only `GDELT DOC 2.0 artlist` catalyst tape (title, domain, URL, publication time). No article body. Forward-compatible with `terminal.news_mentions` / `news_mentions` / `artist_news_mentions`; when the table is absent the panel stays **UNKNOWN**. Deterministic placeholder clustering; real clustering will use NIM rerank/embed.
  * `evidence.items` extended with `news` and `momentum` for the evidence table; `get_artist_security` still returns the existing contract version (`artist_security_terminal_v1`) so hosted compatibility is preserved.
* **Frontend** (`apps/terminal/mvp/app.js`):
  * Artist page gains, in order: **Overview → Momentum → News → Attention → Factor tape → Sentiment / Provider rails → Peers / Geography → Alternatives / External IDs → Markets / Live history → Festivals / Forward events → PUBLIC TICKET MARKET → Evidence summary**.
  * New renderers: `renderOverview`, `renderMomentum`, `renderNews`. Overview heading mirrors the page title style (no unrelated nav leak — only the existing "Public Ticket Market — *not ticket demand*" panel had stale wording; every peer/momentum note already carries the required disclaimer).

### Verified on the frozen serving generation

```
PYTHONPATH=python .venv/bin/python -c "… get_artist_security …"
  alice cooper (no YouTube history):  overview has 9 bullets, momentum UNKNOWN 0 series, news UNKNOWN — honest.
  mbid::15a6f792-… (143 YouTube obs, 3 factors): momentum OBSERVED 3 series
     channel_view_count  latest 12244076  1D +1077 (+0.01%)  7D +7404 (+0.06%)  z 1.23
     subscriber_count    latest 11900     flat (slope 0, vol 0, z null)
     video_count         latest 14        flat
  sentence: sentiment OBSERVED 111-row VADER baseline for 0.44% of artists, kept as baseline.
```

The frozen generation's YouTube evidence window (2026-08-27 → 2026-09-05, 89,388 rows, youtube-only) now yields real momentum deltas rather than a static panel. Spotify/Soundcharts/Chartmetric remaining at `PROVIDER_READY / AUTH_REQUIRED` is intentional — no unauthorized scraping.

---

## Phase 2 — Continuous Acquisition Fabric

**No new queue was created.** The fabric is the one already deployed and measured in [Cloud Forward Data Plane v2](./cloud-forward-data-plane-v2.md):

* **Clock:** Cloudflare Cron `* * * * *` in `cloud-runtime` (`fi-acquisition-runtime`) — the one-minute `scheduled()` trigger is the master clock; the planner is the only cadence/quota authority.
* **Planner:** `cloud-runtime/src/forward-planner.ts` + `cloud-runtime/src/planner.ts` — family planning and quota-aware cadence (`YOUTUBE_CHANNEL` / `TICKET_STRUCTURED` / `TICKET_WEB` / etc.), with `loadV2Universe` reading `control/watch_universe/current.json` and `control/youtube/active_channels.json`.
* **Queues:** `fi-youtube`, `fi-structured-api`, `fi-browser`, `fi-monid` (`FAST_QUEUE` alias), `fi-processing`, `fi-dlq`. `fi-monid` is constrained independently from official API work.
* **Governor:** `cloud-runtime/src/governor-do.ts` / `governor.ts` — authoritative consumer-side budget gate.
* **Consumers:** `youtube-consumer.ts` (batches up to 50 `channels.list` IDs, exact raw + SHA-256, `VALUE_CHANGE`/`HEARTBEAT`), `structured-consumer.ts` (official Ticketmaster API with raw + normalized `raw_evidence_ref`), DLQ archival handler (`evidence/queue-dlq/` idempotently by Cloudflare message id).
* **Lanes (spec §2):**
  1. `STRUCTURED_API` — Ticketmaster Discovery, YouTube `channels.list` — **wired**.
  2. `STATIC_HTTP` — content-addressed raw fetches — **wired** via the router (`acquisition.ts`).
  3. `CLOUDFLARE_BROWSER_RUN` — `cloud-runtime/src/browser.ts` with `BROWSER` binding — **declared in wrangler + routing code**, bounded retries.
  4. `HETZNER_CRAWLEE_PLAYWRIGHT` — `scrapling` / `apify` / `commoncrawl` provider classes + Hetzner env (`HETZNER_API_KEY` in `.env.example` as `HETZNER_*`) — **provider classes exist, Hetzner provisioning not exercised in this session**; crawler capabilities (Crawlee queue, dedup, bounded retry, session handling, domain concurrency, checkpoint, Playwright fallback, failure store, hashing, observability) are spec'd but not provisioned against the 25 K estate in this slice. This lane stays `NOT_AVAILABLE` until an explicit provisioning step records hourly/monthly economics.

**Ops health** at `GET /ops/health` (authenticated) reconciles scheduler enqueue counts with ack/retry and explicit-DLQ counts over a rolling 15-minute window, merges in authoritative `Queue.metrics()` (Cloudflare Queues observability + [2026-04-28 backlog metrics changelog](https://developers.cloudflare.com/changelog/post/2026-04-28-improved-queues-metrics/)), and marks incomplete/failed-minute telemetry as `telemetry_complete: false` (fail-closed, never fabricate completeness).

**Change in this session:** none to the fabric wiring. The supplement exists to prevent rebuilding it.

---

## Phase 3 — Source Policy

Every domain/provider has an explicit `RightsProfile` evaluated by `acquisition/policy.py` → `governance/policy.py`; absent sources fail closed via `unknown_profile`.

| Source | Content | API | Scraping | Storage | Derivative | Redistribution | Commercial | Notes |
|---|---|---|---|---|---|---|---|---|
| wikidata | APPROVED | APPROVED | PROHIBITED | APPROVED | APPROVED | APPROVED | APPROVED | CC0 |
| openstreetmap | COND | COND | PROH | COND | COND | COND | COND | ODbL, attribution |
| wikimedia/gdelt/wikipedia | COND | COND | PROH | COND | COND | COND | COND | CC BY / BY-SA variants with attribution |
| youtube/musicbrainz/reddit/x/tiktok/instagram/facebook/seatgeek/setlistfm/rss | REVIEW/RESEARCH | REVIEW/RESEARCH | PROH | REVIEW/RESEARCH | REVIEW/RESEARCH | REVIEW/RESEARCH | REVIEW/AGREEMENT | ToS / commercial review required |
| seatgeek | REVIEW | REVIEW | PROH | REVIEW | REVIEW | REVIEW | REVIEW | Platform API v2 event-level stats; no individual listings |
| ticketmaster | REVIEW | REVIEW | PROH | REVIEW | REVIEW | REVIEW | **COMMERCIAL_AGREEMENT_REQUIRED** | Official Discovery API v2 — research reads allowed with key; commercial product needs agreement |
| soundcharts | COND | COND | PROH | COND | COND | COND | **COMMERCIAL_AGREEMENT_REQUIRED** | Licensed provider rail |
| google_trends | REVIEW | REVIEW | PROH | REVIEW | REVIEW | REVIEW | REVIEW | Official alpha waitlist only; UI scraping is disabled |

`commercial_context = "research" | "commercial"` gates differently; `mechanism = "api" | other` is enforced per profile. No CAPTCHA/paywall/auth/anti-bot bypass is built. GDELT provider honors a shared, process-wide 5 s minimum spacing (`DEFAULT_MIN_INTERVAL_SECONDS = 5.0`, threading lock) and maps 429 → `RATE_LIMITED`.

---

## Phase 4 — Immutable Web Evidence

Contract (`evidence_rails/contract.py`):

* Every fetch persists **immutable** `ObservationRecord` (content-addressable; `observation_id = obs::<hash16>` over `source_platform|source_record_id|observation_type|retrieved_at`):
  `source_platform`, `acquisition_provider`, `source_record_id`, `observation_type` (`EVENT_DISCOVERY`, `TICKET_PRICE`, `TICKET_AVAILABILITY`, `TICKET_LISTING`, `EVENT_METADATA`, `CAPACITY`, `ARTIST_ATTENTION`, `MARKET_CONTEXT`), `raw_payload`, canonical keys (`artist_key`/`venue_key`/`event_key`/`market_key`), temporal (`observed_at`, `retrieved_at`, `source_publication_time`, `announcement_time`, `onsale_time`, `event_time`, `knowledge_time`), actor/endpoint, category, `normalized_fields`, `parser_version = evidence_rails_v1`, `rights_status`, `commercial_use_status`.
* **Raw before derived:** raw bytes stored content-addressed by SHA-256; identical content may dedup storage but each retrieval records a new observation. No LLM summary ever becomes the source record — `raw_payload` is the source.
* YouTube batches: exact raw response bytes + `raw_evidence_ref` carried into the lake tick; Ticketmaster structured: raw Ticketmaster response + normalized observation with matching `raw_evidence_ref`.
* R2 layout: `raw/` (content-addressed), `lake/` (normalized ticks + provider snapshots), `backups/` (`control/scheduler/`, `control/watch_universe/`, `control/youtube/active_channels.json`, `evidence/queue-dlq/` DLQ archive).

---

## Phase 5 — NVIDIA NIM Intelligence Plane

*Client:* `python/festival_bloomberg/intelligence/llm.py` — fail-closed `NimClient` (zero network calls when unconfigured) exposing `list_models()` (cheap auth check + catalog load), `chat()`, `embed()`. Base URL `https://integrate.api.nvidia.com/v1`.

*Model routing:* `ModelRouter` is a three-layer, fail-closed router:
1. explicit overrides (only if catalog-valid),
2. catalog candidates (hint-matched against the LIVE catalog),
3. fallback defaults (only if catalog-valid),
4. `"UNAVAILABLE"` — never invent a model id.

`DEFAULT_TASKS`: `FAST_EXTRACT → meta/llama-3.3-70b-instruct`, `DEEP_REASON → deepseek-ai/deepseek-r1`, `CODE_REASON → qwen/qwen2.5-coder-32b-instruct`, `EMBED → nvidia/nv-embedqa-e5-v5`, `RERANK → nvidia/llama-3.2-nv-rerankqa-1b-v2`. `TASK_HINTS` ordered so known-good deployable ids (e.g. `nv-embedqa-e5-v5`) never get shadowed by a broken same-prefix hint (e.g. `llama-3.2-nv-embedqa-1b-v1`).

*Live catalog (2026-09-08, from `NVIDIA_API_KEY` in env — key not printed):*

```json
{"status": "OK", "models": ["01-ai/yi-large", "deepseek-ai/deepseek-v4-flash-0731", "deepseek-ai/deepseek-v4-pro-0813", …, "nvidia/embed-qa-4", "nvidia/llama-3.1-nemotron-70b-instruct", "nvidia/llama-3.2-nv-embedqa-1b-v1", "nvidia/nemotron-3-embed-1b", "nvidia/nv-embedqa-mistral-7b-v2", "nvidia/nvclip", …], "count": 78}
```

Resolved today: `EMBED → nvidia/embed-qa-4` (hint `embed` matched before the non-deployable `llama-3.2-nv-embedqa-1b-v1`), `FAST_EXTRACT → meta/llama-3.3-70b-instruct` (absent today → `UNAVAILABLE` under strict catalog mode — **honest**; `llama-glimmer-30b` etc. exist but did not match the hint; production should override explicitly). `RERANK → UNAVAILABLE` — no rerank model in this account catalog, correctly fail-closed (matches the Phase 0 finding).

*Derived-evidence contract:* NIM output is **derived evidence only** — it never overwrites `raw_payload`. Required persisted fields per inference: `model`, `model_version`, `prompt/schema version`, `input evidence IDs`, `created_at`, `confidence` where available, `output_hash`. See `evidence_rails/contract.py` (`knowledge_time`, `parser_version`, `rights_status`) and `llm.py` routing guarantees.

*Usage policy (Phases 5–7):* structured extraction, embeddings, reranking, classification — **not** generic prose generation. Every inference is recorded with the fields above and is replayable from its evidence IDs.

---

## Phase 6 — News / Catalyst Engine

*Existing code (not rebuilt):* `acquisition/providers/gdelt.py` (`GdeltProvider`) — key-free `DOC 2.0 artlist`, metadata-only (`content_role = news_metadata`, no article body), 5 s spacing, `RATE_LIMITED` on 429. Grounded against the live API (recent-only ~3-month explicit-date window; `seendate` normalized to ISO-8601 as `published_at`).

*Catalyst taxonomy (candidate intents):*
`TOUR_ANNOUNCEMENT`, `FESTIVAL_BOOKING`, `NEW_SHOW`, `CANCELLATION`, `POSTPONEMENT`, `RELEASE`, `COLLABORATION`, `LABEL_CHANGE`, `MANAGEMENT_CHANGE`, `AWARD`, `VIRAL_EVENT`, `CONTROVERSY`, `LEGAL_EVENT`, `HEALTH_EVENT`, `OTHER_MATERIAL_NEWS`.

*Clustering spec:* multiple articles about one development → one **catalyst cluster** with multiple `evidence_ref`s. Current session: deterministic placeholder (48 h window + domain-normalized title token overlap) in `_artist_news`. Research lane: NIM `EMBED` (`nvidia/embed-qa-4`) + `RERANK` (when available) with deterministic-rule prefilter, persisted with `input evidence IDs` and `output_hash`.

*Serving activation:* `terminal.news_mentions` is not yet materialized into the frozen serving generation (hence the `UNKNOWN` panel). Acquisition is `python -m festival_bloomberg.oa.data_fabric` (`GdeltProvider` → `terminal.news_mentions` + `NEWS_MENTION` tape rows). Frontend already renders catalysts when present.

---

## Phase 7 — Fan Intelligence V2

*Audit of the existing pipeline:*

* **VADER** (`social/sentiment.py`, model `vader@4.0.0`) — **kept as the baseline, never deleted**. `vader_inference` / `tweetnlp_inference` (optional; `tweetnlp` intentionally not installed by default, reports `NOT_AVAILABLE`) return `SentimentInference{task, model_name, model_version, label, probabilities, emotion}`. Daily aggregate in serving (`artist_sentiment_observations`: `mention_count`, `analyzed_count`, `positive/neutral/negative_share`, `sentiment_mean`, `model_name@version`, single-row language/topic stubs) holds **111 rows on `2026-09-03` only** — `0.44%` coverage, single-day window. Do not aggregate daily → weekly/monthly before source-level inference.
* **YouTube source** whose comments feed that aggregate is the official `youtube` API path (same quota domain as the factor tape); no raw usernames/IDs/post IDs/text are served.

*V2 — richer multi-label model (research-only in this slice):*

Candidate labels: `POLARITY`, `EXCITEMENT`, `LIVE_SHOW_INTENT`, `TICKET_PRICE_DISCUSSION`, `PURCHASE_INTENT`, `PERFORMANCE_REACTION`, `RELEASE_REACTION`, `CONTROVERSY`, `COMPLAINT`, `SPAM_OR_PROMOTIONAL`, `FAN_GENERATED`, `GEOGRAPHIC_MENTION`, `TOPIC`.

* Benchmark first: a **human-labeled**, stratified benchmark of **≥ 1,000 real observations** if the grown corpus supports it (from daily YT ticks × fan-signal batches at `oa/youtube_fan_signal.py`). Stratify by sentiment label, mention count, and tier.
* Report: **precision / recall / macro F1 / per-class F1**, plus calibration where applicable. Treat `SPAM_OR_PROMOTIONAL` as a filter, not a sentiment class. Keep VADER's numeric baseline frozen for the ladder.
* Non-goal: **fan sentiment is never claimed to be local demand.**

---

## Phase 8 — Multi-View Similar Artists

ListenBrainz full-corpus affinity (`artist_peers`: 126,898 edges / 13,220 artists, Gold `lb_full_20260908T193559Z`) is **one view, not the whole model**.

*Independent views (only where source/rights semantics permit; Spotify via `PROVIDER_READY`):*
* `LISTENBRAINZ` consumption affinity (Gold peer graph);
* MusicBrainz genre/tag/relationship features;
* festival co-appearance (`festival_appearances` / `event_history`);
* event/tour co-billing;
* catalog/release context;
* semantic artist embeddings from verified evidence (NIM `EMBED` over evidence text — derived, never source).

*Representation & retrieval:* 25 K universe → **simple exact similarity first** (cosine / Jaccard over explicit feature matrices) unless measured performance requires ANN. Materializer emits per-peer: `peer artist`, `similarity`, `confidence`, `coverage`, **component contributions** (e.g. `44% consumption / 24% live-co-billing / 18% semantic / 14% genre`), `evidence_date`.

*Evaluation (report these, not vibes):* `Precision@K`, `Recall@K` where meaningful, `NDCG@K`, **popularity-bias diagnostics** (decile breakdown by listen count / tier). Gold references: known consumption peers, genre relationships, co-billing relationships, **human QA set**. No unexplained black-box score — every peer exposes its decomposition.

*Status:* research-only (API currently exposes only the ListenBrainz view through `_peer_rows` + `_alternatives`). The views above are the measured-guarded path to the multi-view model.

---

## Phase 9 — Artist Momentum Research

Momentum = **ATTENTION / CONSUMPTION MOMENTUM**, not ticket demand.

*Temporal completeness audit (frozen generation):*
* ListenBrainz `attention_observations`: **50,886 rows, 25,000 artists**, but only **two metric kinds** (`LISTENBRAINZ_TOTAL_LISTEN_COUNT` / `LISTENBRAINZ_TOTAL_USER_COUNT`), **`period_start = NULL`** for 50,000 of them, so only **886 rows carry a window** (the 142 artists with 8 rows each form the only real time series). Latest `knowledge_time = 2026-08-15` — ~**24 d stale**.
* YouTube `artist_factor_observations`: **89,388 rows, youtube-only, 3 factors** (`subscriber_count`, `channel_view_count`, `video_count`), daily ticks `2026-08-27` → `2026-09-05` (9-day window). Real history lives here, but it is **9 days deep**, not the `WIKI_30D` / `LB_7D` windows the research corpus assumed.
* The ListenBrainz `artist_day` temporal side output is **`INCOMPLETE`** — do not assume completeness; verify via the `listenbrainz.artist_day` checkpoint table (like `control/listenbrainz/full_corpus/...`) before any ML.

*Deterministic baselines (now LIVE per artist via `_momentum_baselines`):*
`1D / 7D / 30D` time-windowed change, `EMA(7/30)`, linear slope, acceleration (second-order), historical z-score, volatility, **change-point flag** — all PIT-admissible.

*Falsifiable target (research ladder — not trained in this slice):*
a future attention change such as **future 7-day attention change** / `future 30-day attention change` / `future 30-day consumption change`. Inputs must be **PIT-admissible** (no future peek, `knowledge_time < cutoff`).

*Validation (required before any model claim):* rolling-origin holdout, artist holdout, genre holdout, **popularity-decile diagnostics**.

*Baseline ladder:*
`last observation` → `linear trend` → `EMA` → `30D momentum` → `regularized linear model` → then one strong tabular model (LightGBM / XGBoost). **ML is promoted only if it materially and consistently beats simple baselines.** Publish per-prediction: `expected momentum`, `interval/confidence`, `horizon`, `source coverage`, **major contributing factors**. Never relabel as ticket demand.

---

## Phase 10 — Refresh Scheduler

Not every artist needs the same cadence. No fake popularity score is ever fabricated to prioritize crawling — only measurable states gate priority.

| Tier | Policy | Signals |
|---|---|---|
| **ACTIVE / HIGH-INTEREST** | several times/day where source economics permit | upcoming event (`future_events` within N days), new catalyst (`news` cluster), unusual factor movement (momentum z-score / change-point), watchlist/shortlist membership, ticket-market exposure (771-row lake), `MONITOR` underwrite activity |
| **NORMAL ACTIVE** | daily | default for artists with real factor history and non-zero markets |
| **LOW-ACTIVITY** | weekly | tail artists with 2-row attention only, no peers, no future events |
| **EVENT / CATALYST TRIGGER** | immediate targeted refresh | `NEWS_MENTION` catalyst cluster, `EVENT_ANNOUNCED` / `CANCELLATION` / `POSTPONEMENT` from the news engine, venue-change delta |

Implementation hooks already in place: `forward-planner.ts` windows (`STRUCTURED 15-min`, `WEB 6-hour`, YouTube `250 hot channels/hour` + daily cold rotation), `planForwardFamilies` envelope, lifecycle-aware `due` semantics in `planner.ts`. Policy evolution is owned by the planner, not the clock.

---

## Phase 11 — Observability

Target: an **Artist Data Control Panel** (no new page built in this slice; spec + writer contract below).

*Per source (YouTube, Ticketmaster structured, GDELT, Wikimedia, Monid web):*
`queued tasks / successful tasks / failed tasks / rate limited / rights blocked / stale / average latency / bytes / changed-content yield / distinct artists / coverage / freshness / cost` where applicable.

*Per artist surface (Artist Security page):*
`coverage % / freshness (latest knowledge_time + per-source knowledge) / serving generation + SHA / source health`.

*Controls (already wired):* `/ops/health` reconciles enqueue/ack/DLQ + `Queue.metrics()` with `telemetry_complete`, Governor budget checks, DLQ archival to `evidence/queue-dlq/`, per-minute partitioned metrics (`telemetry_complete = false` on any truncated/malformed minute), single-queue failure isolation.

*Writer contract (already enforced):* queue consumer persists one low-cardinality batch lifecycle record per invocation; scheduler writes `control/scheduler/<run_id>.json` + `control/scheduler/LAST_CRON.json` + `control/runs/<run_id>.json` (O(1) `last_cron` — never a truncated list).

*Invariants that make dormancy visible:* a source with zero successful tasks after N scheduler ticks must surface as **0 covered artists / stale freshness / no bytes**, never as a green panel. A panel that would show zero-derived-from-empty is returned as **UNKNOWN + note**, never as a zero.

---

## Phase 12–13 context (cohort + 25K matrix)

The 25 K matrix lives in the Activation Matrix file above (this supplement does not re-freeze serving counts). The pending step is a hosted-browser acceptance pass over a varied cohort — major superstar, legacy act, mid-tier touring, electronic, hip-hop, country, Latin, emerging, international, sparse, same-name/identity-risk — with Alice Cooper remaining a fixture. For each fixture verify on **hosted production** (behind `TERMINAL_ACCESS_PATH`): identity, factor tape, **momentum (new)**, peers, **news (new — expect UNKNOWN)**, sentiment, markets, live history, forward events, tickets, relationships, evidence/freshness — with **no empty panel masquerading as working data** (the whole page is built on `UNKNOWN != 0`).

*Scale report (next time the serving generation is rebuilt):* per feature emit
`artists covered | coverage % | observations | history depth | freshness p50 / p95 | source status | hosted status` — PASS there requires actual hosted data activation, not just passing tests.

---

## Rights & evidence invariants throughout

* Every observation carries `source_system / source_scope / rights_status / knowledge_time / evidence_ref`.
* `PILOT_25K_ESTATE_SUMMARY` is the only estate label; pilot provenance was never re-introduced.
* `UNKNOWN != 0` is a contract: place ≠ venue, listing ≠ sale, offer ≠ transaction. Public ticket-market prices are `ADVERTISED_STRUCTURED_RANGE` only, never transactions/sales/attendance.
* NIM inferences are derived evidence with `(model, model_version, prompt/schema version, input evidence IDs, created_at, confidence, output_hash)` — raw evidence is never overwritten.
* No CAPTCHA / paywall / auth / anti-bot bypass. Blocked sources fail closed.

---

## Operational pointers

* Activate GDELT news into serving (and make `momentum` ML-grade): `python -m festival_bloomberg.oa.data_fabric --help` (local) or the Cloudflare path via `control/watch_universe` + scheduler ticks.
* NIM sanity: `PYTHONPATH=python .venv/bin/python -c "from festival_bloomberg.intelligence.llm import NimClient; print(NimClient().list_models())"` — expect `OK` when `NVIDIA_API_KEY` is set; never commit the key.
* Nightly serving refresh: `cloud-runtime` `scheduled()` gates a `terminal_serving_build_v1` batch (`control/serving/terminal/LAST_REFRESH.json`) — the materializer only moves `CURRENT` after validation + SHA verification (old generation stays live on failure).
* Local UAT (one-process, no port race): `PYTHONPATH=python scripts/uat_current_serving.py --port 0` and `PYTHONPATH=python scripts/uat_hosted_terminal.py --help` for hosted.

---

## What remains

* **GDELT backfill** for the 25 K estate (bounded, 5 s-spaced), then promotion of `terminal.news_mentions` into the next serving generation.
* **Fan V2** benchmark collection from grown YouTube comment corpora (the daily ticks are 9 d deep today).
* **Multi-view similarity** feature views + human QA (post-peers).
* **Momentum ML** — grows the PIT-admissible ladder only when the YouTube + ListenBrainz histories deepen beyond the frozen window and the `artist_day` table is complete; the deterministic baselines above are the honest research floor.
* **25 K scale acceptance** on the rebuilt generation with the new panels.

