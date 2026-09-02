# Competitive Buyer Decision Matrix V1

Bounded competitive audit (2026-09). Sources: public product surfaces and marketing
documentation at the time of writing — Callboard.fm, prism.fm, RealCount, Opendate,
Pollstar, Soundcharts, Chartmetric, Ticketmaster TM1. "Known from public marketing" is
all we claim; treat deal pricing and feature depth as directional, not contractual.

## Why this matrix exists

The shipped Talent Buyer MVP competes with products that already sell artist analytics
(Soundcharts, Chartmetric) and pre-offer briefs (Callboard). Winning on **more artist
metrics** is a losing race. This matrix records what each competitor does and the gaps
Festival Bloomberg can legitimately exploit with evidence it already holds.

## Matrix

| Competitor | Customer | Core job | Public artist data | Market intelligence | Historical box office | Ticket counts | Sales curves | Economics | Offers/workflow | Private customer data | Alerting | Biggest strength | Gap we can exploit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Callboard.fm** | Independent promoters | Artist + market → one pre-offer brief (fit, venue sizing, pricing tiers, guarantee range, revenue projection, risk flags) | Yes | Yes (market fit) | Partial (comps) | Partial | No | Yes (estimates) | No (brief, not workflow) | No | No | Speed: one query → one brief in ~3 min | Engineered guarantee ranges are *estimates*; no auditable evidence drill-down, no point-in-time reconstruction, no buyer-owned outcome loop |
| **Prism Insights** | Promoters/venues | Opt-in pooled box-office benchmarks; offers & settlements | Partial | Yes | **Yes (shared box office)** | Yes | Yes | Yes | Yes (offers, settlements) | Yes (shared pool) | Some | Real shared performance data at scale | Pooling needs scale; single-buyer private history is untapped — we let the buyer own their outcomes, not share them |
| **RealCount** | Industry analytics | Longitudinal ticket counts across hundreds of ticketing systems | Partial | Some | Partial | **Yes (counts/history)** | Yes | No | No | No | Yes | Breadth of longitudinal ticket-count history | No decision record, no assumption→actual loop, no buyer-owned private layer |
| **Opendate** | Promoters/venues | Historical sales + offer workflow + P&L + settlements | Some | Some | Yes (customer-side) | Yes | Yes | Yes | **Yes (workflow)** | Yes | Some | Decision/P&L/settlement stay inside the workflow | No open public evidence layer, no pre-offer research brief, no independent artist/market state |
| **Pollstar** | Industry pros | Listings, grosses, box-office reporting, news | Yes | Some | Yes (reported grosses) | Yes | Some | Some | No | No | Yes | Depth of reported industry box office | Static reporting product; no PIT decision reconstruction, no private outcome learning |
| **Soundcharts / Chartmetric** | Labels/managers/agencies | Deep streaming/social/audience/geography analytics | **Extremely deep** | Geographic fan base | No | No | Yes (charts) | No | No | Some (watchlists) | Yes | Streaming/social/audience depth | No live/market booking evidence, no show economics, no private outcomes — research only |
| **Ticketmaster TM1** | Venues/promoters on TM | Real-time sales, inventory, scan/attendance, sales curves | Some | Local only | **Yes (own shows)** | **Yes (real-time)** | **Yes** | Yes | Yes | Yes (own venue data) | Yes | Truth: actual sales/inventory/attendance | Platform-locked, own-shows-only; no independent 25K-artist research layer, no PIT learning from a buyer's own history |

## Synthesis

**Where the market already wins**
- Speed to a pre-offer brief (Callboard).
- Streaming/social artist depth (Soundcharts, Chartmetric).
- Pooled industry box office (Prism, Pollstar, RealCount).
- Real-time sales truth (TM1) and workflow stickiness (Opendate).

**Where Festival Bloomberg is differentiated (and already has the rails)**
1. **Auditable evidence** — every number on the brief carries provenance
   (OBSERVED_PUBLIC / OBSERVED_PRIVATE / USER_ASSUMPTION / DERIVED / UNKNOWN) and
   one-click drill-down. Competitor estimates do not disclose their assumptions.
2. **Point-in-time reconstruction** — for an imported historical show we rebuild what
   public evidence was knowable *before* the decision (PIT cutoffs), never gating on
   `retrieved_at`. No competitor exposes decision-time vs realized-outcome side-by-side.
3. **Buyer-owned private history** — PRIVATE_ONLY by default, PII-quarantined, never
   merged into the public serving DB. Prism pools; we let a single buyer learn from
   their own shows.
4. **Deterministic show economics** — capacity/tiers/sell-through/guarantee/backend
   with explicit UNKNOWN propagation and scenario labels that mean
   USER-DEFINED SCENARIO, never a black-box BOOK/PASS or a fake projected guarantee.
5. **Backtesting** — the Outcome Vault lets a buyer close the loop (assumption vs
   actual) and — once >1,000 diverse settled outcomes exist — begin serious OOS
   evaluation. Until then: **no predictive model** (see `MODEL_READINESS.json`).

## Rule this implies

Do not copy Callboard's GO/HOLD verdict or guarantee-range output until validated
out-of-sample. The product answer stays: *evidence + deterministic scenario math +
buyer judgment*.