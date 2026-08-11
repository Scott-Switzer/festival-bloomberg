# Secondary-Market Ticket Arbitrage: Research and System Specification

Status: Design specification and research memorandum
Date: Tuesday, August 11, 2026
Audience: quantitative researchers, data engineers, product owners, and institutional live-entertainment investors
Scope: festival-bloomberg secondary-market observations, price normalization, arbitrage detection, and acquisition-grade data controls

## Executive Summary

A secondary-market ticket price is not an executable economic value until the system knows what the price includes, which currency it is denominated in, whether the inventory is still available, what ticket quantity and delivery constraints apply, and when the observation was retrieved. The central design requirement is therefore not merely to scrape a number; it is to preserve an auditable chain from listing observation to normalized, fee-adjusted, FX-adjusted, freshness-qualified comparison.

The system should treat every market observation as an assertion with provenance and uncertainty rather than as a mutable row representing the current truth. A listing may change price, quantity, section, delivery method, or availability while retaining a platform identifier. A source may expose an all-in buyer price, a seller-set price, a price excluding fees, or only a minimum/representative price. These are different economic objects and must not be conflated.

The proposed design has five principles:

1. Store immutable observations. Never overwrite a prior observation merely because a listing identifier is unchanged.
2. Separate displayed price, mandatory fees, seller proceeds, tax, delivery charges, and total buyer payable amount.
3. Refuse to manufacture arbitrage signals when currency, fee basis, inventory state, FX direction, or retrieval freshness is unknown.
4. Make every derived metric reproducible from raw observations, fee-policy versions, FX observations, and matching decisions.
5. Report opportunity quality and execution risk alongside spread. A nominal spread is not an investable opportunity without inventory, settlement, and operational feasibility.

The economic output should be a ranked set of qualified opportunities, not a raw list of unusually cheap tickets. The database becomes strategically valuable to an institutional acquirer when it can demonstrate persistent, venue- and event-level pricing intelligence; distinguish true demand or supply signals from data artifacts; and quantify the value of distribution, primary inventory, promoter relationships, and controlled ticketing channels.

## 1. Research Basis and Terminology

### 1.1 Primary and secondary markets

SeatGeek describes the secondary market as tickets being resold after their original purchase from a box office or primary market. This distinction matters because a low secondary price can reflect resale supply, transfer restrictions, an organizer release, speculative inventory, or a stale listing rather than weak primary demand.

SeatGeek’s public support material says that eligible tickets may be listed through its marketplace, that sellers choose listing price and buyers choose whether to purchase, and that the marketplace supports resale rather than primary issuance. The production model must still record whether an observed listing is marked resale, primary, partner inventory, or unknown because a platform’s event page can aggregate multiple inventory channels.

### 1.2 Economic price fields

Use the following vocabulary:

- displayed_price: the amount visibly associated with the listing at retrieval.
- buyer_fee: mandatory fee charged to the buyer, if separately disclosed.
- seller_fee: amount withheld from seller proceeds, if separately disclosed.
- tax: tax or government charge, if disclosed and applicable.
- delivery_fee: delivery or fulfillment charge, if disclosed.
- buyer_total: mandatory amount payable by the buyer for the specified quantity and delivery option.
- seller_proceeds: amount the seller receives after seller-side deductions.
- face_value: primary-market price, if observed and attributable to the same ticket class.
- unit_price: price divided by the minimum quantity purchased in the listing.
- all_in_status: explicit, inferred, excluded, or unknown.
- fee_confidence: observed, policy-derived, estimated, or unknown.

A price comparison must declare its basis. For example, comparing SeatGeek buyer_total against a different marketplace’s seller-set displayed_price is invalid unless a fee transformation is applied and documented.

### 1.3 Source hierarchy

The preferred evidence hierarchy is:

1. Transaction or checkout-confirmed total for the exact listing and quantity.
2. Explicit listing-level all-in price with mandatory-fee inclusion documented.
3. Listing-level price plus separately observed mandatory fees.
4. Platform policy estimate applied to a listing-level price, labelled estimated.
5. Event-level minimum or representative price, used only for discovery and never treated as exact cross-platform arbitrage.
6. Search-engine snippets, cached pages, third-party datasets, or inferred values, used only as weak corroboration.

