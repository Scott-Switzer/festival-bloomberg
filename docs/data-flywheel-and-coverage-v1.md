# Data Flywheel & Coverage V1

The strategic shift after `BASELINE_RESEARCH_V1` (`COMPS_SIGNAL_ONLY`):

> We are no longer trying to prove we can build a predictor from 657 rows. We
> are building the live-entertainment dataset that a serious predictor, risk
> engine and festival capital-allocation system would need to exist.

The baseline proved historical comparables carry real but modest signal, and
that the TIME holdout is an almost pure cold-start test (Billboard 2012–13,
Pollstar Jan–May 2024, Touring Data 2024–26 are temporally disjoint). The
next lever is **data scale + data depth + customer utility**, not algorithm.

Immediate roadmap:

```
DATA_FLYWHEEL_AND_COVERAGE_V1   <- this milestone
  -> COMPARABLE_EVENT_ENGINE_V1 <- first useful product (retrieval/ranking)
  -> PRE_OFFER_PIT_LAB_V1       <- offer-time decision cutoffs at scale
  -> UNDERWRITING_RESEARCH_V2   <- economics-centered underwriting
```

Status: **remote PR #20 (draft) — operational closure in progress.** The
warehouse layer is live and tested; keyed/terms-review sources (Census, BLS,
Ticketmaster, JamBase, ...) are registered with their real access status and
never bypassed. Three review fixes landed before merge: engagement vs
performance KPI split, settlement-gated private evidence, and onsale-anchored
D+N forward-watch milestones (regression-tested).

## The four pipelines

| Pipeline | What it does | Gate (research corpus, live OA) |
| --- | --- | --- |
| `EVENT_GRAPH` | MusicBrainz identity backbone (CC0) + source registry + Common Crawl/JamBase/Ticketmaster discovery | PASS (identities resolved live, key-free) |
| `OUTCOME_HUNTER` | Claims-based outcome acquisition for attendance / paid tickets / gross / sellout / capacity / ticket price / promoter / tour / announcement / onsale / show_count | PASS for plan/task ledger (7,227 tasks planned); **execution NOT started** — no live hunt source wired yet |
| `CONTEXT_PANEL` | Attention (Wikimedia pageviews), market (Census/BLS/BEA), weather (NOAA/ERA5) — PIT-vintaged | **PARTIAL** — only Wikimedia implemented; Census/BLS/NOAA/ERA5/GDELT registered, keys required, no rows yet |
| `FORWARD_WATCH` | Discovered future events enter a milestone ladder; time-sensitive observations that can never be reconstructed later | NOT_EVALUATED — 0 future events in the research warehouse (forward collection lives in the market-history warehouse) |

Every row keeps the canonical PIT evidence columns: `event_time`,
`source_publication_time`, `retrieved_at`, `knowledge_time`, `validity`,
source identity, `rights_status` / `commercial_use_status`, raw hash, parser
version and resolution confidence.

## Coverage objectives (KPI-corrected)

`flywheel.objectives` stores 28 product-development targets (proposed, not
statistically validated). The vocabulary is deliberate — and engagements are
NEVER conflated with performances:

* **CANONICAL_BOXSCORE_ENGAGEMENTS** — bookings, incl. multi-show aggregates
  (an engagement is not a performance)
* **SINGLE_SHOW_ENGAGEMENTS** — defensible single-show bookings
* **CANONICAL_PERFORMANCES** — canonical single-performance rows; the
  explicit eligible denominator for every decision rate (multi-show
  aggregates never enter a metric called PERFORMANCES)
* **OUTCOME_CLAIMS** — source-backed claims in the ledger (claims ≠ events)
* **UNIQUE_EVENTS_WITH_OUTCOMES** — distinct events with ≥1 defensible claim
* **FULLY_SETTLED_EVENTS** — distinct events with settlement evidence
* **PRIVATE_EVENTS_WITH_SETTLEMENT_EVIDENCE** — OBSERVED_PRIVATE imports with
  settlement-TYPE evidence (PROMOTER_CONTRIBUTION / SETTLEMENT_GROSS /
  SETTLEMENT_NET); an attendance-only private import is NOT settlement

Measured baseline (research corpus, live OA):

