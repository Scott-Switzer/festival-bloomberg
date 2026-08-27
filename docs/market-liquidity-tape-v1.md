# MARKET_LIQUIDITY_TAPE_V1

Milestone `feat/market-liquidity-tape-v1` — turn 33,000+ event identities into
a REAL, multi-marketplace, longitudinal liquidity tape and attach it directly
to the ARTIST × MARKET security.

## Phase 0 — close ARTIST_SECURITY_1000_SCALE_V1 (#54)

- PR #54 `feat/artist-security-1000-scale-v1` merged normally into `main`.
- Exact head verified (`0f721d3152bbbcd80c347b46c295821989de8d64`); all four CI
  checks green pre-merge and on post-merge `main` (`257a3d2...`).
- Branch `feat/market-liquidity-tape-v1` created from that `main`.

## Policy (unchanged from the milestone)

1. **Official structured APIs first.** Ticketmaster Discovery is the primary
   rail; SeatGeek and StubHub official APIs are probed; browser/Monid
   acquisition is deferred (P11) — never a substitute for authorized APIs.
2. **STANDARD_PRICE_RANGE and CURRENT_AVAILABLE_INVENTORY_PRICE are distinct
   semantics** — never merged into one generic ticket price.
3. **No attendance inference, no sales inference.** Listing-count change is not
   a sale; listing disappearance is not a sale.
4. **Artist linkage is evidence-backed** (TM attraction ID + event-side
   attribution double-confirm). Bare normalized-name matching never produces a
   VERIFIED link; ambiguous matches fail closed.
5. **Credential/authorization state is explicit** per provider (P10): every
   source reports credential state, API authorization, calls, cash cost,
   useful observations, and commercial-rights state. Public accessibility is
   not treated as automatic commercial permission.

## What this milestone delivers

| Workstream | Deliverable | Status |
|---|---|---|
| P0 | Ticketmaster structured tape — Discovery GET_EVENT enrich (status / onsale / standard price range / promoter / URL) + Inventory Status auth probe | TM key VALID; structured enrich live |
| P1 | SeatGeek official Platform API — event-level public stats (listing_count / low / avg / high) | NOT_AUTHORIZED (no key) — fail closed |
| P2 | StubHub application-only OAuth — official catalog API | NOT_AUTHORIZED (no credentials) — fail closed |
| P3 | Marketplace identity graph — TM exact IDs in `acquisition.event_identifiers` | TM EXACT_PROVIDER_ID rows |
| P4 | Canonical market observation contract | `acquisition.market_price_observations` |
| P5 | Bootstrap cohort — future events defensibly linked to ARTIST_SECURITY_1000 | cohort via TM attraction double-confirm |
| P6 | Longitudinal depth metrics (PIT days, pair distribution, observation depth) | real counters |
| P7 | Forward artist tape — wiki latest daily + LB current + YouTube credential | wiki/LB live; YT BLOCKED_INVALID_KEY |
| P8 | Product join — market liquidity into `asm.artist_market_security_v1` | descriptive join columns |
| P9 | Perspective monitor market-liquidity columns | sort/filter/pivot over real rows |
| P10 | Rights + cost scorecard | `acquisition.source_auth_status` |
| P11 | Vivid/TickPick/Gametime/AXS probe | P11_DEFERRAL (no paid provider) |

## The canonical observation contract (P4)

`acquisition.market_price_observations` — one neutral contract for every
marketplace:

- `event_key | artist_key | market_key | marketplace | provider_event_id`
- timing: `observed_at | available_at | retrieved_at | knowledge_time`
- **standard primary range**: `standard_primary_min / standard_primary_max /
  primary_currency` (listed face-value band from the primary seller)
- **current available inventory**: `current_available_min / current_available_max /
  inventory_currency / listings_extend_beyond_max` — only populated where a
  marketplace legitimately exposes current inventory (NOT inferred from the
  standard range)
- public market stats: `listing_count / average_public_offer /
  lowest_public_offer / highest_public_offer` (event-level; never seat-level)
- `availability_state / event_status / price_basis / inventory_basis`
- provenance: `source / source_origin / raw_evidence_ref / canonical_url /
  promoter / rights_status / commercial_use_status`

UNKNOWN stays NULL. Inventory Status semantics are recorded as
`NOT_EXPOSED` when the Discovery rail is used — never merged into the standard
range.