Every observation should preserve source URL, retrieval timestamp, parser version, raw response hash, and extraction confidence.

## 2. SeatGeek Secondary-Market Fee and Listing Mechanics

### 2.1 What is known and what must remain unknown

SeatGeek’s support documentation states that it uses all-in pricing for buyers: the event-page price is intended to include mandatory fees, allowing comparison of the total ticket cost before checkout. SeatGeek also documents a seller flow in which the seller selects tickets, number of tickets, split options, listing price, and payout. This establishes an important distinction:

- buyer-facing all-in price is an acquisition cost for the buyer;
- seller listing price is an input to the marketplace and is not automatically the seller’s net proceeds;
- seller payout is a separate economic field;
- the fee schedule and treatment can vary by context, location, event, account, product, or policy version.

The system must not hard-code a universal SeatGeek fee percentage. Public discussions and historical articles may report particular percentages or fee arrangements, but those reports are not sufficient evidence for a current listing. A current listing should be classified as actual only when the listing or checkout exposes the relevant amount. A platform policy statement may support a policy-derived estimate, but not an exact claim about an individual transaction.

The following values must be represented independently:

```text
buyer_total = displayed_ticket_component
             + mandatory_buyer_fee
             + tax
             + delivery_fee
             + other_mandatory_charges

seller_proceeds = seller_list_price
                  - seller_fee
                  - other_seller_deductions
```

If all-in pricing is explicit and the displayed figure is per ticket, buyer_total can be populated with confidence observed. If the displayed figure is an event-level “from” price, it must not be promoted to listing-level buyer_total.

### 2.2 Fee state machine

Each listing should carry a fee state:

- fee_observed_all_in: source explicitly states that the displayed amount includes mandatory charges for the selected listing and quantity.
- fee_observed_breakout: source provides displayed amount and fee components.
- fee_policy_estimated: a documented policy model estimates the missing component.
- fee_partial: some mandatory components are known but others are not.
- fee_hidden: checkout or listing context indicates additional fees may exist, but the amount is unavailable.
- fee_unknown: no reliable inference is possible.
- fee_not_applicable: the source is a seller-proceeds or face-value observation where buyer fee is not relevant to the metric.

Only fee_observed_all_in and fee_observed_breakout should be eligible for a strict executable buyer-cost comparison. fee_policy_estimated may be eligible for research ranking when the uncertainty interval remains below the anomaly threshold. fee_partial, fee_hidden, and fee_unknown must be excluded from hard arbitrage signals.

### 2.3 Estimated versus actual fees

The data model should retain both point estimates and intervals:

```text
fee_amount_actual: nullable decimal
fee_amount_estimate: nullable decimal
fee_lower_bound: nullable decimal
fee_upper_bound: nullable decimal
fee_basis: enum(observed, policy, historical, inferred, unknown)
fee_policy_version: nullable string
fee_observed_at: nullable timestamp
fee_confidence: decimal in [0, 1]
```

For an estimated fee, calculate a price interval rather than a false point value:

```text
buyer_total_low  = displayed_price + mandatory_cost_low
buyer_total_high = displayed_price + mandatory_cost_high
```

An opportunity should be called positive only if the conservative bound remains positive after all costs. If A is the candidate cheap listing and B is the reference listing:

```text
spread_low = buyer_total_B_low - buyer_total_A_high
spread_high = buyer_total_B_high - buyer_total_A_low
```

A positive signal requires spread_low greater than transaction costs, execution buffer, and anomaly tolerance. If only spread_high is positive, the result is a research lead, not arbitrage.

### 2.4 Hidden and unknown fees

Unknown fees are not zero. The correct representation is null plus a reason code, not 0.00. Zero means an observed and verified absence; null means the system does not know.

When mandatory fees are hidden:

1. preserve the raw displayed amount;
2. set all_in_status to unknown or hidden;
3. assign a conservative fee interval only if a documented policy or repeated same-context observations support it;
4. lower confidence and increase the required anomaly tolerance;
5. prevent inclusion in an executable opportunity feed unless the final cost interval clears the threshold;
6. schedule a re-fetch or checkout verification if the event is material.

