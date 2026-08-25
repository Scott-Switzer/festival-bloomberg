# TICKET_MARKET_DATA_MOAT_V2 — Handoff

**Branch:** `feat/ticket-market-data-moat-v2`
**Base:** `main` at `f3452e2` (post-#45 merge, exact)
**PR #45:** merged — merge SHA `f3452e2`, post-merge CI green.

This milestone turns the proven acquisition architecture into a durable,
cross-marketplace ticket-market asset:

```
canonical event → exact marketplace mapping → targeted fetch → JSON-LD /
normalized capture → immutable snapshot → listing lifecycle → time series
```

## What shipped

### Migration 041 — `ticket_market_moat_v2`
- `acquisition.event_identifiers` — the cross-market security master:
  one row per (event_key × marketplace) with provider ID/URL, mapping status
  (EXACT_PROVIDER_ID / EXACT_PAGE_MATCH / HIGH_CONFIDENCE / AMBIGUOUS / STALE /
  NOT_FOUND), method, confidence, first_resolved / last_verified.
- `acquisition.marketplace_listings` — listing-level lifecycle (DEEP rail):
  first_seen / last_seen, price history JSON, status transitions
  (LISTING_APPEARED / DISAPPEARED / PRICE_CHANGED / QUANTITY_CHANGED).
  **Disappearance is never inferred as a sale.**
- `acquisition.raw_evidence_store` — content-addressed raw payloads
  (hash dedup: identical pages reuse one row, new timestamps still get new
  snapshot rows).
- `acquisition.source_health_by_method` — health ledger keyed by acquisition
  method (MONID_HTML, MONID_FETCH, TICKETS_DEV, APIFY_ACTOR) × marketplace,
  because method matters more than platform.

### Evidence-rails modules
- `evidence_rails/tickets_dev.py` — tickets.dev adapter:
  - **Catalog** (`/v1/events`, never billed): cross-marketplace event mapping —
    one catalog row carries the same event's Ticketmaster / Vivid / StubHub /
    SeatGeek IDs and URLs. This is the security-master feed.
  - **Capture** (`/v1/capture`, billed with a live key): one normalized
    snapshot across all marketplaces — stats (get-in/median/avg/max,
    listing/ticket counts) + every listing (section/row/quantity/base/fee/
    all-in). Sandbox key `tk_test_sandbox` returns free fixtures with the
    exact same schema (never billed).
- `evidence_rails/router.py` — deterministic economic router. No LLM picks a
  source:
  - FAST rail (daily event-level) → MONID_HTML when a URL is mapped
  - DEEP rail (weekly / T-minus milestones, listing-level) → TICKETS_DEV_DEEP
    with a live key, MONID fallback otherwise
  - APIFY_FALLBACK only where empirically justified
  - `monthly_cost()` and `deep_cadence()` (T-30/T-14/T-7/daily buckets)

### Production collector
- `scripts/collect_ticket_market.py` — `--fast` / `--deep` / `--max-cost`
  budget guard / `--dry-run` / `--max-fetch` / `--wave` / `--source`.
  Append-only; never overwrites prior snapshots; per-URL retry; persists
  observed_at / retrieved_at / knowledge_time on every row.
- `scripts/ticket_market_scheduler.sh` — `enable` / `disable` / `status` /
  `run-once [--deep] [--max-cost N]` using the repo's existing LaunchAgent
  pattern (mirrors the economics-snapshot agent). No new orchestration
  framework.

### Buyer workspace (TICKET MARKET section)
- `market_history` — per-source NOW / 1D / 7D columns with absolute + percent
  deltas and elapsed hours (real timestamps).
- `cross_market` — lowest/highest observed all-in price and spread across
  mapped marketplaces (explicitly not an arbitrage claim).
- `event_identifiers` — security-master drill-down: FI event_key → provider
  IDs and mapping status.
- `source_health` — health by acquisition method.
- All V2 additions are guarded: with only migration 039 present the section
  still renders (verified by test).

## Real observations this milestone (no simulation)

Wave C (FAST, real network): **6 fetches, 6 snapshots, $0.0054, zero errors.**
Wave C-deep (tickets.dev sandbox): **4 captures, 16 listing rows persisted,
$0.00** (fixtures; live key required for real deep pricing).

| wave | snapshots | with price | method |
|------|-----------|-----------|--------|
| wave_A | 19 | 8 | Monid HTML (real) |
| wave_B | 17 | 8 | Monid HTML re-fetch (real, 0 price changes) |
| wave_C | 6 | 5 | Monid HTML via collector (real) |
| wave_C_deep | 4 | 4 | tickets.dev sandbox (fixtures) |

Price history exists for 17 URLs with 2+ real observations. Listing lifecycle
rows: 16 (all LISTING_APPEARED; transition logic unit-tested).

## Same-URL benchmark (Monid vs tickets.dev)

Run on the SAME mapped URLs (8 events):

| metric | Monid HTML (context.dev) | tickets.dev (sandbox) |
|--------|--------------------------|----------------------|
| success | high | high (fixtures) |
| event-level price | real (J. Cole $87, Metallica $1,533) | fixture get-in $402-410 |
| listing-level rows | none | 4 per capture |
| latency | ~4-78s | ~0.1-1.5s |
| cost | $0.0009/call | $0 (sandbox); $0.02-0.05 live |

**Reading:** Monid HTML is the working FAST rail today with real prices at
~$0.0009/call. tickets.dev returns the normalized listing contract (section/
row/quantity/fees/all-in) that Monid HTML extraction cannot, and its catalog
is the security-master feed — but live captures require a `tk_live_…` key
(advertised $0.02-0.05/capture). No live key was configured; the sandbox
proved the parser contract and lifecycle logic at zero cost.

## Rebrowser `seatgeek-dataset` verdict

- Public repo advertises a 76M+ listing SeatGeek estate with TM/StubHub/SeatGeek
  IDs, announcement/visibility dates.
- **No LICENSE file found** → default `TERMS_REVIEW_REQUIRED`.
- The free sample is **sports-only** (MLB/NHL/NBA/NFL) with `[PREMIUM]`-masked
  price fields — near-zero lift for a music-focused watch universe.
- **Verdict: REJECT for this milestone.** Revisit only if a license is
  actually obtained and the full dataset (including music) becomes accessible.
- The tickets.dev catalog already provides cross-marketplace TM↔Vivid↔StubHub↔
  SeatGeek event-ID mapping for free, which covers the mapping lift Rebrowser
  would have provided for music events.

## Cost model (measured, hybrid policy)

FAST rail: Monid HTML @ $0.0009/call. DEEP rail: tickets.dev @ $0.03/capture
(live) or Monid fallback @ $0.0009.

| universe | FAST 1x/day | + DEEP weekly | total/mo |
|----------|-------------|---------------|----------|
| 100 | $2.70 | $3.60 (TD) / $0.36 (Monid) | ~$3.06-6.30 |
| 500 | $13.50 | $18.00 / $1.80 | ~$15.30-31.50 |
| 1,000 | $27.00 | $36.00 / $3.60 | ~$30.60-63.00 |
| 5,000 | $135.00 | $180.00 / $18.00 | ~$153-315 |

Compare: the broken Apify search-Actor path was ~$1,350/mo for 100 events
daily. Targeted URL fetching is ~2 orders of magnitude cheaper.

## Rights

- All marketplace-page observation: `TERMS_REVIEW_REQUIRED`, stored as
  `PROTOTYPE_ONLY` unless cleared.
- SeatGeek official API: NOT used (terms restrict competitive monitoring).
- No CAPTCHA bypass; no session tokens / cookies / auth material stored.
- `raw_evidence_store` stores page content/hashes only.

## Gates

- Python: **816 passed, 1 skipped** (17 new V2 tests)
- Node: **76/76**
- TypeScript: clean
- Secret scan: `.env.example` only (placeholders), no tokens in tracked files
- CI: exact-head run on this branch

## Answer: what ticket-market information are we now accumulating that a
competitor starting six months later cannot recreate?

1. **The cross-market event security master itself** — the FI event_key ↔
   {TM, SeatGeek, Vivid, StubHub, Gametime, TickPick} ID map. This is
   relationship data built from repeated observations and validation; a
   competitor starts from zero and cannot reconstruct our mapping history.
2. **Repeated price/listings/availability observations with exact
   observation timestamps** — every wave is a snapshot of marketplace state
   at a moment in time. A late entrant can fetch today's prices but cannot
   recover what SeatGeek showed on 2026-08-25 at 21:59 UTC.
3. **Listing lifecycle transitions** (appeared / disappeared / price-changed /
   quantity-changed) — change events that only exist because we observed the
   market repeatedly. Disappearance timing, repricing, and quantity
   movements are not archived anywhere public.
4. **Point-in-time discipline** — observed_at ≠ retrieved_at ≠ knowledge_time
   is preserved on every row, so the history is auditable as history, not
   backfilled.

The moat is not "we have a scraper." It is: **we resolved the identity layer
once, and we are the only ones who have been watching the same events
repeatedly since 2026-08-25.**

## Not done (deliberately)

- No Census / Common Crawl / social / ML — explicitly out of scope.
- tickets.dev live captures not run (no live key; sandbox validated the
  contract at zero cost).
- Multi-key Apify rotation not ported — one credential, clean attribution.
