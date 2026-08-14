# Artist × Market Event History V1

Operational acceptance for the stacked event/performance graph.

This work lives on `feat/artist-market-event-history-v1` and is **stacked on
PR #10**. Do not merge this branch before the Signal Fabric / PIT foundation
merges. Do not add this work onto PR #10.

## What this proves

Ticketmaster Discovery API v2 and Setlist.fm API v1 can produce a source-backed
artist × market × venue × event × date package:

- Has this artist played Chicago city proper?
- When, where, how often, on which named tours?
- What upcoming Chicago listings exist?
- Which claims are backed by observation IDs?

It does **not** produce a booking score, demand score, attendance forecast,
revenue forecast, fabricated venue capacity, or fabricated billing tier.

## PIT rules

| Source field | Semantic |
|---|---|
| Ticketmaster event start / Setlist `eventDate` | `EVENT_TIME` (source fact) |
| Setlist `lastUpdated` | `SOURCE_UPDATED_AT` (not knowledge) |
| Live retrieval timestamp | `knowledge_time = retrieved_at` |

A 2019 performance retrieved in 2026 is visible at a 2026 cutoff and **not**
visible at a 2020 cutoff. Event time is never used as knowledge time.

## Chicago market

Canonical market is **Chicago, IL, US** city proper. Rosemont, Tinley Park,
Evanston, and other nearby cities are not Chicago automatically. Search query
strings are not geographic evidence.

Festival type is stored only when a provider makes the relationship explicit.
Names that merely sound like festivals stay `UNKNOWN`.

## Sampling caveat (YouTube V1)

Raw comment counts are not comparable artist popularity. A capped Chicago
cohort (500 comments) and a small global cohort (14 comments) have different
sampling exposure. Sentiment shares are **within sampled comments**, not
fanbase sentiment. Capped collections are labeled `CAPPED`, never `COMPLETE`.

## Event-linked fan evidence

A YouTube video may attach to a canonical event only with explicit venue+date,
festival+date/year, or a canonical event identifier. The query `"Artist Chicago"`
is not enough. `EVENT_LINKED_FAN_SIGNAL` is `PASS` only when a real event, a
linked source object, and `FAN_GENERATED` comments all exist.

## Live OA (local, opt-in)

```text
PYTHONPATH=python python3.12 -m festival_bloomberg.cli \
  operational-acceptance-event-history
```

Monetary budget is `$0.00`. No Monid paid calls. No Apify actor runs.
Authenticated API tests stay local; CI uses fixtures only.

The machine-readable manifest is local and gitignored:

`reports/artist_market_event_history_v1.json`