A missing fee should also generate a data-quality flag. This permits institutional users to distinguish “no fee” from “fee not observed,” which is essential for backtests and auditability.

### 2.5 Sellout and low-inventory context

Sellout is not equivalent to “no listing was returned.” A platform may be sold out of primary inventory while secondary listings remain; an event page may show a minimum price even when only one listing is available; a page may be inaccessible, rate-limited, geo-restricted, or temporarily empty.

Define an inventory state separately for primary and secondary channels:

```text
primary_inventory_state:
  available | sold_out_explicit | unavailable_unknown | not_observed

secondary_inventory_state:
  listings_present | no_listings_explicit | unavailable_unknown | not_observed

sellout_evidence:
  explicit_banner | inventory_endpoint | checkout_failure |
  repeated_empty_results | inferred_from_zero_inventory | none
```

A sellout assertion should require explicit source evidence or repeated observations satisfying a configured rule. A single empty response is not sellout evidence.

For event-level reporting, maintain:

- timestamp of first explicit sellout observation;
- timestamp of last observed primary availability;
- number of secondary listings;
- number of distinct listing IDs;
- lowest qualified all-in price;
- median and quantile prices by ticket class;
- observation coverage and failure counts.

Sellout context changes interpretation. A secondary premium over face value in a confirmed primary sellout is a scarcity measure. A low resale price during primary availability may be a discount or may simply be a different section, delivery method, or fee basis. The anomaly engine must not pool these regimes without a state indicator.

### 2.6 Listing normalization and comparability

A listing is comparable only when the following dimensions match or are explicitly modelled:

- event and performance date/time;
- venue and market;
- ticket quantity and split constraints;
- section, row, seat or a defensible seat-quality bucket;
- general-admission versus reserved seating;
- delivery method and transfer restrictions;
- currency;
- buyer/seller price basis;
- observed availability status;
- source retrieval freshness.

For general admission, section/row may be absent but capacity tier, access class, VIP benefits, and delivery restrictions remain relevant. A “from” price can be used to locate an event but cannot establish a tradeable pair.

### 2.7 Pricing anomaly tolerance

A pricing anomaly is a discrepancy large enough to survive fee uncertainty, FX uncertainty, timing drift, ticket-quality mismatch, and execution costs. Define a tolerance budget:

```text
T = T_fee + T_fx + T_stale + T_quality + T_execution + T_model
```

Where each term is measured as a relative or absolute amount in the reporting currency. A candidate pair qualifies only when:

```text
conservative_net_spread > max(absolute_minimum_spread,
                               T * reference_value)
```

Recommended initial research defaults, subject to calibration:

- absolute minimum net spread: USD 25 per ticket for consumer-scale observations;
- relative minimum net spread: 8% of the conservative reference buyer_total;
- estimated-fee uncertainty surcharge: 3% of displayed price when policy-derived, unless empirically calibrated;
- stale-data surcharge: 1% per freshness bucket, capped by a hard exclusion age;
- listing-quality mismatch: hard exclusion rather than a numeric surcharge when section or access class is unknown.

These are starting governance parameters, not claims about SeatGeek pricing behaviour. Calibrate them using paired observations and realized availability, with separate thresholds by event class and days-to-event. For a low-priced event, the absolute threshold prevents noise from rounding and small fee differences. For a high-priced event, the relative threshold prevents a fixed dollar threshold from being too permissive.

## 3. Multi-Currency and FX Arbitrage Safety

### 3.1 Currency is mandatory for cross-currency comparison

An observation with a numeric price but missing currency is not a USD observation. Currency may be inferred from an ISO code, a trusted locale-specific source, a currency symbol with unambiguous market context, or a platform metadata field. A symbol alone is insufficient where currencies share symbols or where locale formatting is ambiguous.

Required fields:

```text
price_amount: decimal > 0
price_currency: ISO 4217 code, non-null for normalized comparisons
currency_source: explicit | metadata | locale_inferred | symbol_inferred | unknown
currency_confidence: [0, 1]
```

