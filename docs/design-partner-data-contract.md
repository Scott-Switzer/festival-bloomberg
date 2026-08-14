# Design Partner Data Contract

The canonical contract for a promoter / venue group / festival operator's
historical event file. No field is required; missing values remain NULL and
are reported as such. Real files rarely use these exact names, so the import
layer maps arbitrary headers conservatively (ambiguous headers require review).

## Identity

| field | definition | type |
| --- | --- | --- |
| `customer_event_id` | customer's unique event identifier (preferred dedup key) | text |
| `artist_name` | headline artist display name | text |
| `artist_external_id` | customer's artist identifier | text |
| `venue_name` | venue display name | text |
| `venue_external_id` | customer's venue identifier | text |
| `market` | market label (e.g. Chicago) | text |
| `city` | city proper | text |
| `state` | state / province | text |
| `country` | country code | text |
| `event_date` | event local date (`YYYY-MM-DD`) | date |
| `event_time` | event local time | text |
| `timezone` | IANA timezone | text |

## Decision timing (drives PIT reconstruction)

| field | definition | type |
| --- | --- | --- |
| `offer_date` | date the offer was made | date |
| `booking_date` | date the booking was confirmed | date |
| `announcement_date` | date the show was publicly announced | date |
| `presale_date` | presale start date | date |
| `onsale_date` | public on-sale date | date |

## Event configuration

| field | definition | type |
| --- | --- | --- |
| `venue_capacity` | venue maximum capacity | number |
| `event_usable_capacity` | event-specific usable capacity (configuration) | number |
| `ticket_capacity` | tickets placed on sale | number |
| `configuration_name` | seating / GA configuration name | text |
| `event_status` | performed / cancelled / postponed | text |

## Deal

| field | definition | type |
| --- | --- | --- |
| `deal_type` | deal structure label | text |
| `artist_guarantee` | artist guarantee | money |
| `artist_backend_pct` | artist backend percentage | percent |
| `artist_bonus` | artist bonus | money |
| `artist_expenses` | artist-expense allowance | money |

## Ticketing

| field | definition | type |
| --- | --- | --- |
| `tickets_sold` | tickets sold (all types) | number |
| `paid_tickets` | paid tickets | number |
| `comp_tickets` | complimentary tickets | number |
| `refunded_tickets` | refunded tickets | number |
| `scanned_attendance` | scanned / checked-in attendance | number |
| `paid_attendance` | paid attendance | number |
| `reported_attendance` | reported attendance | number |
| `ticket_gross` | ticket gross revenue | money |
| `ticket_net` | ticket net revenue | money |
| `average_paid_ticket` | average paid ticket price | money |
| `face_value_min` | minimum face value | money |
| `face_value_max` | maximum face value | money |
| `sold_out` | explicit sold-out flag (`true` / `false`) | boolean |

## Costs

`marketing_spend`, `venue_cost`, `production_cost`, `labor_cost`,
`security_cost`, `insurance_cost`, `other_cost` (all money).

## Ancillary revenue

`merch_revenue`, `fnb_revenue`, `parking_revenue`, `vip_revenue`,
`sponsor_revenue`, `other_revenue` (all money).

## Settlement

| field | definition | type |
| --- | --- | --- |
| `promoter_contribution` | promoter net contribution | money |
| `settlement_gross` | settlement gross | money |
| `settlement_net` | settlement net | money |

## Metadata

`currency` (ISO code), `source_system`, `source_file`, `notes`.

## Semantic rules (never silently violated)

- `attendance` is never mapped to `tickets_sold`.
- `gross` is never mapped to `promoter_contribution`.
- `cap` is never mapped to `paid_attendance`.
- capacity claims carry a capacity definition, never an attendance definition.
- `OFFSALE` is never `SOLD_OUT`; sold-out is only an explicit assertion.
- multi-show totals are never divided into event-level values without evidence.

## Privacy

All imported fields are customer-private (`OBSERVED_PRIVATE`). Buyer-level PII
(name, email, phone, street address, card data, full transaction ids) is
quarantined on sight and never read into analytical tables. Pooling into any
shared corpus is opt-in only (default `PRIVATE_ONLY`).
