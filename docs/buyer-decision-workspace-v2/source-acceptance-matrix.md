# Source Acceptance Matrix — BUYER_DECISION_WORKSPACE_V2

## Bakeoff date: 2026-08-24

## Key finding

**Monid does not expose Songkick, Eventbrite, Resident Advisor, Bandsintown, or DICE as endpoints.** These are Apify Actors that require direct Apify access. Monid discovery returned zero relevant results for these queries.

`APIFY_TOKEN` is documented in `.env.example` and `config.py` but is NOT present in the local `.env` or process environment. Direct Apify execution requires setting this token.

## Sources evaluated

### Through Monid (available)

| Source | Endpoint | Cost/call | Records | Fields | Verdict |
|--------|----------|-----------|---------|--------|---------|
| Google Maps Reviews | apify/compass/google-maps-reviews-scraper | $0.00067 | N/A | reviews, place metadata | RESEARCH_ONLY |
| Google Maps Places | apify/damilo/google-maps-scraper | $0.0045 | N/A | places, coordinates | RESEARCH_ONLY |
| Instagram API | apify/apify/instagram-api-scraper | $0.003 | N/A | profiles, posts | RESEARCH_ONLY |
| Instagram Posts | apify/apify/instagram-post-scraper | $0.0023 | N/A | posts, captions, engagement | RESEARCH_ONLY |
| TikTok Scraper | apify/apidojo/tiktok-scraper | $0.00045 | N/A | profiles, videos | RESEARCH_ONLY |
| YouTube Scraper | apify/streamers/youtube-scraper | $0.0045 | N/A | videos, metadata | RESEARCH_ONLY |
| YouTube Comments | apify/streamers/youtube-comments-scraper | $0.0023 | N/A | comments, timestamps | RESEARCH_ONLY |
| Web Markdown | context.dev/web/scrape/markdown | $0.0009 | N/A | markdown from URL | ACQUISITION_UTILITY |

### Through direct Apify (NOT AVAILABLE without APIFY_TOKEN)

| Source | Actor ID | Cost | Priority | Why |
|--------|----------|------|----------|-----|
| Songkick | crawlergang/songkick-scraper | ~$3/1K | **P0** | Historical gigography, upcoming shows, artist/venue/date |
| Bandsintown (artist) | automation-lab/bandsintown-events-scraper | ~$3/1K | **P0** | Artist touring history, event corroboration |
| Bandsintown (city) | aitorsm/bandsintown-events | TBD | **P1** | City-wide discovery, RSVP counts |
| Resident Advisor | crawlerbros/resident-advisor-scraper | ~$3/1K | **P0** | Electronic music, lineups, clubs, ticket URLs |
| DICE (automation-lab) | automation-lab/dice-events-scraper | ~$1/1K | **P0/P1** | Events, venues, coordinates, prices, presenter |
| DICE (hoholabs) | hoholabs/dicefm-scraper | ~$1/1K | **P1** | Artist/venue/city queries, genre tags |
| Eventbrite (automation-lab) | automation-lab/eventbrite-scraper | ~$1/1K | **P0** | Publishing timestamps, prices, organizers |
| Eventbrite (epicscrapers) | epicscrapers/eventbrite-scraper | ~$1/1K | **P0** | 25+ fields, pricing, organizer data |
| Eventbrite (solidcode) | solidcode/eventbrite-scraper | ~$1/1K | **P0** | Events, venues, categories |
| AXS | lexis-solutions/axs-scraper | $35/mo + usage | **INSPECT_ONLY** | Events, venues, performers, prices |

## Measurement summary

### Available (Monid, measured)
- 8 endpoints available through Monid
- Web Markdown: $0.0009/call, reliable, generic transport
- Social scrapers: $0.00045–$0.015/call
- Google Maps: $0.00067–$0.0045/call

### Not available (needs APIFY_TOKEN)
- **10 event-specific scrapers** cannot be evaluated
- **Songkick** is the highest priority for historical tour data
- **DICE** may provide the most direct underwriting value (prices, presenter)
- **Resident Advisor** likely most valuable for electronic genre coverage
- **Bandsintown** offers city-wide discovery option
- **Eventbrite** may improve competitive calendar coverage

## Rights disposition

| Category | Status | Note |
|----------|--------|------|
| Apify actors (social) | TERMS_REVIEW_REQUIRED | TikTok, Instagram, YouTube scrapers |
| Apify actors (event) | TERMS_REVIEW_REQUIRED | Songkick, Eventbrite, RA, Bandsintown, DICE |
| Google Maps | TERMS_REVIEW_REQUIRED | Places and reviews scrapers |
| Web Scrape Markdown | PER_TARGET_REQUIRED | Rights depend on target URL |

## Classification fix

`context.dev/web/scrape/markdown` is classified as **ACQUISITION_UTILITY**, not a DATA SOURCE. It is an extraction transport. Rights and provenance depend on the specific target page being scraped.

## Verdict

| Source | Verdict | Rationale |
|--------|---------|-----------|
| Web Markdown (context.dev) | **PILOT_ONLY** | Acquisition utility for known official pages, not a standalone data source |
| Google Maps | **RESEARCH_ONLY** | Venue enrichment potential, but terms review needed |
| YouTube Scraper | **RESEARCH_ONLY** | Attention signals, but marginal without specific buyer question |
| Instagram/TikTok | **RESEARCH_ONLY** | Social attention, relevance TBD without buyer workflow |
| Songkick | **PENDING_TOKEN** | Highest priority for historical touring evidence |
| DICE | **PENDING_TOKEN** | Most direct underwriting value (prices, presenter, venue) |
| Resident Advisor | **PENDING_TOKEN** | Electronic music coverage |
| Bandsintown | **PENDING_TOKEN** | Tour corroboration |
| Eventbrite | **PENDING_TOKEN** | Calendar coverage improvement |

**No data source is ADOPTED in this milestone.** None can be measured without APIFY_TOKEN.

## Recommended next step

Set `APIFY_TOKEN` in `.env` and run the bakeoff with:
1. Songkick (historical gigography)
2. DICE (underwriting data)
3. Resident Advisor or Eventbrite

Select at most 2 based on measured incremental buyer value.