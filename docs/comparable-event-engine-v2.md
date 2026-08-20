# Comparable Event Engine V2 — research closure (no-tuning pass)

Branch: `feat/comparable-event-engine-v2`
Base: `main @ e9e59471ad292ba546d3a8c9fe9fa9fd31956194` (post-merge of PR #35)

Objective: determine whether the two families admitted by PR #35 —
**Wikimedia historical attention** and **PIT market competition** — provide
incremental predictive value over the hierarchical champion (A) and
Comparable V1 (B), without changing the methodology to force a win.

Verdict: `COMPARABLE_EVENT_ENGINE_V2 = PARTIAL_ATTENTION_EVALUABLE_COMPETITION_NOT_EVALUABLE`

- **Competition** is `NOT_EVALUABLE_ON_CURRENT_CORPUS` (no historical event
  source exists).
- **Wikimedia attention** is not evaluable from the *stored* trailing panel
  (`INSUFFICIENT_FOR_COMPARABLE_V2`) but **is** `EVALUABLE_AFTER_HISTORICAL_ACQUISITION`:
  the source serves historical pageviews from 2015-07-01, and a bounded real
  pilot retrieved complete per-cutoff 30d windows for both 2024 and 2026
  events (see below).

## What happened before this pass

A prior local (uncommitted) experiment reported that Wikimedia attention
"hurts" and competition "adds nothing." That experiment was invalid for two
reasons, both confirmed by the closure audit in
`scripts/comparable_v2_closure.py`:

1. **Competition** was substituted with a 2026-era **city-level density**
   proxy (126/657 = 19.2% coverage) instead of the admitted event-level PIT
   feature `event_competition_same_day_market` / `_14d_market`.
2. **Wikimedia attention** used a **trailing 2025-2026 pageview window** as if
   it were a per-cutoff historical window, leaking current attention backward
   into a 2012-2026 historical comparable set.

Both substitutions violate the PIT contract. Their numbers are not evidence
about the admitted features.

## PHASE 1 — Denominator reconciliation

PR #35 reported competition as `500/500 = 100% coverage`. The frozen research
corpus is **657 events (243 TIME)**. The two populations are **temporally
disjoint**:

| Population | Date range | Distinct events |
|---|---|---|
| Frozen corpus | 2012-11-25 → 2026-07-03 | 657 (243 TIME) |
| Warehouse Ticketmaster snapshots | 2026-08-14 → 2028-07-18 | 14,024 |

The 500-event competition sample was drawn from the **warehouse** (2026-2028
Ticketmaster events), not from the frozen corpus. Therefore "500/500" is a
**warehouse coverage figure**, not a Comparable-V2-corpus coverage figure. It
cannot be compared against the other TIME percentages (which use the
243-event TIME denominator).

## PHASE 2 — Competition semantic gate

Event-level competition for a corpus event at date D requires *other* events
in the same market near D whose `knowledge_time < cutoff`. The warehouse
contains:

- zero Ticketmaster events before 2026-08-14,
- an empty `events.events` table (0 rows),
- an empty `events.provider_event_observations` table (0 rows),
- all knowledge_time in 2026-08-15 → 2026-08-19.

The dated corpus spans 2012-2026, and **zero** corpus events have a cutoff
inside the warehouse event window. There is no historical event source from
which 2012/2013/2024/2025 competition could be reconstructed.

**Competition verdict: `NOT_EVALUABLE_ON_CURRENT_CORPUS`.**

The correct statement is *not* "competition adds no signal" — it is
"competition signal could not be evaluated on the current historical corpus."

## PHASE 3 — Wikimedia attention coverage gate

Three facts that an earlier revision conflated:

1. **Stored panel is insufficient.** The locally stored Wikimedia rows are a
   single trailing window (observation days 2025-08-19 → 2026-08-20). They
   cannot supply a 30d window ending before a 2019 or 2024 cutoff.
2. **Retrieval time is not a gate.** The rows were downloaded in Aug 2026, but
   `retrieved_at` is provenance and is **never** an admissibility gate. A 2019
   pageview fetched in 2026 was still knowable in 2019 because its
   `available_at = observation_day + 1`. This matches
   `attention.historical_pit` and the feature registry's
   `artist_attention_wikimedia_30d_at_cutoff` knowledge-time rule.
3. **The source is capable.** The Wikimedia Analytics API serves historical
   pageviews from **2015-07-01**, so per-cutoff historical acquisition is
   possible for the eligible corpus tail.

### Corpus split at the source boundary

| Split | Cutoff | Dated events |
|---|---|---:|
| PRE_WIKIMEDIA_SERIES (UNAVAILABLE) | < 2015-07-01 | 232 |
| WIKIMEDIA_SERIES_ELIGIBLE | ≥ 2015-07-01 | 240 |

Days before 2015-07-01 are **UNAVAILABLE** (the source did not exist) — never
MISSING, never ZERO.

### Bounded real historical pilot

`scripts/wikimedia_historical_pilot.py` fetched real per-cutoff 30d windows for
30 stratified eligible events (TIME-hold first, then year-spread):

| Metric | Value |
|---|---:|
| Targets attempted | 30 |
| Page resolved | 27 |
| 404 (missing) | 3 |
| Errors | 0 |
| Full PIT-admissible 30d window | 26 |

The 3 missing are genuine article-resolution failures (including a corpus
name typo, "Bruce Sprigsteen"). Every resolved artist had a complete,
PIT-admissible 30d window with `available_at = observation_day + 1 < cutoff`,
for both 2024 and 2026 cutoffs. `retrieved_at` (Aug 2026) is recorded as
provenance and changes nothing.

## Overall verdict

```
COMPARABLE_EVENT_ENGINE_V2 = PARTIAL_ATTENTION_EVALUABLE_COMPETITION_NOT_EVALUABLE
  competition              = NOT_EVALUABLE_ON_CURRENT_CORPUS
  wikimedia_stored_panel   = INSUFFICIENT_FOR_COMPARABLE_V2
  wikimedia_source         = EVALUABLE_AFTER_HISTORICAL_ACQUISITION
```

`retrieved_at` is NEVER an admissibility gate.

## What remains to actually run Comparable V2

1. **Wikimedia attention** — the pilot proved acquisition works; the next step
   is a bounded historical backfill of per-cutoff windows for the 240 eligible
   (post-2015-07-01) corpus events, then the frozen attention-only V2
   experiment. This is acquisition, not a modeling pass.
2. **Competition** — requires a historical event source (archival event data
   with knowledge_time) covering 2012-2026. None exists in the warehouse; it
   stays `NOT_EVALUABLE_ON_CURRENT_CORPUS` until one is acquired.

## Next milestone decision

Do **not** build Comparable V3. The negative result here does not license more
modeling on the same sparse inputs.

- Priority A: **SHOW_ECONOMICS_WORKBENCH_V1** (deterministic break-even /
  margin-of-safety / price × capacity × sell-through scenarios; no demand
  prediction required).
- Priority B: **VENUE_INTELLIGENCE_SCALE_V1** — venue capacity is 0% and
  coordinates 47% in the current warehouse; capacity is economically
  fundamental and is the single largest measured coverage gap.

If a real sanitized promoter/design-partner dataset arrives, run
**DESIGN_PARTNER_RETROSPECTIVE** immediately — it remains the highest-value
information source.

## Repro

```
PYTHONPATH=python .venv/bin/python scripts/comparable_v2_closure.py
# writes reports/comparable_engine_v2_closure.json

PYTHONPATH=python .venv/bin/python scripts/wikimedia_historical_pilot.py
# writes reports/wikimedia_historical_pilot.json (real network fetch)
```
