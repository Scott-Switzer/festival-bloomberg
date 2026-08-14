# Public Boxscore Research Corpus V2

The V1 milestone proved we can acquire real reported box-office outcomes from
public sources at $0. V2 turns that proof-of-concept into a **diversified,
deduplicated, statistically auditable research panel** — and it answers the
questions that matter before any baseline model is fit.

> Status: **research corpus only.** Every row is `RESEARCH_ONLY` /
> `TERMS_REVIEW_REQUIRED`. The commercial-eligible corpus remains **0**
> (fail-closed). No model is trained here.

## Live result (real public data, $0)

| Metric | Value |
| --- | --- |
| Pages fetched | 28 (1 Billboard, 12 Pollstar, 15 Touring Data), 0 failures |
| Raw engagements | **657** (262 Billboard, 239 Pollstar, 156 Touring Data) |
| Canonical engagements | 657 (all distinct; sources span disjoint windows) |
| Distinct artists | 356 |
| Distinct tours | 15 |
| Distinct venues | 384 |
| Distinct markets | 270 |
| Distinct promoters | 129 |
| Date range | 2012-11-25 → 2026-07-03 (5 distinct years) |
| Monetary cost | **$0.00** (no Monid, no Apify) |

### Outcome coverage (single-show, reported)

| Target | Count |
| --- | --- |
| `REPORTED_ATTENDANCE` | 357 |
| `PAID_TICKETS` | 86 |
| `TICKET_GROSS` | 441 |
| `SELL_OUT` (explicit sellout/not-sellout assertion) | 226 |
| `SELL_THROUGH` (source-printed %) | 131 |

Promoted outcome claims: **1,716** (research-only).

## What changed vs V1

1. **Scale.** V1 had 332 engagements from 3 pages. V2 has 657 from 28 pages,
   spanning five years and three sources.
2. **Cross-source engagement identity.** `research/resolution.py` maps raw
   rows onto deterministic `canonical_boxoffice_engagements` identities
   (`DISTINCT` / `EXACT_MATCH` / `PROBABLE_MATCH` / `REVIEW_REQUIRED`) without
   mutating raw rows.
3. **Source agreement.** `cross_source_agreement()` reports per-field
   differences across matched engagements; values are never reconciled.
4. **Diversity / concentration audit.** `research/audit.py` computes distinct
   counts and Herfindahl indices (artist 0.008, venue 0.006, market 0.011,
   source 0.348) plus top-N shares.
5. **Explicit selection metadata.** Every source declares its sampling
   mechanism (Billboard chart, Pollstar top-5-per-tier chart, Touring Data
   reported tour). We never pretend these are random samples.
6. **Deterministic, leakage-safe split manifests.** TIME, ARTIST_GROUP,
   VENUE_GROUP, MARKET_GROUP and TOUR_GROUP splits are written as ID-only
   manifests. A tour is never split across train/test.
7. **Baseline readiness verdict.** Model-free: `RESEARCH_READY`.
8. **Forward ticket-inventory watchlist foundation.** Touring Data "Ticket
   Count" is Patreon-gated; the schema/model exists but the OA reports
   `NOT_AVAILABLE` (no bypass).

## Touring Data block parser

Current (2024+) Touring Data pages render each engagement as a 7-line block:

```text
March 5-7, 2024          ← dates
Zach Bryan               ← artist
United Center            ← venue
Chicago, United States   ← city
$12,648,557              ← gross  (or "TBA")
56,931 (100%)            ← headcount + sell-through (or "TBA")
3 shows                  ← show count
```

`parse_touring_data_blocks()` emits only blocks with numeric gross **and**
headcount. "TBA" blocks (upcoming/unreported shows) are skipped; "~"-prefixed
values are flagged estimated and never promoted. A legacy inline parser is
retained for 2019-era pages and is auto-selected by `parse_touring_data_auto()`.

## Semantic guards (unchanged from V1, re-verified)

- Multi-show aggregates are never divided across nights and never promoted.
- Estimated rows are never promoted.
- Pollstar "Tickets Sold" is `PAID_TICKETS` (per Pollstar's own policy:
  comps/production kills excluded) — never relabeled to the broader
  `TICKETS_SOLD`.
- Billboard "Attend/Capacity" and Touring Data headcount are
  `REPORTED_ATTENDANCE` (paid vs scanned unspecified).
- Every promoted claim is research-only; commercial-eligible is always 0.

## Honest limitations

- **Cross-source overlap is currently ~0.** Billboard (2012-13), Pollstar
  (Jan-May 2024) and Touring Data (2024-26) span largely disjoint windows, so
  the agreement machinery has no live matched pairs yet. It is unit-tested and
  will populate as the panel broadens.
- **Selection-biased.** All three sources are chart/editorial compilations of
  reported engagements, not a representative draw sample.
- **Capacity bins are partial.** Only Billboard single-show rows carry a
  numeric `capacity_total`; Pollstar rows carry a capacity-tier label, and
  Touring Data rows carry sell-through % but not absolute capacity. Venue
  maximum capacity is never conflated with event usable capacity.
- **Chicago is a small slice** (12 engagements) — this is a national/global
  research panel, not a Chicago-only corpus.

## Baseline-readiness verdict

`RESEARCH_READY` — 443 single-show reported headcount labels across 242
artists and 277 venues with a 14-year span and low concentration. This is a
sufficiently diverse panel to support a **grouped/time-held-out baseline
study** (comps → linear → simple hierarchical), which is the next milestone —
not ML.

## Files

| Path | Purpose |
| --- | --- |
| `schema/migrations/016_public_boxscore_research_corpus_v2.sql` | sources, canonical engagements, resolutions, inventory snapshots, splits |
| `research/resolution.py` | cross-source identity + agreement |
| `research/audit.py` | diversity/HHI/selection/coverage/readiness/splits |
| `research/inventory.py` | forward ticket-inventory watchlist model |
| `research/acquisition.py` | Pollstar archive + Touring Data discovery + selection metadata |
| `research/boxscore.py` | Touring Data block parser + tier-aware Pollstar parser |
| `research/repository.py` | persistence for the new tables |
| `oa/boxscore_v2.py` | live OA driver |
| `tests/python/test_boxscore_research_v2.py` | offline regressions |
