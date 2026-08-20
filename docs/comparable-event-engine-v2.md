# Comparable Event Engine V2 — research closure (no-tuning pass)

Branch: `feat/comparable-event-engine-v2`
Base: `main @ e9e59471ad292ba546d3a8c9fe9fa9fd31956194` (post-merge of PR #35)

Objective: determine whether the two families admitted by PR #35 —
**Wikimedia historical attention** and **PIT market competition** — provide
incremental predictive value over the hierarchical champion (A) and
Comparable V1 (B), without changing the methodology to force a win.

Verdict: `COMPARABLE_EVENT_ENGINE_V2 = PARTIAL_COMPETITION_NOT_EVALUABLE`

Neither admitted family is evaluable on the current frozen corpus. This is a
**valid negative research result**, but it is *not* "the features were tested
and found useless" — it is "the features could not be tested at all with the
data that actually exists."

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

## PHASE 3 — Wikimedia attention PIT gate

The admitted feature is a **30d pageview window ending before the cutoff**,
knowable only when `available_at < cutoff`. The stored Wikimedia data is a
single trailing window:

- period 2025-08-19 → 2026-08-20,
- `retrieved_at` 2026-08-15 → 2026-08-20,
- 157 distinct artists with `status='ok'`.

The latest corpus cutoff is **2026-07-03**, so **every** stored Wikimedia
observation was retrieved after **every** corpus cutoff. The PIT availability
rule fails for the entire corpus. Using the trailing window would leak current
(2026) attention into historical (2012-2026) comparable sets.

**Wikimedia verdict: `NOT_EVALUABLE_ON_CURRENT_CORPUS`.**

Page resolution (does the artist have *any* pageview row) is not the same as
PIT window availability at the cutoff. The PR #35 "45.6% coverage" figure
measured page resolution, not PIT-available historical windows.

## Overall verdict

```
COMPARABLE_EVENT_ENGINE_V2 = PARTIAL_COMPETITION_NOT_EVALUABLE
  competition        = NOT_EVALUABLE_ON_CURRENT_CORPUS
  wikimedia_attention = NOT_EVALUABLE_ON_CURRENT_CORPUS
```

Both admitted families are not evaluable on the current frozen corpus. The
strict PIT / UNKNOWN!=0 rules held; the result is honest.

## What would be required to actually test these families

1. **Wikimedia attention** — a real *historical* acquisition: for each corpus
   event, fetch the pageview window ending strictly before that event's cutoff
   and persist `available_at`/`retrieved_at` semantics. That is new
   acquisition work, not a modeling pass, and only the ~2025-2026 tail of the
   corpus is even in range of the trailing data already collected.
2. **Competition** — a historical event source (archival event data with
   knowledge_time) covering the 2012-2026 corpus. None exists in the current
   warehouse.

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
```