| Objective | Target | Actual |
| --- | ---: | ---: |
| Canonical boxscore engagements | 50,000 | **657** |
| Single-show engagements | 45,000 | **443** |
| Canonical performances (denominator for all rates) | 50,000 | **443** |
| OUTCOME_CLAIMS | 5,000 | **1,110** |
| UNIQUE_EVENTS_WITH_OUTCOMES | 2,500 | **443** |
| FULLY_SETTLED_EVENTS | 500 | **0** |
| Artists with ≥3 outcome observations | 1,000 | **27** |
| Markets | 50 | **248** ✅ |
| Canonical venues | 1,000 | **384** |
| Continuous useful period (≥2018) | 8 years | **3** |
| Forward-tracked future events | 2,000 | **0** |
| Private events with settlement evidence | 500 | **0** |
| Events with attendance | 2,500 | 357 |
| Events with paid tickets | 1,000 | 86 |
| Events with gross | 3,000 | 441 |
| Events with sellout | 1,000 | 226 |
| Events with capacity | 1,000 | 0 |
| Events with onsale date | 2,000 | 0 |
| Events with announcement date | 2,000 | 0 |
| Events with ≥3 prior artist results (PIT) | 1,500 | **0** |
| Events with prior market result (PIT) | 2,000 | **0** |
| Events with prior venue result (PIT) | 1,500 | **0** |
| Events with ticket pace | 2,000 | 0 |
| Events with offer/booking cutoff | 2,000 | 0 |
| **WARM_START_RATE** | 0.5 | **0.0** |
| **OFFER_TIME_RECONSTRUCTABLE_RATE** | 0.8 | **0.0** |
| **TICKET_PACE_COVERAGE** | 0.6 | **0.0** |
| **SETTLEMENT_COVERAGE** | 0.5 | **0.0** |

### The critical finding: 657 engagements are only 443 single-show performances, 1,110 claims live on 443 events, and warm-start is 0.0

Three numbers change the conversation:

1. **Engagements ≠ performances.** The 657 canonical engagements include
   **214 multi-show aggregates**. Only **443** are defensible single-show
   engagements — and every decision rate is measured against that eligible
   denominator, never against the full 657. (Previously reported as 657
   "performances"; corrected.)

2. **Claims ≠ events.** The 1,110 outcome claims come from only **443 unique
   events** — so "outcome rows" was never a proxy for settled-event depth.
   `UNIQUE_EVENTS_WITH_OUTCOMES` is the number that matters for comparable
   retrieval.

3. **WARM_START_RATE = 0.0 under strict PIT, and it is a data gap, not a
   modeling verdict.** The strict prior-result metrics require
   `source_publication_time < event start`. **All 657 corpus rows have NULL
   `source_publication_time`** — the product-time knowledge was never
   persisted. The measurement fails closed (unknown ≠ zero; it is reported as
   no coverage), which is exactly the baseline's warning: *historical results
   exist, but product-time decision cutoffs largely do not*. Reconstructing
   those cutoffs (Common Crawl time-machine, archived announcements/onsale
   pages) is the single highest-value acquisition target.

Every OA run appends `flywheel.coverage_snapshots`, so the acquisition metric
stays decision coverage, not row counts.

## OUTCOME_HUNTER execution statistics (honest)

Plans are NOT acquisitions. The OA manifest reports the full execution block:

```
tasks_planned    7227   (657 plans x 11 target fields, persisted ledger)
tasks_attempted  0      (no live hunt source wired yet)
tasks_successful 0
claims_created   0      (corpus claims are promoted by the boxscore research corpus)
unique_new_outcomes / unique_new_events / duplicate_claims / conflicts 0
rate_limited / rights_blocked / not_found / parser_failed 0
cost_usd         0.0
```

`new_evidence_by_field` (NEW_REPORTED_ATTENDANCE / NEW_PAID_TICKETS /
NEW_GROSS / NEW_SELL_OUT / NEW_CAPACITY / NEW_ONSALE_DATE /
NEW_ANNOUNCEMENT_DATE / NEW_TICKET_PRICE) is zero until a live hunt source
executes. The system is rewarded for **new decision-useful evidence**, never
for searches.

## Sources (flywheel.source_registry)

21 sources are registered with their rights, quota and coverage contribution:

- **Identity/event graph:** MusicBrainz (CC0, key-free), Ticketmaster
  Discovery (key, 5,000 calls/day), JamBase (terms review), Common Crawl
  (CC0 index; page rights tracked per source)
