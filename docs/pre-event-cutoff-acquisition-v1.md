# PRE_EVENT_CUTOFF_ACQUISITION_V1

The binding research question after `DATA_ACQUISITION_ACTIVATION_V1` is no
longer "do we have outcome data?" — it is:

> **What was actually knowable BEFORE a promoter decided to book the show?**

Reconstructing *result* availability (when outcomes became public) does not
answer the underwriting question, because the research corpus can support

```
P(Y | information available AFTER the show)
```

but not

```
P(Y | information available at booking/offer time)
```

This milestone builds the decision-time cutoff layer and measures, for every
cutoff, how many PRIOR artist/venue/market outcomes were actually knowable.

## What was built

- **`flywheel.pre_event_cutoff_evidence`** (migration 020) — append-only
  decision-cutoff ledger. One row per (event, cutoff type, evidence kind).
- **Decision-time taxonomy** — `BOOKING_OR_OFFER | ANNOUNCEMENT | PRESALE |
  GENERAL_ONSALE | TICKET_PRICE_OBSERVATION | EVENT_DATE |
  RESULT_PUBLICATION | SETTLEMENT`. Never collapsed.
- **Booking/offer evidence types** — a public announcement date is **NEVER** a
  booking date. At most it establishes `booking <= announcement`, persisted as
  an `ANNOUNCEMENT_UPPER_BOUND` (upper_bound set, `cutoff_timestamp` NULL).
  Exact booking/offer dates exist only as `OBSERVED_BOOKING_DATE` /
  `OBSERVED_OFFER_DATE` / `CONTRACT_DATE` /
  `INTERNAL_FIRST_PARTY_BOOKING_DATE`.
- **Interval evidence** — `(lower_bound, upper_bound, bound_semantics)`,
  never collapsed to a midpoint.
- **Warm-start-by-cutoff** — `prior_outcome_distribution()` answers, per
  target event and per cutoff, the distribution (0/1/2/3+/5+) of knowable
  prior same-dimension results. UNKNOWN cutoffs are reported separately, never
  silently zeroed.
- **Decision-time coverage + historical cutoff matrix** — per-cutoff
  EXACT/UPPER_BOUND/INTERVAL/UNKNOWN over the single-show universe.

## PIT doctrine (inherited, unchanged)

- `event_time != source_publication_time != knowledge_time`
- archive/first-seen capture proves availability BY that time, never original
  publication
- day-level evidence never leaks at midnight (end-of-day for OBSERVED_DAY)
- `STRICT_PIT` uses only exact observed instants; `CONSERVATIVE_BOUND_PIT`
  may additionally consume a bound; `ESTIMATED_RESEARCH_ONLY` never enters
  either
- `UNKNOWN != 0`; unknown is reported, never upgraded

## Authoritative OA: `pre_event_cutoff_20260815T122726`

Pure warehouse derivation from persisted evidence (no HTTP, $0, no fabricated
timestamps).

### Cutoffs derived vs. newly acquired

| Cutoff evidence | Rows | Kind |
| --- | ---: | --- |
| EVENT_DATE (single-show universe) | 357 | derived (re-expression of known scheduled dates) |
| RESULT_PUBLICATION | 474 | derived (re-expression of persisted PIT evidence) |
| **FORWARD ANNOUNCEMENT first-seen bound** | **562** | **NEW pre-event evidence** |
| **FORWARD BOOKING `<= announcement` bound** | **562** | **NEW pre-event evidence** |

New decision-useful cutoffs: **1,124** (the forward bounds; event-date /
result-publication re-express facts the corpus already knew).

### Warm-start by cutoff (artist, single-show headcount-bearing universe = 357)

| Cutoff | Known cutoff | Unknown | >=3 priors |
| --- | ---: | ---: | ---: |
| BOOKING_OR_OFFER | 0 | 357 | 0 |
| ANNOUNCEMENT | 0 | 357 | 0 |
| GENERAL_ONSALE | 0 | 357 | 0 |
| EVENT_DATE | 357 | 0 | **0** |
| RESULT_PUBLICATION | 131 | 226 | **0** |

The warm-start count is **0 at every cutoff**, and the reason is now
quantified rather than assumed:

1. Historical booking/announcement/onsale cutoffs are **UNKNOWN** — the public
   corpus carries result-publication dates, not pre-event decision dates.
2. Even at EVENT_DATE, the result-publication evidence is **retrospective**:
   the Touring Data batch was published 2026-08-08 (after its 2026 events) and
   the Pollstar rows carry NULL start dates, so no event has a knowable prior
   result before its own date.

This is the precise, defensible statement of the binding bottleneck: **result
availability != pre-event knowability**.

### Decision-time coverage

| Metric | Value |
| --- | ---: |
| EVENTS_WITH_ANNOUNCEMENT_CUTOFF (historical) | 0 |
| EVENTS_WITH_PRESALE_CUTOFF | 0 |
| EVENTS_WITH_ONSALE_CUTOFF | 0 |
| EVENTS_WITH_BOOKING_EXACT | 0 |
| EVENTS_WITH_BOOKING_UPPER_BOUND (historical) | 0 |
| EVENTS_WITH_RESULT_PUBLICATION (all engagements) | 390 |
| EVENTS_WITH_EVENT_DATE (single-show) | 357 |
| FORWARD_EVENTS_WITH_ANNOUNCEMENT_BOUND | 562 |
| FORWARD_EVENTS_WITH_BOOKING_BOUND | 562 |

Historical cutoff matrix (single-show universe = 357): BOOKING/ANNOUNCEMENT/
PRESALE/ONSALE are all UNKNOWN; EVENT_DATE is EXACT for 357; RESULT_PUBLICATION
is EXACT for 131 (UNKNOWN for 226).

### Comparable-engine readiness

| Decision point | Status | Usable target events |
| --- | --- | ---: |
| EVENT_DATE | READY | 357 |
| RESULT_PUBLICATION | READY | 131 |
| ONSALE | NOT_READY | 0 |
| ANNOUNCEMENT | NOT_READY | 0 |
| BOOKING_OR_OFFER | NOT_READY | 0 |

Comparable retrieval can be evaluated at EVENT_DATE and RESULT_PUBLICATION
(ex-post questions), but **not** at announcement/onsale/booking — those cutoffs
remain UNKNOWN for the historical corpus.

## Milestone verdict: **PRE_EVENT_CUTOFF_ACQUISITION_V1 = PARTIAL**

Real new pre-event evidence was persisted (1,124 forward bounds), the forward
universe now has announcement + booking upper bounds (562 events), and the
warm-start-by-cutoff measurement is operational. But the advance bar is not met:

- historical announcement/onsale/booking cutoffs did not move from UNKNOWN
  (no defensible public source of those dates exists in the corpus yet);
- strict/bound pre-event reconstructability for HISTORICAL events is still 0;
- warm-start at booking time remains 0 for the honest reason above.

The durable win is the **forward compounding**: 562 events are now under
surveillance with first-seen announcement/booking bounds, and every additional
day of observation is evidence that can never be reconstructed later.

## Next

`COMPARABLE_EVENT_ENGINE_V1` can start on the EVENT_DATE / RESULT_PUBLICATION
(ex-post) slice, but the economically decisive slice — retrieval at
announcement/onsale/booking time — needs announcement/onsale/presale
timestamps for historical events. That is the next acquisition sprint's target
(first-party booking/offer/onsale fields via the Design Partner Retrospective
machinery, plus keyed ticketing/announcement sources when configured).