If currency is missing or confidence is below the configured threshold, the listing may remain in the raw dataset but must be excluded from FX-normalized arbitrage.

### 3.2 No fallback FX rate in the signal path

A fallback exchange rate can be useful for a display estimate, but it must never silently enter an executable arbitrage calculation. The following are separate concepts:

- display_fx_rate: approximate rate used only for user interface context;
- research_fx_rate: rate used for exploratory historical analysis and explicitly marked;
- signal_fx_rate: fresh, sourced, directionally validated rate eligible for arbitrage.

If the requested pair is unavailable, do not substitute a stale rate, a hard-coded rate, a same-day but different timestamp rate, or an arbitrary USD bridge without recording the cross calculation and all component timestamps. The safe result is unknown.

### 3.3 Pair direction and inverse-rate resolution

Represent rates canonically as quote currency per one base currency:

```text
rate(base=EUR, quote=USD) = USD per EUR
```

Conversion is:

```text
amount_quote = amount_base * rate(base, quote)
```

If only the inverse is available:

```text
rate(base, quote) = 1 / rate(quote, base)
```

The implementation must retain:

```text
fx_base_currency
fx_quote_currency
fx_rate
fx_direction: direct | inverted | triangulated
fx_source
fx_observed_at
fx_retrieved_at
fx_rate_id
```

Never infer direction from field names such as “price” or “value.” Test the rate using a known sanity check and reject nonpositive, zero, NaN, or extreme values. A pair request must be normalized before lookup, and a direct match must take precedence over an inverse match.

Triangulation through a reporting currency is permissible only when explicitly enabled:

```text
rate(EUR, GBP) = rate(EUR, USD) * rate(USD, GBP)
```

Both legs must pass freshness, source-quality, and timestamp alignment checks. The result must be labelled triangulated and carry both source identifiers. Triangulation should incur an uncertainty surcharge and may be prohibited for small spreads.

### 3.4 Retrieval freshness

A ticket observation and FX observation are time-sensitive. Store both source timestamp and system retrieval timestamp because an API may publish a timestamp that differs from when the system received it.

Recommended eligibility rule:

```text
abs(ticket_retrieved_at - fx_effective_at) <= 15 minutes
now - ticket_retrieved_at <= 10 minutes
now - fx_retrieved_at <= 10 minutes
```

For event-day or high-volatility environments, use a five-minute window. For historical backtests, use only FX observations available at or before the ticket observation timestamp; never use a later daily close to normalize an earlier ticket price unless the research explicitly models that information delay.

A stale observation can still be retained for trajectory analysis, but its use must be limited:

- current arbitrage: hard reject when stale;
- descriptive history: retain with stale flag;
- backtest: use only if it was timely relative to the simulated decision time;
- model training: include age as a feature or censor observations beyond the horizon.

### 3.5 FX-adjusted spread formula

For a candidate listing A and reference listing B, both converted to reporting currency R:

```text
cost_A_R = buyer_total_A * fx(A.currency, R)
cost_B_R = buyer_total_B * fx(B.currency, R)
net_spread_R = cost_B_R - cost_A_R - costs_R
```

For uncertainty-aware valuation:

```text
cost_A_high = buyer_total_A_high * fx_A_high
cost_B_low  = buyer_total_B_low  * fx_B_low
net_spread_low = cost_B_low - cost_A_high - costs_high
```

The opportunity is qualified only when net_spread_low exceeds both absolute and relative thresholds. FX risk should include bid/ask or conversion-cost assumptions where the user must actually convert funds. A mid-market rate is not necessarily executable.

### 3.6 FX validation tests

The test suite should include:

1. Missing currency results in no normalized value and no arbitrage signal.
2. EUR/USD direct lookup is not confused with USD/EUR inverse lookup.
3. Inverse rates multiply to approximately one within configured tolerance.
4. Triangulated rates carry both source observations and are rejected if either leg is stale.
5. A later FX timestamp cannot leak into an earlier backtest observation.
6. A stale fallback never changes a rejected opportunity to qualified.
7. Currency symbols with ambiguous interpretation remain unknown.
8. Zero, negative, NaN, and implausibly large rates are rejected.

