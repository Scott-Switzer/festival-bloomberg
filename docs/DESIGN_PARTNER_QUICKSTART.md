# Design Partner Quickstart

**Goal:** a promoter can hand us their historical show history in 15 minutes, and get
back a private, point-in-time retrospective of what was knowable before each booking
versus what actually happened.

> Pitch: *"Give us your past shows. We reconstruct what was knowable before each
> booking and show which signals, comps and assumptions actually lined up with the
> outcome."*

## What to send

One file: `examples/design_partner_show_history_template.csv`

- CSV, TSV, or XLSX all work.
- **Only fill what you have.** Every blank cell stays `UNKNOWN` — we never guess.
- Delete the example rows (if the file has any) before importing your own.

## What fields help most

| Priority | Fields | Why |
|---|---|---|
| Required-ish | `artist_name`, `event_date`, `venue_name`, `market` | Lets us resolve to the artist/vendor and reconstruct decision-time evidence |
| High value | `onsale_date` / `announcement_date` / `booking_date` / `offer_date` | Defines the decision cutoff for point-in-time reconstruction |
| High value | `tickets_sold`, `ticket_gross`, `ticket_net`, `average_paid_ticket` | The realized outcome |
| High value | `artist_guarantee`, `artist_backend_pct`, `deal_type` | The deal math actually agreed |
| Nice to have | `venue_capacity`, `event_usable_capacity`, `ticket_capacity`, `paid_tickets`, `comp_tickets`, `scanned_attendance`, `paid_attendance` | Sell-through and utilization |
| Nice to have | `marketing_spend`, `production_cost`, `venue_cost`, `labor_cost`, `security_cost`, `insurance_cost`, `other_cost`, `merch_revenue`, `fnb_revenue`, `parking_revenue`, `vip_revenue`, `sponsor_revenue`, `other_revenue`, `promoter_contribution`, `settlement_gross`, `settlement_net` | Contribution and margin |
| Optional | anything else (notes, currencies) | Kept per-row, never forced into a model |

## Privacy contract

- **Default: `PRIVATE_ONLY`** — imported rows live in your local workspace DB, never
  in the public serving DuckDB, never in the public R2 terminal generation, never in
  the Demo.
- **PII quarantine** — email addresses, phone numbers, and similar identifiers are
  caught at import, logged to a quarantine ledger, and never written into the outcome
  tables. Don't bother scrubbing, but don't expect those columns to be stored.
- **Architecture** — public serving evidence and private workspace outcomes are joined
  only at query time. Nothing private crosses into the shared artifact.

## What you receive back (immediately — no model training)

- Artist / market / venue **historical outcome strips** (sell-through, gross,
  guarantee, contribution distributions where supplied).
- **Comparable-show outcomes** from your own history, labeled `OBSERVED_PRIVATE`.
- **Point-in-time backtest** per show: *at booking* evidence (prior market plays,
  attention moves, comp rooms) vs *actual* outcome — with the explicit caveat that
  this is reconstruction, not causality.
- A **MODEL_READINESS.json** ledger showing exactly how many settled shows exist and
  whether a calibrated forecast is even warranted yet (spoiler: under 200 useful
  outcomes means **no model** — just evidence and math).

## How long it takes

1. Copy the template (~2 min).
2. Fill from your ticketing/settlement exports (~10 min).
3. Upload in **BACKTEST** → preview → confirm mapping → import (~3 min).
4. Read your retrospective (instant).

## Current limitations (honest)

- Private outcomes are never shared with other buyers (unlike pooled products).
- PIT reconstruction depends on cutoffs being present in your file; shows without one
  are still imported but not OOS-eligible.
- No calibrated forecasts until the readiness thresholds are met.