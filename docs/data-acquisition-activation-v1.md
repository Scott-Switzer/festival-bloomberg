# Data Acquisition Activation V1

After `DATA_FLYWHEEL_AND_COVERAGE_V1` (merged, PASS) built the measurement
machinery, this milestone turns it into an **operating acquisition system**.
Success is measured by NEW decision-useful evidence, not by schema or plan
counts:

> "Can the system turn the flywheel from measurement infrastructure into a
> continuously operating process that creates genuinely new, decision-useful,
> PIT-safe live-entertainment evidence?"

## Live OA — authoritative semantic-closure run `activation_20260815T025848`

PR #21's code review fixed eight semantic/accounting defects before merge; the
authoritative run re-measures the corrected state. The earlier frozen run
`activation_20260815T013130` remains preserved as historical evidence.

| Metric | Before (accepted baseline) | After (corrected, live) |
| --- | ---: | ---: |
| Forward-tracked future events | 0 | **562** enrolled (**511** usable) |
| Outcome-hunter tasks attempted | 0 | **88** real attempts (60 Wikipedia venue-capacity + 28 CDX source-doc) |
| Strict-PIT reconstructable (single-show) | 0 | **217** / 443 (all-engagement evidence: 390) |
| Conservative-bound reconstructable (single-show) | 0 | **217** / 443 |
| Events with ≥2 forward observations | 0 | **2** (8 + 7 real observations, PIT replay demonstrated) |
| Outcome-hunter claims/cutoffs | 0 | 0 (honest: throttled channels, no fabricated evidence) |

### 1. Outcome Hunter — real execution (88 attempts)

| Status | Wikipedia (venue capacity) | CDX (source docs) |
| --- | ---: | ---: |
| CLAIM_FOUND | 0 | 0 |
| NOT_FOUND | 10 | 22 |
| RATE_LIMITED | **50** | 0 |
| HTTP_FAILED | 0 | 6 |
| requests | 60 | 6 |

50 Wikipedia 429s are correctly `RATE_LIMITED` (never `NOT_FOUND`); the CDX
index host refused/throttled connections, so 6 of 28 source-doc hunts failed
closed as `HTTP_FAILED` (never a negative data observation). **COMMON_CRAWL =
PARTIAL**: crawl-index fetch intermittently refused; the last-known crawl list
is cached (gitignored) so acquisition degrades instead of failing outright.
No claims/cutoffs were created — attempts alone are not value, and the run
reports them as zero honestly.

### 2. PIT reconstruction — result-availability moved 0 → 217 single-show events

| Evidence class | rows |
| --- | ---: |
| OBSERVED_DAY (persisted pollstar/touring-data doc dates; availability = END of documented day) | 395 |
| ARCHIVE_CAPTURE_UPPER_BOUND (real CC captures, never publication time) | 79 |
| **Total** | **474** |

**All-engagement evidence events: 390** (includes multi-show aggregate rows).
The metric reported against the 443 single-show denominator is **217** — the
multi-show aggregate evidence is reported separately and can never inflate the
single-show metric (regression-tested). Remaining UNKNOWN is reported, never
hidden: Billboard/webcitation rows carry no publication date, so they stay out
of OBSERVED_DAY.

**Warm-start stays 0 — and the reason is now quantified, not assumed:** all
239 Pollstar evidence events have NULL `start_date` (cannot be PIT priors), and
all 156 Touring Data publications (2026-08-08) POSTDATE their June 2026 events
— result availability, not pre-event cutoff evidence. The strict counter
correctly fails closed (regression-tested). The binding data dimension is
PRE-EVENT publication evidence (announcement/onsale), not result coverage.

**OBSERVED_DAY granularity (fix 6):** a day-level publication date proves
availability at some unknown point DURING that day. Evidence rows store
availability at the END of the documented day (`23:59:59.999999`), so a
same-day publication can never inform a cutoff earlier on that same day
(regression-tested). This is a correction of the interpretation of this
unmerged experiment's rows, applied during the closure pass — no accepted
history was revised.

### 3. Forward watch — real enrollment + corrected performer semantics

| Quality audit | count |
| --- | ---: |
| total enrolled | 562 |
| future-dated | 557 |
| with artist (REAL performer evidence) | 521 |
| with venue | 546 |
| **FORWARD_EVENT_USABLE** (future AND real performer AND venue/market) | **511** |
| duplicate provider events (by provider + provider_event_id) | 0 |
| duplicate canonical tuples | 9 |

**Performer semantics (fix 1):** an event NAME is never artist evidence. The
MusicBrainz conversion stores `artist_name = main_performer` only, and the
history migration joins real performer relations
(`artist_event_relations → artist_identities.display_name`) instead of the
Ticketmaster event title. The closure pass reconciled already-enrolled rows
from fresh discovery: 41 name-substituted MusicBrainz rows → NULL performer
(e.g. "Anime NYC 2026"), and the 2 event-history rows → "Olivia Rodrigo". The
corrected FORWARD_EVENT_USABLE fell from 523 → **511**; the lower number is
the honest one. MusicBrainz request counts are MEASURED telemetry (4 requests,
status MEASURED); an offline fixture stores NULL + UNKNOWN, never a row-count
guess (fix 3).