## Artist → event linkage (P3/P5)

- `identity.ticketmaster_artist_resolutions` — artist → TM attraction id
  resolved via the official Discovery `attractions.json` search (canonical,
  non-tribute pages only; ambiguous names fail closed to AMBIGUOUS).
- `acquisition.artist_marketplace_links` — artist ↔ event links promoted to
  VERIFIED only when BOTH the attraction id AND the event's own TM headline
  attribution agree (double-confirm). The bootstrap cohort is built from these
  verified links over the top-10 US live markets and future-dated events.

## Forward artist tape (P7)

`metrics.artist_forward_tape` — latest complete daily observation per artist
per feed with freshness tracking:

- `wiki_daily` — latest committed daily wikipedia pageviews row (pointer to the
  real daily tape, not a re-derivation)
- `listenbrainz` — latest all-time listen count
- `youtube_channel` — credential state; `BLOCKED_INVALID_KEY` when no valid
  `YOUTUBE_API_KEY` exists (no fabricated subscriber counts)

A cron runner can append the next day forward from these pointers.

## Result — real collected data (2026-08-27 run)

_Real counts are written to `reports/market_liquidity_success.json` by
`scripts/populate_market_liquidity.py`; the doc below mirrors that report._

### Structured sources

| Provider | Credential | Auth | Evidence |
|---|---|---|---|
| Ticketmaster Discovery | CONFIGURED | AUTHORIZED | GET_EVENT enrich + attraction resolution |
| Ticketmaster Inventory Status | CONFIGURED (Discovery key) | ENDPOINT_UNREACHABLE — separate paid/authorized rail, never scraped | none |
| SeatGeek Platform API | ABSENT | NOT_AUTHORIZED | none (fail closed) |
| StubHub OAuth app-only | ABSENT | NOT_AUTHORIZED | none (fail closed) |
| YouTube Data API | CONFIGURED but INVALID (400) | BLOCKED_INVALID_KEY | forward tape blocked, no fabrication |
| Vivid/TickPick/Gametime/AXS | ABSENT | P11_DEFERRAL | none (no paid provider) |

### Market liquidity tape (2026-08-27 full run)

- canonical events considered: **33,383** (event tape)
- TM attraction linker: **866 / 1000 artists** resolved to a canonical
  Ticketmaster attraction id; **134 ambiguous** (fail closed, never guessed)
- bootstrap cohort: **571 future events** double-confirmed (TM attraction id +
  event attribution) linked to ARTIST_SECURITY_1000 artists across the top-10
  US live markets
- Ticketmaster exact mappings: **571** (cohort events → EXACT_PROVIDER_ID in
  `acquisition.event_identifiers`)
- SeatGeek / StubHub exact mappings: **0** (NOT_AUTHORIZED — no keys configured)
- events with 2+ / 3+ marketplaces: **0 / 0** (honest — no second authorized
  source exists yet; pair metrics are never fabricated)
- price observations: **572** (571 events; **36 with standard primary ranges**;
  current-available inventory recorded `NOT_EXPOSED`, never inferred)
- PIT event-marketplace-days: **33,383** (tape), 571 from real price
  observations; pair depth distribution honestly single-marketplace
- product join: **564 artist×market rows updated** with TM price evidence
  (282 artists with price evidence)
- forward artist tape: wiki latest daily **966** artists, ListenBrainz current
  **2000** rows (all 1,000 artists × listens+listeners), YouTube
  **BLOCKED_INVALID_KEY** for all 1,000
- Perspective monitor: 1,000 rows with new market-liquidity columns
  (marketplace_count, price_observation_count, latest TM standard min/max,
  on-sale state, freshness, listing count); **121 artists** carry price evidence

### Costs & rights

- cash cost: **$0.00** (free-tier official APIs only)
- browser calls: 0; Monid calls: 0 (deferred by policy)
- rights: TERMS_REVIEW_REQUIRED / PROTOTYPE_ONLY on every observation;
  no inference of attendance/sales; listing-count change is NEVER a sale.

## Deliberately NOT built

No ML, no sell-through inference, no ticket-sales inference, no demand
forecast, no lineup optimization, no additional abstract schema milestone, no
generic scraper framework, no new paid provider. SeatGeek/StubHub multi-
marketplace depth is gated on real credentials and reported honestly as
NOT_AUTHORIZED until they exist.
