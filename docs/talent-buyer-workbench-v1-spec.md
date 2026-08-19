# Talent-Buyer Workbench V1 — spec

Branch: `feat/talent-buyer-workbench-v1`
Base: `main @ 790ff05` (post-merge PR #33, CI green)

Target users: talent buyers and festival strategy at C3, Goldenvoice, AEG,
Live Nation; festival promoters; booking strategists; FP&A / economics analysts.

The product supports **DISCOVER → FILTER → COMPARE → INVESTIGATE → WATCH →
SHORTLIST → SCENARIO → EVIDENCE → EXPORT**. It is NOT authorized to answer
"book this artist." Every important number exposes source, knowledge_time,
evidence class, rights status, coverage state, and derivation version.
**UNKNOWN != 0.**

## The buyer workflow

```
FESTIVAL (planning project)
  → CANDIDATE UNIVERSE (deterministic, reason-tagged)
  → FILTER (fast, p95 < 300 ms target)
  → ARTIST SCORECARD (identity/live/festival/market/attention/comps/coverage)
  → COMPARE 2–10 artists side-by-side
  → SHORTLIST (statuses DISCOVERED…SHORTLIST; every change audited)
  → SLOT/DAY SCENARIO (non-optimizing board with conflict warnings)
  → RISK / COVERAGE REVIEW (UNKNOWN fields explicit)
```

## What exists (from this increment)

- **Planning schema (migration 033)**: `planning.festival_projects`,
  `planning.festival_project_stages`, `planning.festival_candidate_artists`,
  `planning.festival_shortlists`, `planning.festival_constraints`,
  `planning.festival_scenarios`. Historical festival records are never
  mutated; synthetic scenarios are marked
  `SYNTHETIC_PLANNING_SCENARIO`, never official.
- **`planning/repository.py`** — CRUD with idempotent upserts; talent_budget
  stays NULL when unknown (never 0).
- **`planning/candidates.py`** — deterministic candidate universe with
  inclusion reasons (`RECENT_FESTIVAL_ARTIST`, `TOURING_IN_REGION`,
  `ATTENTION_MOMENTUM`, `WATCHLIST_TARGET`, `COMPARABLE_TO_PRIOR_BOOKING`);
  availability is never invented (`NO_CONFLICT_OBSERVED != AVAILABLE`).
  Professional scorecard: identity, live history, festival history, attention,
  market history, PIT-safe comparable gross/attendance ranges (evidence class
  follows the stratum: OBSERVED artist comps / DERIVED venue-market comps /
  UNKNOWN broad fallback), coverage, evidence.
- **`planning/scenario.py`** — non-optimizing scenario board: CONFIRMED
  double-booking and stage-slot conflicts, PASSED-artist warnings, coverage
  gaps; summaries (billing distribution, shortlist coverage) without
  fabricated constraints.
- **Terminal API** under `/api/planning/*` (projects, stages, candidates,
  shortlist, scenarios, scorecard, seed).

## Semantics guaranteed

- Availability: `CONFIRMED_CONFLICT | POSSIBLE_CONFLICT | NO_CONFLICT_OBSERVED | UNKNOWN`.
- Shortlist: `DISCOVERED | RESEARCHING | INTEREST | HOLD | CONTACTED | PASSED | SHORTLIST | UNKNOWN`.
- Comparable evidence class = stratum class; a broad-fallback range is a
  market baseline, never artist evidence.
- Scenario warnings are warnings; the board never optimizes and never
  recommends.

## Hard product truth (current classification)

| Capability | Status |
|---|---|
| Search artists | PRODUCTION_USABLE (existing terminal search) |
| Investigate a candidate | PRODUCTION_USABLE (scorecard + existing ART page) |
| Compare candidates | PARTIAL (scorecard fields exist; dedicated compare UI pending) |
| Build a shortlist | PRODUCTION_USABLE (planning shortlists + watchlists) |
| Build a hypothetical lineup | RESEARCH_USABLE (scenario board; validation only) |
| Identify conflicts | RESEARCH_USABLE (deterministic warnings; routing data UNKNOWN) |
| Historical economics | RESEARCH_USABLE (box-office corpus; research-only rights) |
| Comparable outcomes | RESEARCH_USABLE (comparable engine; champion unchanged) |
| Data provenance | PRODUCTION_USABLE (evidence class + knowledge_time everywhere) |
| Commercial use | RESEARCH_ONLY for public corpora; design-partner track pending |
| Estimate demand | NOT_READY |
| Estimate guarantee | NOT_READY |
| Recommend booking | NOT_READY |
| Optimize a lineup | NOT_READY |

## Next increments

1. Dense pre-event data panel (venue capacity/geography first) feeding the
   scorecard and comparable V2.
2. Festival history expansion (billing/stage/set coverage for major US
   festivals) making festival-history scorecard sections real.
3. Dedicated COMPARE and BUILD surfaces in the SPA (the API contract is live).
4. Design-partner onboarding (commercial outcomes track).