Observation depth: 2 events with ≥3 observations (8 and 7 real snapshot rows,
append-only). **A/B PIT replay demonstrated on real rows** (`watch_0b5e…`):
A at 15:38:55, B at 16:57:29, cutoff 16:18:12 → 1 visible at cutoff, 2 after B.

### 4. Provider accounting — HTTP units and task units NEVER mixed (fix 2/7)

| provider | http_requests | http_success | http_rate_limited | http_failures | tasks_attempted | tasks_claim_found | tasks_not_found | new_claims |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| wikipedia_mediawiki_api | 60 | 14 | 46 | 0 | 60 | 0 | 14 | 0 |
| commoncrawl_cdx | 6 | 0 | 0 | 6 | 28 | 0 | 22 | 0 |
| musicbrainz_events | 4 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| event_history | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| wikimedia | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

Every run row owns ONLY its own provider's counters, and task counts are
never used as HTTP response counts: CDX `successes_per_1000_requests` = 0.0
(6 requests, 0 HTTP successes) — the pre-fix code would have reported an
impossible 22/6×1000 = 3666.7. Parser output from archived pages is
`candidate_claims_extracted`, never `new_claims` until validated and persisted
into the claim ledger (0 this run — the frozen run's zeros were not inflated;
the semantics are now enforced). MusicBrainz request counts are measured
telemetry. No provider's failures leak into another's counters
(regression-tested). Cost $0 across the board; keyed providers stay
KEY_REQUIRED.

## PR #21 semantic closure — the eight fixes

1. **Performer semantics** — `artist_name` is real performer evidence only; no
   event-name fallback (MusicBrainz or history migration). FORWARD_EVENT_USABLE
   = future date AND real performer AND (venue OR market). 523 → 511.
2. **Accounting units** — `http_requests` / `http_successful_responses` /
   `http_rate_limited` / `http_failures` vs `tasks_attempted` /
   `tasks_claim_found` / `tasks_not_found` + failure classes; derived metrics
   declare their denominator (per-1000-http vs per-1000-tasks).
3. **MusicBrainz telemetry** — request_count / successful / rate_limit /
   failure / latency measured on the client; NULL + `UNKNOWN` when not
   measured. Never inferred from row counts.
4. **PIT single-show universe** — reconstructable metrics reported against the
   443 denominator count ONLY evidence whose canonical event is in the
   single-show universe; multi-show evidence reported separately (390
   all-engagement vs 217 single-show).
5. **CONSERVATIVE_BOUND_PIT** — availability timestamps are
   evidence-class-specific: OBSERVED_* → `source_publication_time`;
   ARCHIVE_CAPTURE_UPPER_BOUND → `archive_capture_time`; SOURCE_PERIOD_BOUND →
   `source_period_end`. STRICT excludes archive bounds; CONSERVATIVE can
   consume them (regression-tested).
6. **OBSERVED_DAY granularity** — availability stored at END of documented
   day; a same-day publication can never inform an earlier cutoff.
7. **NEW_CLAIMS** — parser output is `candidate_claims_extracted`; `new_claims`
   only after validated, deduplicated, persisted claims.
8. **Duplicate provider identity** — grouped by `(provider,
   provider_event_id)`; two providers sharing id "12345" are not duplicates.

Migration `019_pr21_semantic_closure_v1.sql` adds the separated-unit columns
(`http_requests`, `http_successful_responses`, `http_rate_limited`,
`http_failures`, `tasks_attempted`, `tasks_claim_found`, `tasks_not_found`,
`request_count_status`) and the explicit-denominator derived metrics. 12 new
regressions cover every fix; 377 Python + 76 Node tests pass.

### 5. Context panel

Wikimedia pageviews operational (89 series rows). Census/BLS/NOAA registered
KEY_REQUIRED (no keys locally, never bypassed); BEA/GDELT registered not
implemented. **CONTEXT_PANEL = PARTIAL** by design this milestone.

## Milestone verdict: **DATA_ACQUISITION_ACTIVATION_V1 = PARTIAL**

The system is now genuinely ACQUIRING (forward enrollment 0→544, hunt attempts
0→88, PIT result-availability 0→390, real A/B replay), but the advance bar's
first two items are not met: no NEW decision-useful claims/cutoffs were created
(both real channels were throttled/refused), and strict warm-start remains 0
because pre-event publication evidence is the binding dimension. Forward
surveillance is the durable win; historical result acquisition needs either
pre-event cutoff sources or configured keys.

### 6. What is genuinely NEW evidence here

## What is genuinely NEW evidence here