## 4. Changed Listing Immutability and Stale Data

### 4.1 The listing identifier is not the observation identity

A marketplace listing ID identifies a marketplace object, not a point in time. The same listing may change price, quantity, seat allocation, delivery method, or status. Therefore:

```text
listing_id != observation_id
```

The observation primary key should be a generated identifier, or a deterministic hash of listing_id, retrieved_at bucket, payload hash, and source context. The listing dimension stores slowly changing identity attributes; the observation fact stores every observed state.

### 4.2 Proposed relational model

```sql
create table secondary_listing (
  listing_key             varchar primary key,
  source                  varchar not null,
  source_listing_id       varchar not null,
  event_key               varchar not null,
  first_seen_at            timestamp not null,
  last_seen_at             timestamp not null,
  current_status          varchar not null,
  identity_confidence     decimal not null,
  unique(source, source_listing_id)
);

create table secondary_listing_observation (
  observation_key         varchar primary key,
  listing_key              varchar not null,
  observed_at              timestamp not null,
  retrieved_at             timestamp not null,
  payload_hash             varchar not null,
  status                   varchar not null,
  quantity                 integer,
  split_type               varchar,
  section                  varchar,
  row_label                varchar,
  seat_quality_bucket      varchar,
  displayed_amount         decimal,
  displayed_currency       varchar,
  buyer_total              decimal,
  buyer_total_low          decimal,
  buyer_total_high         decimal,
  seller_proceeds          decimal,
  all_in_status            varchar not null,
  fee_basis                varchar not null,
  raw_payload_uri          varchar,
  parser_version           varchar not null,
  quality_flags            varchar[]
);
```

The uniqueness constraint should be on source_listing_id plus a payload or state hash, not listing ID alone. If the same state is retrieved repeatedly, deduplicate the exact duplicate while retaining retrieval audit counts. If the state changes, insert a new immutable observation.

### 4.3 Price trajectory

For a listing, construct a trajectory ordered by observed_at and retrieved_at. Each transition should identify changed fields:

```text
price_changed
quantity_changed
seat_changed
delivery_changed
status_changed
fee_basis_changed
currency_changed
```

The first and last observations should not be treated as a complete lifecycle unless the source coverage supports it. A listing absent from one poll is not necessarily sold; it may be temporarily unavailable or omitted due to pagination. Use explicit status when available and a separate not_seen state otherwise.

Useful trajectory metrics include:

- initial displayed price and buyer_total;
- current qualified price;
- minimum and maximum observed price;
- price volatility and duration-weighted average;
- time at each price state;
- number of price changes;
- time from price change to disappearance;
- probability of reappearance;
- observed sell-through proxy, clearly labelled as a proxy.

Do not impute a sale from disappearance unless the source provides a sold or removed status. Disappearance is censored data.

### 4.4 Deduplication without historical loss

Apply deduplication at three layers:

1. Transport duplicate: same response payload and retrieval context. Collapse into an audit count.
2. Exact state duplicate: same normalized state hash. Retain one observation plus first_seen, last_seen, and observation_count.
3. Semantic duplicate: two listing IDs appear to represent the same inventory. Never delete either raw record; link them through a match table with confidence and evidence.

A canonical state hash should include the fields that matter economically, including price, currency, fee state, quantity, split, section/row, delivery, status, and event identity. It should exclude volatile metadata such as HTML ordering or request IDs.

### 4.5 Stale observation policy and purging

Purging should mean removing data from the active signal surface, not destroying the historical evidence. Define retention classes:

- raw payloads: retain according to storage and contractual policy;
- immutable observations: retain for research and audit;
- active opportunity index: exclude observations older than the current freshness horizon;
- trajectory summaries: retain after raw observation archival;
- personally identifiable or prohibited seller data: never collect, or delete under the applicable policy.

Example active windows:

- event more than 30 days away: 24-hour active horizon for descriptive monitoring;
- event 30 to 3 days away: 2-hour active horizon;
- event within 72 hours: 15-minute active horizon;
- event day: 5-minute active horizon.

These values should be configuration, not constants in parser code. A stale observation can be marked inactive by a scheduled job:

```text
active = retrieved_at >= now - freshness_horizon(event_time, now)
```

If a source is down, do not silently extend the horizon and present stale prices as current. Surface source outage and coverage age explicitly.

### 4.6 Concurrency and idempotency

Ingestion must be safe under overlapping jobs. Use a source/event/listing-level lock or an idempotency key based on request ID and payload hash. Transactions should insert the observation and update the listing summary atomically. A failed summary update must not erase the observation. Reprocessing a response should be a no-op at the exact-state layer.

## 5. Arbitrage Engine Specification

### 5.1 Opportunity lifecycle

An opportunity should move through explicit states:

```text
candidate
 -> normalized
 -> comparable
 -> fee_qualified
 -> fx_qualified
 -> freshness_qualified
 -> inventory_qualified
 -> net_positive
 -> execution_review
 -> expired | rejected | confirmed | realized
```

A candidate must not skip directly from scraped price to trade recommendation.

### 5.2 Matching hierarchy

Match listings in this order:

1. same event/performance and same inventory identity;
2. same event/performance and same section/seat-quality bucket;
3. same general-admission access class and quantity constraints;
4. event-level comparisons only for descriptive analytics.

Each match receives a comparability score and rejection reasons. A high numeric spread with low comparability is a data-quality exception, not an opportunity.

### 5.3 Fee- and FX-aware calculation

The calculation service should accept immutable observation IDs and return a fully explainable result:

```json
{
  "candidate_observation_id": "...",
  "reference_observation_id": "...",
  "reporting_currency": "USD",
  "candidate_cost_interval": {"low": 0, "high": 0},
  "reference_cost_interval": {"low": 0, "high": 0},
  "fx_observation_ids": [],
  "net_spread_interval": {"low": 0, "high": 0},
  "tolerance_budget": 0,
  "decision": "qualified|research_only|rejected",
  "rejection_reasons": [],
  "policy_versions": []
}
```

The service must be deterministic for a fixed set of observation IDs and policy versions. Re-running a historical calculation after changing fee assumptions should create a new result version, not mutate the old result.

### 5.4 Operational costs and execution realism

Subtract or model:

- marketplace fees and payout deductions;
- payment processing and currency conversion costs;
- transfer or delivery costs;
- taxes where applicable;
- inventory acquisition cost;
- expected cancellation or invalid-ticket loss;
- time-to-execute and price movement risk;
- capital lock-up and working-capital cost;
- platform terms and any restrictions on resale.

An institutional decision engine should report expected value and downside, not merely nominal gross spread:

```text
expected_net_value = probability_of_success * net_spread
                     - probability_of_failure * loss_if_failure
                     - operating_cost
                     - capital_cost
```

## 6. Integration with the C3 Presents / Live Nation Acquisition Thesis

### 6.1 Context

Live Nation announced completion of a controlling stake acquisition in C3 Presents in December 2014. C3 is a promoter and festival operator with event, marketing, production, venue, and sponsorship relationships. In an acquisition context, a secondary-market intelligence system is valuable not because it predicts one ticket’s price, but because it measures the economic surface around live events and connects market signals to controllable assets.

The system should not imply that secondary-market observations alone establish acquisition value. They are one evidence layer in a broader diligence stack including promoter economics, primary ticketing, sponsorship, venue terms, artist guarantees, capacity, sell-through, customer acquisition cost, and regulatory constraints.

### 6.2 Strategic use cases

#### A. Demand sensing and event underwriting

A clean time series of secondary prices, inventory count, sellout state, and time-to-event can improve forecasts of demand pressure. It can distinguish:

- genuine scarcity: primary sold out and qualified secondary prices rising;
- weak demand: persistent inventory and declining prices;
- fragmented supply: many listings but poor comparability;
- artificial scarcity: primary unavailable but secondary inventory uncertain;
- event-specific shocks: lineup changes, weather, transport, or schedule changes.

These signals can support festival capacity, pricing, routing, and marketing decisions, but must remain separate from causal claims until validated.

#### B. Primary/secondary price leakage

For events with reliable face-value and primary inventory observations, calculate the premium or discount to primary price after fee normalization:

```text
secondary_premium = secondary_buyer_total / primary_buyer_total - 1
```

A persistent premium may indicate underpricing, demand exceeding capacity, or constrained primary distribution. A persistent discount may indicate over-allocation, weak demand, or poor transferability. Because the secondary market reflects both demand and speculative supply, interpret the ratio with inventory and sellout controls.

#### C. Portfolio and routing intelligence

For a promoter operating a portfolio of festivals, the system can estimate cross-event overlap, price pressure, and geographic substitution. A buyer can use this to identify where calendar conflicts, market saturation, or lineup overlap reduce incremental demand. The value is portfolio-level: events should not be assessed only in isolation.

#### D. Ticketing and distribution economics

A market-observation layer can quantify the difference between listing price, buyer total, and seller proceeds. This supports diligence on fee leakage, distribution economics, conversion friction, and the value of owning or integrating ticketing channels. It can also identify where all-in pricing or fee presentation changes consumer-visible price elasticity.

#### E. Sponsorship and premium inventory

Secondary premiums by access class, VIP package, section, or hospitality tier can inform the pricing and packaging of sponsorship-adjacent inventory. This requires strict separation of ordinary admission from bundled benefits; a VIP ticket must not be treated as a comparable ordinary seat.

### 6.3 Why institutional buyers care about safeguards

An acquirer will discount a database that cannot answer basic audit questions:

- Was the price all-in or pre-fee?
- Was the ticket actually available at the quoted price?
- Was the currency known and the FX rate contemporaneous?
- Did the listing price change before the analyst acted?
- Are the observations duplicates or independent inventory?
- Was the event actually sold out, or did the scraper fail?
- Can the metric be reproduced from immutable source evidence?

The proposed controls answer those questions and create a defensible data asset. They turn scraped prices into a research-grade observation ledger with provenance, uncertainty, and lifecycle semantics.

### 6.4 Acquisition diligence outputs

A mature system should produce the following institutional reports:

1. Event demand curve: qualified secondary buyer totals by days-to-event, access class, and inventory state.
2. Sellout chronology: primary availability and secondary scarcity timeline.
3. Fee leakage report: displayed price versus buyer total versus seller proceeds.
4. Cross-market and cross-currency comparability report with FX freshness statistics.
5. Listing survival and repricing report based on immutable trajectories.
6. Opportunity attribution report separating genuine market spread from fee, FX, stale-data, or matching artifacts.
7. Data coverage and reliability report by source, event, currency, and retrieval window.
8. Backtest with walk-forward information barriers and no future FX or availability leakage.

### 6.5 Governance and integration risks

The acquisition thesis must consider platform terms, resale restrictions, data licensing, privacy, anti-bot controls, ticket transfer rules, and potential antitrust sensitivity. The system should collect only permitted data, preserve source attribution, respect rate limits, and avoid representing inferred values as observed facts. Any integration with a promoter or ticketing operator should separate analytical use from operational decisions and document access controls.

## 7. Implementation Plan

### Phase 1: schema and provenance

- Add fee, currency, FX, status, freshness, and raw-provenance fields.
- Introduce immutable listing observations.
- Add enumerated quality flags and rejection reasons.
- Version parser, fee policy, FX adapter, and calculation logic.

### Phase 2: deterministic normalization

- Implement fee-state machine.
- Implement ISO currency validation and explicit missing-currency rejection.
- Implement direct/inverse FX pair resolution.
- Add timestamp and freshness gates.
- Add event/listing comparability matching.

### Phase 3: trajectory and stale-data services

- Add exact-state hashing and observation deduplication.
- Build listing trajectory transitions.
- Implement active-signal expiry without historical deletion.
- Add source outage and coverage-age reporting.

### Phase 4: arbitrage and research outputs

- Implement interval-valued net spread.
- Add tolerance budget and event-specific calibration.
- Produce candidate, research-only, and qualified opportunity feeds.
- Add walk-forward backtests.

### Phase 5: institutional reporting

- Add event-level demand, sellout, fee leakage, and source reliability reports.
- Link observations to festival, edition, venue, artist, and promoter entities.
- Add data-room exports with reproducible calculation manifests.