- **Outcomes:** Billboard Boxscore / Pollstar Hot Tickets (research-only),
  Touring Data (terms review), Setlist.fm (key)
- **Context:** Wikimedia pageviews (key-free, IMPLEMENTED), GDELT (free),
  Google Trends (alpha application), YouTube (key), Bluesky firehose (terms
  review), Census / BLS (key), BEA (open), NOAA CDO (token), ERA5
  (registration) — all registered, none wired yet
- **Forward:** SeatGeek (key)
- **Private/backtest:** Eventbrite first-party connector, customer
  ticket/settlement exports (partner-gated)

The acquisition metric: *"how much does this source improve decision coverage
or our ability to validate a decision model?"* — not "how many rows did we
get?" No multi-account key evasion; caching, delta collection and multiple
legitimate sources instead.

## Semantic honesty (unchanged doctrine)

- **Outcomes are claims, not resolved values.** Conflicting observations
  coexist in `economics.event_outcome_claims`; reconciliation happens later.
- **Capacity ≠ attendance; OFFSALE ≠ SOLD_OUT; setlist presence ≠
  attendance.** `flywheel.outcome_hunter.claim_from_hunt_finding` validates
  through the controlled outcome taxonomy and fails closed.
- **Two kinds of weather never mix:** actual weather (explains outcomes) vs
  forecast weather known at cutoff (usable prospectively).
- **Vintages matter:** a 2022 booking model must never receive a
  2026-revised ACS/BLS statistic. `flywheel.context_panel_series` stores
  `vintage` + `knowledge_time` on every row.
- **Discovery-API status is not an internal ticket-count feed.**
  `forward_watch_observations.observation_class` keeps that distinction
  explicit.
- **Missing publication time is unknown, not zero.** PIT prior metrics fail
  closed.
- **D+N means days after ONSALE.** An unknown onsale date means the D+1/
  D+3/D+7/D+14 capture timestamps are UNKNOWN (`basis=onsale_unknown`), never
  anchored to the event date; the event-relative T-N ladder runs
  independently. `UNKNOWN_ONSALE != EVENT_DATE`.

## Running it

```bash
PYTHONPATH=python python3 -m festival_bloomberg.oa.flywheel_v1
# or against the research corpus database:
PYTHONPATH=python python3 -c \
  "from festival_bloomberg.oa.flywheel_v1 import run_flywheel_v1_oa; \
   run_flywheel_v1_oa(db_path='data/warehouse/boxoffice_research_v2.duckdb')"
```

The manifest lands at `reports/data_flywheel_and_coverage_v1.json`. All
pipelines degrade gracefully: no network / no rows is reported honestly
(`NOT_EVALUATED`), never fabricated. Bounded, `$0.00`.

## Files

- `schema/migrations/017_data_flywheel_coverage_v1.sql` — flywheel schema
- `python/festival_bloomberg/flywheel/` — objectives, coverage, repository,
  event_graph, outcome_hunter, context_panel, forward_watch, sources
- `python/festival_bloomberg/oa/flywheel_v1.py` — live OA driver (execution
  stats, per-provider context gates, pipeline gates)
- `tests/python/test_flywheel_v1.py` — 30 offline regressions (incl. the
  engagement/performance split, private settlement gating, and onsale-anchored
  D+N forward-watch regressions)
- `docs/baseline-research-v1.md` — the research verdict this milestone answers

## What is deliberately NOT built yet

No ML, no LLM agents, no ticketing/POS, no fake `74/100 Artist Score`, no fake
guarantee, no fake sell-through prediction. The next milestone is
`COMPARABLE_EVENT_ENGINE_V1` — but only after this milestone closes
operationally (remote PR + exact-head CI + live OA verified), and it will be
a falsifiable retrieval/ranking question:

> Can we systematically identify historical events that are economically more
> informative for a target booking than crude artist/venue/market medians?

Three layers: (A) PIT-safe retrieval, (B) a similarity VECTOR (never a single
arbitrary score) with per-component missingness, (C) outcome-weighted
evaluation across TIME / ARTIST / VENUE / MARKET / TOUR holds — Top-1, Top-3
median, Top-5 median, distance-weighted KNN, hierarchical fallback, vs the
baseline medians. The milestone is the retrieval system beating the strongest
`BASELINE_RESEARCH_V1` baseline, not "does the comp list look reasonable".