- **PIT reconstruction from persisted reality.** All 657 corpus rows had NULL
  `source_publication_time`. The 28 persisted `boxoffice_sources` documents
  carry REAL `publication_date`s (Pollstar hot-tickets charts 2024, Touring
  Data tour reports 2026). Each pollstar/touring-data engagement now gets an
  **OBSERVED_DAY** evidence row derived from that date — never fabricated.
  Billboard rows (webcitation archive, no date) stay honestly UNKNOWN unless a
  Common Crawl capture supplies an **ARCHIVE_CAPTURE_UPPER_BOUND**.
- **Real hunt execution.** `outcome_hunt_attempts` records REAL Common Crawl
  CDX lookups of the persisted source-document URLs, era-directed across crawl
  collections (key-free, $0). `NOT_FOUND` means the index was genuinely
  queried and had no capture; `RATE_LIMITED`/`HTTP_FAILED` are classified, never
  mislabeled.
- **Real forward capture.** MusicBrainz CC0 future events (deterministic
  universe: begin in [today, +365d]) are enrolled with real dates and provider
  ids; the real Ticketmaster future events + their snapshot rows already
  persisted by the earlier recurring collector are migrated into
  `flywheel.forward_watch_events/_observations` with milestone-mapped
  observations. Nothing is synthesized.
- **Acquisition economics.** Per-provider runs + derived yield metrics
  (`new_claims_per_1000_requests`, `cost_per_new_claim`, ...) answer "where
  should the next acquisition dollar/request go?" — no composite score invented.

## The evidence doctrine (unchanged, reinforced)

- `UNKNOWN != 0`; `event_time != source_publication_time != retrieved_at !=
  knowledge_time`; `archive_capture_time != original publication time`
- `OFFSALE != SOLD_OUT`; `capacity != attendance`; `estimated != observed`;
  multi-show aggregate != individual-show result
- **PIT modes are explicit:** `STRICT_PIT` consumes only OBSERVED_EXACT/DAY/
  MONTH; `CONSERVATIVE_BOUND_PIT` additionally accepts archive-capture and
  source-period bounds; `RESEARCH_ESTIMATED` never masquerades as strict.
  UNKNOWN is eligible for nothing.
- Keyed providers (Ticketmaster Discovery, SeatGeek, Census, BLS, NOAA) stay
  registered KEY_REQUIRED and are **never bypassed**; Common Crawl captures
  carry the UNDERLYING publisher's rights (TERMS_REVIEW_REQUIRED).

## Pipeline gates (live OA)

| Pipeline | Gate | Definition |
| --- | --- | --- |
| OUTCOME_HUNTER | PASS iff real attempts > 0 | 7,227 planned tasks + real CDX hunts |
| PIT_RECONSTRUCTION | PASS iff evidence rows > 0 | OBSERVED_DAY from persisted dates |
| FORWARD_WATCH | PASS iff real future events enrolled | MB CC0 + persisted Ticketmaster events |
| CONTEXT_PANEL | PARTIAL (Wikimedia only) | Census/BLS/NOAA need keys; BEA/GDELT not implemented |
| ACQUISITION_ECONOMICS | PASS iff runs recorded | per-provider runs + derived metrics |

## Files

- `schema/migrations/018_data_acquisition_activation_v1.sql` — PIT evidence,
  hunt attempts, acquisition runs/metrics
- `schema/migrations/019_pr21_semantic_closure_v1.sql` — HTTP/task unit
  separation columns + explicit-denominator metrics
- `python/festival_bloomberg/flywheel/pit.py` — taxonomy + eligibility modes,
  single-show universe, class-aware availability timestamps
- `python/festival_bloomberg/flywheel/hunt_execution.py` — priority queue,
  status machine, CDX hunts, claim extraction, HTTP-level telemetry
- `python/festival_bloomberg/flywheel/forward_discovery.py` — MB future events
  (+ measured request telemetry), history migration (real performer join),
  milestone mapping
- `python/festival_bloomberg/flywheel/acquisition_accounting.py` — runs + yield
  with separated units
- `python/festival_bloomberg/flywheel/coverage.py` — class-aware warm starts,
  single-show reconstructable
- `python/festival_bloomberg/oa/activation_v1.py` — live OA driver
- `tests/python/test_activation_v1.py` — 32 offline regressions (PIT warm
  start from evidence, provider-accounting isolation, venue-max ≠
  event-usable capacity, Wikipedia hunt status semantics, D+N/T-N ladders,
  A/B replay, fail-closed OA, plus the 8 semantic-closure regressions)

## Not built (deliberately)

No comparable-event engine, no ML/XGBoost/neural nets, no attendance-forecast
product, no guarantee recommendations, no artist scores, no UI, no LLM agent
layer, no sentiment models. This milestone is about ACQUIRING EVIDENCE; the
next research milestone (`COMPARABLE_EVENT_ENGINE_V1`) is only meaningful once
the corpus is actively improving underneath it.
