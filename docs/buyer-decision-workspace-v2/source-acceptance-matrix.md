# Source Bakeoff Acceptance Matrix — PR #44

**Generated**: 2026-08-25
**Bakeoff Version**: source_bakeoff_v2
**Total Spent**: $0.0038 (budget: $2.00)

## Executive Summary

All 18 candidates were schema-inspected. 10 were executed with real queries against the direct Apify API using a single `APIFY_TOKEN`. Results are based on actual dataset record analysis, not actor metadata.

Two sources merit pilot adoption: **Eventbrite (scrapesage)** and **DICE (hoholabs)**.

---

## P0 Sources

### Eventbrite

| Actor | Schema Version | Records | Cost | Fields | Coords | Price | PIT | Verdict |
|-------|---------------|---------|------|--------|--------|-------|-----|---------|
| `scrapesage~eventbrite-scraper` | 1.1.6 (2026-08-15) | 25 | $0.00 (free tier) | 74 | ✓ | ✓ (min/max + currency) | ✓ (publishedDate) | **PILOT_ONLY** |
| `solidcode~eventbrite-scraper` | 1.0.6 (2026-08-05) | — | — | 11 fields | ✗ | ✓ (priceFilter) | ✗ | INSPECT_ONLY |
| `epicscrapers~eventbrite-scraper` | 0.0.16 (2026-07-06) | — | — | 11 fields | ✗ | ✓ | ✗ | INSPECT_ONLY |

**Eventbrite (scrapesage) key fields**: `eventId`, `name`, `startDateLocal`, `endDateLocal`, `publishedDate`, `venueName`, `addressFull`, `latitude`, `longitude`, `minTicketPrice`, `maxTicketPrice`, `currency`, `isFree`, `isSoldOut`, `salesStatus`, `capacity`, `organizerId`, `organizerName`, `organizerTotalAttendees`, `lineup`, `searchQuery`, `scrapedAt`

**Strengths**:
- Rich price data (min/max/currency/isFree)
- Published date gives PIT knowledge time
- Organizer enrichment (followers, total events, attendees, verified status)
- Full address + coordinates
- Sales status and sold-out detection
- Capacity field

### DICE

| Actor | Schema Version | Records | Cost | Fields | Coords | Price | PIT | Verdict |
|-------|---------------|---------|------|--------|--------|-------|-----|---------|
| `hoholabs~dicefm-scraper` | 0.1.14 (2026-08-03) | 50 | $0.003 | 52 | ✓ (nested) | ✓ (price + currency + breakdown) | ✓ (announcement_date) | **PILOT_ONLY** |
| `solidcode~dice-fm-scraper` | 1.0.7 (2026-08-08) | — | — | 5 fields | ✗ | ✗ | ✗ | INSPECT_ONLY |
| `chalkandcheese~dice-fm-events-scraper` | 0.3.2 (2026-06-06) | — | — | 7 fields | ✗ | ✗ | ✗ | INSPECT_ONLY |

**DICE (hoholabs) key fields**: `id`, `name`, `date`, `date_end`, `venue`, `location` (lat/lng/city/street), `artists`, `lineup`, `detailed_artists`, `price`, `currency`, `ticket_types`, `show_price_breakdown`, `sold_out`, `status`, `promoters`, `presented_by`, `genre_tags`, `type_tags`, `sale_start_date`, `sale_end_date`, `announcement_date`, `apple_music_tracks`, `spotify_tracks`, `age_limit`

**Strengths**:
- Genre tags and type tags for music-specific discovery
- Announcement date gives genuine PIT knowledge
- Detailed artist breakdown with Apple Music / Spotify cross-links
- Ticket types with price breakdown
- Sold-out detection
- Promoter/presenter data
- Age restrictions

### Songkick

| Actor | Schema Version | Records | Cost | Fields | Coords | Price | PIT | Verdict |
|-------|---------------|---------|------|--------|--------|-------|-----|---------|
| `gio21~songkick-events-scraper` | 0.0.2 (2026-08-09) | 50 | $0.00 | 16 | 98% | ✗ | ✗ | RESEARCH_ONLY |
| `hoholabs~songkick-scraper` | 0.1.15 (2026-08-03) | 5* | $0.001 | 13 | ✗ | ✗ | ✗ | REJECT |
| `aitorsm~songkick-events` | 0.1.10 (2026-07-10) | 0 | — | 4 | ✗ | ✗ | ✗ | REJECT |

