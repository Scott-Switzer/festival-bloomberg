# Historical Laboratory V1

The Historical Laboratory is the first **source-backed historical outcome
ledger** for Festival Bloomberg. Its job is to produce a defensible dataset
against which future underwriting models can be falsified — not to predict
anything yet.

No attendance, revenue, guarantee, or CVaR models exist yet. They are gated
on this corpus.

## Why this exists

Event underwriting needs *real outcomes*: attendance, tickets, gross, deal,
settlement, capacity, and event status — each tied to a point-in-time
decision cutoff. A "Bloomberg for music" that cannot tell the difference
between capacity and attendance, or between a sold-out show and latent
demand, is not finance-grade.

This milestone fixes the data semantics before any model is allowed to read
them.

## Architecture

```
RAW EVIDENCE (setlist.fm / Wikidata / OSM / Common Crawl / private CSV)
        │
        ▼
PARSED CLAIM  (controlled outcome taxonomy + semantic guards)
        │
        ▼
economics.event_outcome_claims  (append-only ledger, never overwritten)
        │
        ├─ conflict groups (conflicting sources coexist)
        ├─ supersession (recorded, never deleted)
        ├─ decision cutoffs (booking / announcement / onsale / event)
        └─ coverage + quality + PIT + selection-bias reports
```

New modules:

| Module | Purpose |
| --- | --- |
| `economics/outcome_claims.py` | Controlled taxonomy, source grading, semantic guards, censoring helpers |
| `economics/repository.py` | `event_outcome_claims` + `event_decision_cutoffs` persistence |
| `economics/private_import.py` | Generic private historical outcome CSV import |
| `economics/laboratory.py` | Data quality / coverage / PIT / selection-bias reports |
| `acquisition/providers/commoncrawl.py` | Common Crawl CDX historical-web lookup |
| `acquisition/providers/eventbrite.py` | Eventbrite provider contract (customer-authorized only) |
| `oa/historical_laboratory.py` | Live operational-acceptance driver |

Migration `013_historical_outcome_laboratory_v1.sql` adds the two append-only
tables. The existing coarse `event_outcome_observations` table is untouched.

## Semantics (the point of the milestone)

These are enforced by validation, not by convention:

- **capacity ≠ attendance** — a capacity claim carrying an attendance
  definition is rejected.
- **paid ≠ scanned ≠ reported** attendance are three distinct outcome types.
- **tickets sold ≠ attendance**.
- **OFFSALE ≠ SOLD_OUT** — a sold-out assertion is its own explicit type.
- **permit capacity ≠ attendance** — `PERMIT_CAPACITY_LIMIT` is separate from
  every attendance type.
- **expected attendance is not a claim type at all**.
- **setlist presence ≠ attendance** — a setlist proves `EVENT_PERFORMED`,
  nothing more.

### Outcome taxonomy

- Attendance: `PAID_ATTENDANCE`, `SCANNED_ATTENDANCE`, `REPORTED_ATTENDANCE`
- Tickets: `TICKETS_SOLD`, `PAID_TICKETS`, `COMP_TICKETS`, `REFUNDED_TICKETS`
- Sold-out: `EXPLICIT_SOLD_OUT_ASSERTION`, `EXPLICIT_NOT_SOLD_OUT_ASSERTION`
- Revenue: `TICKET_GROSS`, `TICKET_NET`, `PRIMARY_FACE_VALUE_MIN/MAX`,
  `MERCH_REVENUE`, `FNB_REVENUE`, `PARKING_REVENUE`, `VIP_REVENUE`,
  `SPONSOR_REVENUE`
- Capacity: `VENUE_CAPACITY`, `EVENT_USABLE_CAPACITY`, `PERMIT_CAPACITY_LIMIT`
- Cost/deal: `ARTIST_GUARANTEE`, `ARTIST_BACKEND`, `PROMOTER_COST`,
  `MARKETING_SPEND`, `PRODUCTION_COST`, `LABOR_COST`, `VENUE_COST`
