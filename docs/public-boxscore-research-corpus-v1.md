# Public Boxscore Research Corpus V1

The Economic Outcome Acquisition milestone proved the hard lesson: **free
public web pages are not a box-office feed.** Wikipedia + venue pages yielded
1 attendance label and 0 tickets-sold/gross/guarantee labels after a genuine
bounded pass.

This milestone changes the approach from *scraping more pages* to *ingesting
structured box-office reports* that already carry the exact fields we were
missing — gross, headcount, capacity, show count, sellouts, price range, and
promoter. The result is the first real research-grade box-office corpus, and
it gets us from **1 attendance outcome to 312 headcount + 277 gross
observations** in a single bounded, $0 acquisition run.

## The one thing that changed

A `BOXOFFICE_ENGAGEMENT` is **not** an event. It is one reported record from
a public box-office source, and it may span **one show or many**. Multi-show
aggregates are never divided across nights; only genuine single-show
engagements are promoted into the event-level outcome ledger.

## Sources (all RESEARCH_ONLY / TERMS_REVIEW_REQUIRED)

| Source | Headcount semantic | Headcount definition | Live result |
| --- | --- | --- | --- |
| Billboard Current Boxscore (archived) | "Attend/Capacity" — paid vs scanned unspecified | `REPORTED_ATTENDANCE` | 262 engagements |
| Pollstar Hot Tickets | "Tickets Sold" = **paid** tickets (comps/production kills excluded) | `PAID_TICKETS` | 20 engagements |
| Touring Data (Post Malone Runaway Tour) | date-level attendance; reported rows only | `REPORTED_ATTENDANCE` | 50 engagements |

`openicpsr` (Ticketmaster auction microstructure) and `openmuse` (venue
economics) are registered source types in the schema but were **not** part of
this bounded OA — they are separate, later milestones, not event-level box
office.

## Live corpus (single $0 run, real public data)

- **332 engagements** — 262 Billboard, 20 Pollstar, 50 Touring Data.
- **278 single-show**, 54 multi-show.
- **8 Chicago engagements**.
- **1,245 promoted outcome claims** from single-show reported rows.
- Headcount: 270 `REPORTED_ATTENDANCE` + 8 `PAID_TICKETS`.
- Gross: 277 `TICKET_GROSS`; price: 232 `PRIMARY_FACE_VALUE_MIN` + 232
  `PRIMARY_FACE_VALUE_MAX`; sellout assertions: 67 sold-out + 159 not-sold-out.
- **0 failures** across all three fetches.

## Semantic guards (regression-tested)

- Multi-show aggregates are **never divided** and **never promoted**.
- Touring Data `estimated` rows are **never promoted** as observations.
- Pollstar "Tickets Sold" promotes to `PAID_TICKETS`, never the broader
  `TICKETS_SOLD` — comps are excluded by Pollstar's own reporting policy.
- `REPORTED_ATTENDANCE` (Billboard/Touring Data) is never relabeled into
  `PAID_ATTENDANCE` or `SCANNED_ATTENDANCE`; the paid-vs-scanned distinction
  is unknown and stays unknown.
- Sellout assertions come only from the source's own `Sellouts` column, never
  inferred from capacity/offsale/secondary prices.
- Every promoted claim is `RESEARCH_ONLY` or `TERMS_REVIEW_REQUIRED`; the
  commercial-eligible corpus is **always zero** (fail-closed).

## Research vs commercial corpus

This corpus is **research-only by construction**. The research/commercial
split is explicit in the OA report (`research_corpus` vs
`commercial_eligible_corpus = 0`, verdict `FAIL_CLOSED`). Common Crawl and
secondary aggregators do not confer rights over the underlying publisher; a
future production box-office feed requires a licensed Pollstar API / Data
Cloud or an authorized customer settlement feed.

## What this unblocks

For the first time the claim ledger holds a meaningful number of real
reported box-office outcomes — headcount, gross, price, sellout, show count,
promoter — across arena, theater, and club scale (not just stadiums). That is
enough to begin a **narrow baseline research study** once the research-readiness
threshold is evaluated honestly. It is **not** yet a modeling license: the
corpus is still selection-biased (chart-ranked engagements only) and
research-only.

## Files

| Path | Purpose |
| --- | --- |
| `schema/migrations/015_public_boxscore_research_corpus_v1.sql` | `research.boxoffice_engagements` (append-only) |
| `python/festival_bloomberg/research/boxscore.py` | engagement model + Billboard/Pollstar/Touring Data parsers |
| `python/festival_bloomberg/research/repository.py` | persist + promote single-show → outcome claims |
| `python/festival_bloomberg/research/acquisition.py` | source registry + corpus report |
| `python/festival_bloomberg/oa/boxscore.py` | live OA driver (bounded, $0) |

## Cost

**$0.00.** No Monid, no Apify, no paid calls. All three sources are publicly
reachable.