\* hoholabs returned artist suggestions, not events (needs artistId).

**Songkick (gio21) strengths**: Solid venue data with 98% coordinate coverage, performers and genres at 100% coverage. Good for competitive calendar enrichment. No price, no PIT timestamps.

### Bandsintown

| Actor | Schema Version | Records | Cost | Fields | Coords | Price | PIT | Verdict |
|-------|---------------|---------|------|--------|--------|-------|-----|---------|
| `automation-lab~bandsintown-events-scraper` | 0.1.9 (2026-08-22) | 10 | $0.00 | 13 | ✗ | ✗ | ✗ | RESEARCH_ONLY |
| `hoholabs~bandsintown-scraper` | 0.1.18 (2026-08-03) | — | — | 4 | ✗ | ✗ | ✗ | INSPECT_ONLY |
| `gio21~bandsintown-events-scraper` | 0.0.1 (2026-08-24) | — | — | 2 | ✗ | ✗ | ✗ | INSPECT_ONLY |

**Note**: automation-lab returned wrong artist (Coldplay instead of Kendrick Lamar). Input format needs correction.

### Resident Advisor

| Actor | Schema Version | Records | Cost | Fields | Coords | Price | PIT | Verdict |
|-------|---------------|---------|------|--------|--------|-------|-----|---------|
| `crawlerbros~resident-advisor-scraper` | 1.0.2 (2026-06-14) | 20 | $0.00 | 16 | ✗ | ✗ | ✗ | RESEARCH_ONLY |

**Key fields**: `eventId`, `title`, `eventUrl`, `date`, `startTime`, `endTime`, `venue`, `venueUrl`, `city`, `country`, `isTicketed`, `lineupText`, `allArtists`

**Strengths**: Good for electronic music events. Lineup text. No coordinates or price.

---

## Secondary Discovery

| Source | Actor | Records | Cost | Fields | Coords | Price | Verdict |
|--------|-------|---------|------|--------|--------|-------|---------|
| AllEvents | `solidcode~allevents-scraper` | 100 | $0.00 | 24 | ✓ | ✓ (min/max) | RESEARCH_ONLY |
| Fever | `hoholabs~feverup-scraper` | 20 | $0.00 | 14 | ✓ | ✓ (numeric) | RESEARCH_ONLY |
| Facebook Events | `unfenced-group~facebook-events-scraper` | 0 | — | 10 | — | — | REJECT (no results) |
| AXS | `lexis-solutions~axs-scraper` | ERROR | — | 3 | — | — | REJECT (failed) |

**AllEvents**: 100 records, 24 fields, coordinates ✓, price range ✓, interestedCount ✓, categories ✓. Excellent city-level event discovery at zero cost in our test. No PIT timestamps.

**Fever**: 20 records, coordinates ✓, `numeric_price` + `currency` ✓, `rating` ✓. Good for pricing and venue data.

---

## Ticketmaster Web Control

| Actor | Result | Verdict |
|-------|--------|---------|
| `epicscrapers~ticketmaster-scraper` | ERROR | REJECT |

No incremental fields identified over the official Ticketmaster Discovery API. Official API remains primary.

---

## Adoption Recommendations

### Adopted (PILOT_ONLY): 2 sources

1. **Eventbrite — `scrapesage~eventbrite-scraper`**
   - Richer schema than any other source (74 fields)
   - Genuine PIT via `publishedDate`
   - Full price, capacity, organizer enrichment
   - Coordinates on every record
   - Rights: TERMS_REVIEW_REQUIRED

2. **DICE — `hoholabs~dicefm-scraper`**
   - Best electronic/club music event source
   - Genre tags enable buyer-facing categorization
   - `announcement_date` gives genuine PIT knowledge
   - Ticket price breakdowns per type
   - Apple Music / Spotify cross-links for artist identity
   - Rights: TERMS_REVIEW_REQUIRED

### Research Rail: 3 sources

3. **Songkick — `gio21~songkick-events-scraper`** — Strong venue/coordinates, performers/genres
4. **AllEvents — `solidcode~allevents-scraper`** — Best city-level coverage, interest counts
5. **Resident Advisor — `crawlerbros~resident-advisor-scraper`** — Electronic music specialist