## 8. Acceptance Criteria

The implementation is acceptable only when:

- a missing currency cannot produce a normalized arbitrage signal;
- a fallback FX rate cannot produce a qualified signal;
- inverse FX rates are resolved with explicit direction and tests;
- stale ticket or FX data is rejected from current opportunities;
- an unchanged listing ID can have multiple immutable price observations;
- repricing does not delete or overwrite historical values;
- duplicate polling does not inflate listing counts or erase transitions;
- unknown fees are null/flagged, never zero-filled;
- an event-level “from” price cannot masquerade as an exact listing price;
- sellout requires explicit or repeated evidence and distinguishes primary from secondary availability;
- every derived spread identifies its source observations, FX observations, policy versions, and rejection/qualification reasons;
- historical backtests cannot use future availability, future FX, or future fee-policy information;
- reports distinguish observed, estimated, inferred, and unknown values;
- the root festival-bloomberg data model remains compatible with existing event and edition entities.

## 9. Reference Sources

The following public sources informed the research layer. Product-specific mechanics must be revalidated against current contractual documentation and observed listing context before production deployment.

- SeatGeek, “Does SeatGeek charge a fee to buy tickets? (All-In Pricing Explained)”: https://support.seatgeek.com/hc/en-us/articles/360036685293-Does-SeatGeek-charge-a-fee-to-buy-tickets-All-In-Pricing-Explained
- SeatGeek, “How do I sell tickets on the SeatGeek Marketplace?”: https://support.seatgeek.com/hc/en-us/articles/360007201314-How-do-I-sell-tickets-on-the-SeatGeek-Marketplace
- SeatGeek, “What is SeatGeek and how does it work?”: https://support.seatgeek.com/hc/en-us/articles/52321078229139-What-is-SeatGeek-and-how-does-it-work
- SeatGeek, “What is the secondary ticket market?”: https://support.seatgeek.com/hc/en-us/articles/360012945394-What-is-the-secondary-ticket-market
- SeatGeek Platform Overview: https://seatgeek.com/build
- SeatGeek, “Secondary Market Service Fees”: https://seatgeek.com/blog/secondary-market-service-fees-work
- Live Nation / PR Newswire, “Live Nation Entertainment Expands Festival Portfolio With C3 Presents”: https://www.prnewswire.com/news-releases/live-nation-entertainment-expands-festival-portfolio-with-c3-presents-300012666.html
- Federal Register, United States et al. v. Live Nation Entertainment, Inc., proposed final judgment and competitive-impact materials: https://www.federalregister.gov/documents/2026/07/06/2026-13623/united-states-et-al-v-live-nation-entertainment-inc-proposed-final-judgment-and-competitive-impact
- Bank for International Settlements, “OTC foreign exchange turnover in April 2025”: https://www.bis.org/statistics/rpfx25_fx.htm
- Bank for International Settlements, reporting guidelines and data-quality checks: https://www.bis.org/statistics/triennialrep/guidelines_cbanks.htm

## 10. Open Research Questions

1. Which SeatGeek listing and checkout fields are consistently available under an authorized, terms-compliant data access method?
2. Does all-in display remain invariant across quantity, delivery method, geography, logged-in state, and event type?
3. What fee interval is empirically justified for each source, event class, and observation context?
4. How often does a listing ID persist through material seat or quantity changes?
5. What disappearance-to-sale probability can be estimated without treating source failure as a transaction?
6. Which FX source provides both timestamps and sufficient intraday granularity for the target markets?
7. What spread survives actual transfer, payment, cancellation, and currency-conversion costs?
8. Which features explain realized secondary premiums after controlling for sellout and inventory depth?
9. How should market observations feed the existing festival, edition, lineup, and artist entity model without creating duplicate event identities?
10. Which outputs are decision-useful to a promoter or institutional acquirer while remaining compliant with marketplace terms and applicable competition law?

The immediate engineering priority is to make uncertainty explicit and preserve immutable evidence. Additional coverage should follow only after these controls prevent false arbitrage from fees, currencies, stale listings, and event-level aggregation.
