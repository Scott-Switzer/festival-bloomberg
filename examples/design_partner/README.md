# Design Partner Onboarding Package

This folder is a **self-serve onboarding package** for a prospective design
partner (independent promoter, venue group, regional festival operator, etc.).

Everything here is **synthetic / example data** — no real promoter is
represented. Real partner files are never stored in this repository.

## What to send us

A sanitized spreadsheet of your **historical events**. The minimum useful file
has one row per show with, at minimum:

| field | meaning |
| --- | --- |
| `customer_event_id` | your internal show ID (any string) |
| `artist_name` | billed artist |
| `venue_name` | venue |
| `city` / `market` | location |
| `event_date` | show date |

The more of these you can add, the more useful the analysis:

- **decision dates** — `offer_date`, `booking_date`, `announcement_date`, `onsale_date`
- **capacity** — `venue_capacity`, `event_usable_capacity`, `ticket_capacity`
- **tickets** — `tickets_sold`, `paid_tickets`, `comp_tickets`, `scanned_attendance`
- **money** — `ticket_gross`, `ticket_net`, `artist_guarantee`, `promoter_contribution`

## What we do NOT want

- customer / buyer PII (names, emails, phone, addresses)
- payment-card information
- anything unrelated to the event economics

PII columns are detected and quarantined automatically — their values are
never read into analytics.

## Templates

- [`events_template.csv`](events_template.csv) — full event header
- [`settlements_template.csv`](settlements_template.csv) — settlement / cost header
- [`ticket_pace_template.csv`](ticket_pace_template.csv) — sales-pace header
- [`example_synthetic_events.csv`](example_synthetic_events.csv) — a small
  **synthetic** example you can mirror

## How to preview your file locally

```bash
PYTHONPATH=python .venv/bin/python -m festival_bloomberg.cli.main \
  partner preview --files /path/to/your_events.csv --customer my_company
```

This writes a `summary.json` (structural coverage + readiness tier) against an
**isolated** DuckDB. It never touches the public warehouse and never makes
predictions. No private data leaves your machine.

## Readiness tiers

| tier | what it means |
| --- | --- |
| `STRUCTURAL_ONLY` | we can see your entities, but not enough labels/cutoffs yet |
| `RETROSPECTIVE_RESEARCH_USABLE` | attendance + decision cutoffs let us reconstruct PIT research |
| `ECONOMICS_USABLE` | tickets + gross + deal/settlement evidence for benchmarking |
| `UNDERWRITING_RESEARCH_CANDIDATE` | enough labelled outcomes for underwriting *research* (not a recommendation) |

Row count alone never advances a tier. Readiness also depends on dataset
**breadth** (distinct artists/venues/markets), so a single-venue dataset is
scoped `VENUE_SPECIFIC` and is never presented as broadly generalizable. The
`partner value` curves are `SYNTHETIC_STRUCTURAL_SCENARIO` — illustrative
planning targets, not guarantees.