### Rejected: All others

Reasons: no results (hoholabs Songkick, aitorsm, Facebook, AXS), wrong artist (Bandsintown autolab), redundant (other Eventbrite/DICE variants), or no incremental value (Ticketmaster web).

---

## PIT Contract Assessment

| Source | PIT Field | Type | Usable? |
|--------|-----------|------|---------|
| Eventbrite (scrapesage) | `publishedDate` | Source-provided | ✓ — event publication timestamp |
| DICE (hoholabs) | `announcement_date` | Source-provided | ✓ — event announcement timestamp |
| All sources | `scrapedAt` | Scrape time | ✓ — retrieval timestamp only |

**Key**: `publishedDate` and `announcement_date` are genuine source-provided timestamps, not scrape times. They can be used as `knowledge_time` in PIT-aware systems. All scraped records also carry `scrapedAt` as `retrieved_at`.

---

## Ticketmaster Overlap Results — REVISED (2026-08-25 audit)

**Initial 86.5% incremental-lift claim has been corrected.** Manual audit revealed:

1. **DICE was queried for London** against a TM estate with **0 London events** — false comparison, different event universes.
2. **Resident Advisor `city` parameter is unreliable** — returns London venues regardless of city input.
3. **AllEvents `cities` parameter is unreliable** — returns New York venues regardless of city input.
4. **Songkick/Bandsintown queried by artist**, not by market — artist queries span multiple markets.

**Valid same-universe overlap cannot currently be measured** with these actors. RA and AllEvents ignore geographic filtering; DICE and TM cover different markets.

**What we CAN say:** These platforms (DICE, Eventbrite, Songkick, RA, Bandsintown, AllEvents, Fever) carry events that Ticketmaster's API does not surface. But the exact marginal coverage requires same-universe queries — which these specific actor versions do not support reliably.

**Recommendation for future measurement:**
- Query all sources by the same frozen artist × market universe
- Use artist-specific inputs where city filtering is unreliable
- Report only same-universe overlap when making incremental-lift claims

## Historical Depth Tested

| Source | Year Range | Events/Year |
|--------|-----------|-------------|
| DICE | 2023-2026 | 3 (2023), 7 (2024), 2 (2025), 238 (2026) |
| AllEvents | 2026 | 199 |
| Songkick | 2026 | 100 |
| Bandsintown | 2025 | 10 |
| Fever | 2026 | 40 |
| Resident Advisor | 2026 | 20 |

DICE is the only source with genuine multi-year historical depth in our bounded test. Bandsintown returned 2025 events for Kendrick Lamar query.

---

## Rights Status

| Platform | Status | Notes |
|----------|--------|-------|
| Eventbrite | TERMS_REVIEW_REQUIRED | Scraped; Eventbrite API terms differ |
| DICE | TERMS_REVIEW_REQUIRED | Scraped; DICE API terms differ |
| Songkick | TERMS_REVIEW_REQUIRED | Scraped; Songkick has official API |
| AllEvents | TERMS_REVIEW_REQUIRED | Scraped |
| Resident Advisor | TERMS_REVIEW_REQUIRED | Scraped |
| Fever | TERMS_REVIEW_REQUIRED | Scraped |
| Facebook | TERMS_REVIEW_REQUIRED | Scraped; Facebook Platform Policy applies |
| AXS | TERMS_REVIEW_REQUIRED | Scraped |

All scraped third-party ticket/event platforms default to TERMS_REVIEW_REQUIRED for commercial product use. The scraper actor is transport; the underlying site is the source.

---

## Gating Status

| Gate | Status |
|------|--------|
| Direct Apify authentication | ✅ CONFIGURED |
| Live Actor schemas inspected | ✅ 18/18 |
| P0 event-source bakeoff | ✅ 5 platforms, 12 actors |
| Actual dataset records analyzed | ✅ 3 platforms with positive results |
| Cost/useful-record measured | ✅ All under $0.001/record |
| Rights status explicit | ✅ TERMS_REVIEW_REQUIRED for all |
| No fake PIT | ✅ — only source-provided timestamps |
| ≤ 2 sources adopted | ✅ Eventbrite + DICE |
| Budget compliance | ✅ $0.0038 / $2.00 |