- Settlement: `PROMOTER_CONTRIBUTION`, `SETTLEMENT_GROSS`, `SETTLEMENT_NET`
- Event status: `EVENT_PERFORMED`, `EVENT_CANCELLED`, `EVENT_POSTPONED`

### Source quality grading (independent of confidence)

`A_PRIMARY_SETTLEMENT`, `A_PRIMARY_TICKETING`, `A_PRIMARY_GOVERNMENT`,
`A_PRIMARY_PROMOTER`, `A_PRIMARY_VENUE`, `B_REPUTABLE_INDUSTRY_REPORT`,
`B_REPUTABLE_NEWS`, `C_OTHER_PUBLIC_REPORT`, `D_INFERRED`, `D_WEAK`,
`UNKNOWN`.

A source can be authoritative but ambiguous, so `source_quality` and
`claim_confidence` are stored separately.

## PIT rules

Every claim carries both `source_as_of` (when the underlying fact was
published/captured) and `knowledge_time` (when *we* retrieved it). Cuts are
made on `knowledge_time`; a 2019 fact first read from a 2021 page does **not**
enter a 2019 booking feature set.

Decision cutoffs: `BOOKING_CUTOFF`, `ANNOUNCEMENT_CUTOFF`, `ONSALE_CUTOFF`,
`EVENT_CUTOFF`. For the current Chicago corpus only `event_cutoff` is
reconstructed; the others are NULL (unknown), which is the honest state.

## Sources and rights

Rights are per-source and fail closed (UNKNOWN unless positively known):

| Source | Rights | Notes |
| --- | --- | --- |
| Setlist.fm | `RESEARCH_ONLY` | API terms are non-commercial |
| Wikidata (P1083) | `OPEN_COMMERCIAL_OK` | CC0 |
| OpenStreetMap | `OPEN_WITH_ATTRIBUTION` | ODbL |
| Common Crawl | `UNKNOWN` | archive availability ≠ rights to the underlying page |
| Private CSV | `UNKNOWN` | customer data, `OBSERVED_PRIVATE` |

Public (`OBSERVED_PUBLIC`) and private (`OBSERVED_PRIVATE`) observations are
never merged implicitly.

## Censoring

A sold-out show whose `tickets_sold == usable_capacity` does **not** reveal
latent demand. It is labeled `is_censored = TRUE`, `censoring_type = RIGHT`,
`threshold = usable_capacity`. No censored-demand model is built yet; the
labels only make the data model-ready.

## Chicago corpus (live OA result)

Geography is restricted to Chicago city proper. The corpus is the OA10
artist × Chicago event graph (Setlist.fm-derived) plus free capacity
enrichment.

- **95 events discovered** (Beyoncé, Taylor Swift, Kendrick Lamar,
  Travis Scott, Post Malone, Sabrina Carpenter, Billie Eilish, Olivia
  Rodrigo, Bad Bunny; 2003–2026).
- **95 events with ≥1 claim.**
- **150 claims**: `EVENT_PERFORMED` = 95, `VENUE_CAPACITY` = 55.
- Attendance, tickets, gross, guarantee, promoter contribution: **UNKNOWN**
  (not fabricated) — this is the current honest coverage gap.
- Cost: **$0.00** (Wikidata/OSM/Wikipedia are free; no Monid/Apify runs).

## Known gaps

- No paid box-office / settlement data (Pollstar/Billboard Boxscore are paid).
- Booking/announcement/onsale cutoffs are unknown for most historical events.
- Capacity coverage is partial (some venues have no Wikidata/OSM record).
- Eventbrite is a provider contract only (no live authorized run yet).

## Selection bias

Coverage is strongly concentrated on major artists and large Chicago venues
(United Center 35, Soldier Field 16). Small-club and independent shows are
under-represented. Any future model trained here must treat this as
survival/selection bias, not a neutral sample.

## Next modeling gate

Models (attendance, revenue, guarantee, CVaR) may begin only when outcome
coverage — especially paid attendance, tickets sold, and gross — is
materially better than the current near-zero. That requires a licensed
box-office feed, authorized customer data, or both. The laboratory is now
ready to receive them without corrupting the semantics.
