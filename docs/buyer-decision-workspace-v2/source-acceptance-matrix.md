# Source Acceptance Matrix — Monid/Apify Bakeoff

Date: 2026-08-24 | Balance start: $1.00 | Balance end: $0.97165 | Spent: $0.02835

## Methodology

Five candidate sources tested with real Monid CLI calls. Each source inspected
(free), then executed with bounded input (≤5 results). Output analyzed for field
coverage, null rate, cost, and immediate buyer-value fit.

## Matrix

| Source | Provider/Endpoint | Cost | Success | Record Count | Latency | Key Fields |
|--------|------------------|------|---------|-------------|---------|------------|
| Google Maps | apify/damilo/google-maps-scraper | $0.0045/result | ✅ | 1 (3 max) | ~15s | title, address, latitude, longitude, rating, ratingCount, website, phone, types |
| YouTube Scraper | apify/streamers/youtube-scraper | $0.0045/result | ✅ | 5 | ~20s | title, viewCount, likes, date, channelName, channelUrl, collaborators, id |
| TikTok Profile | apify/apidojo/tiktok-profile-scraper | $0.00045/result | ⚠️ | 0 | ~5s | noResults (kendricklamar not found) |
| Instagram Profile | apify/apify/instagram-profile-scraper | $0.003/result | ✅ | 1 | ~15s | username, followersCount, biography, externalUrls, fullName, postsCount |
| Web Scrape (MD) | context.dev/web/scrape/markdown | $0.0009/call | ✅ | N/A | ~5s | markdown (154KB for Wikipedia), contentLength, metadata |

## Sources NOT Available Through Monid

These Apify actors listed in the spec are NOT in Monid's catalog:

| Source | Expected Actor | Status |
|--------|---------------|--------|
| Songkick | crawlergang/songkick-scraper | NOT FOUND in Monid catalog |
| Eventbrite (solidcode) | solidcode/eventbrite-scraper | NOT FOUND in Monid catalog |
| Eventbrite (epicscrapers) | epicscrapers/eventbrite-scraper | NOT FOUND in Monid catalog |
| Resident Advisor | crawlerbros/resident-advisor-scraper | NOT FOUND in Monid catalog |
| Bandsintown | automation-lab/bandsintown-events-scraper | NOT FOUND in Monid catalog |
| Ticketmaster Web | lentic_clockss/ticketmaster-scraper | NOT FOUND in Monid catalog |

**These would require a direct Apify token (APIFY_TOKEN)** for evaluation.

## Verdicts

### ADOPT: context.dev/web/scrape/markdown ($0.0009/call)

**Rationale:** Highest value/cost ratio. Can scrape ANY official URL (venue pages,
event pages, promoter pages, artist tour pages) into structured Markdown at
negligible cost. A single venue Wikipedia page costs $0.0009 and returns 154KB
of structured content.

**Buyer value:** Directly fills venue context, official event information,
promoter page data — without building any custom scraper infrastructure.

**Rights:** PUBLIC — scraping public web pages. Public/no-auth. Commercial use:
standard web scraping terms apply. TERMS_REVIEW_REQUIRED for production.

### PILOT ONLY: apify/streamers/youtube-scraper ($0.0045/result)

**Rationale:** Excellent artist attention data (viewCount, likes, date,
channelName, collaborators). Returns rich, dated metadata per video. At
$0.0045/result, ~$0.05 can get a 10-video artist profile.

**But:** The project already has a YouTube API key. Official API should be
preferred where equivalent. This scraper adds value for broader search and
transcript download (not available in basic API). Only use when official API
is insufficient.

**Rights:** YouTube public data via Apify. RESEARCH_ONLY until terms reviewed.

### RESEARCH ONLY: apify/damilo/google-maps-scraper ($0.0045/result)

**Rationale:** Returns venue coordinates, ratings, website, phone. However,
venue coordinates already come from Ticketmaster/Wikidata in the existing
pipeline. Ratings are not demand signals. Marginal value over existing sources.

**May be useful for:** Venues NOT in Wikidata/Ticketmaster; venue website URLs
for further scraping; phone numbers for direct outreach.

**Rights:** Google Maps public data. TERMS_REVIEW_REQUIRED for commercial use
(Google's ToS restrict automated access).

### RESEARCH ONLY: apify/apify/instagram-profile-scraper ($0.003/result)

**Rationale:** Returns followersCount (18.7M for Kendrick), biography, external
URLs, fullName. Useful for forward artist attention observations.

**But:** $0.003 per artist adds up for bulk. Public profile data only.
Current followers ≠ historical followers. Instagram API is notoriously
restrictive about scraping.

**Rights:** TERMS_REVIEW_REQUIRED. Instagram aggressively blocks scraping.
Prefer official API or public web routes.

### REJECT: apify/apidojo/tiktok-profile-scraper ($0.00045/result)

**Rationale:** Returned noResults for kendricklamar. Username resolution appears
unreliable. Cost is low but signal quality is poor. Revisit if a better TikTok
endpoint emerges or if username canonicalization improves.

### NOT EVALUATED: Songkick, Eventbrite, Resident Advisor, Bandsintown

Cannot evaluate through Monid — no matching endpoints. Would need direct Apify
token (APIFY_TOKEN). These remain the highest-priority event sources for
historical gigography and competitive calendar augmentation, but require
direct Apify access.

## Rights Disposition Summary

| Source | Rights Status | Commercial Use | Notes |
|--------|--------------|----------------|-------|
| Web Scrape Markdown | TERMS_REVIEW_REQUIRED | Terms review needed | Public web scraping |
| YouTube Scraper | RESEARCH_ONLY | No | Already have official YouTube API |
| Google Maps | TERMS_REVIEW_REQUIRED | No | Google ToS restrict automated access |
| Instagram | TERMS_REVIEW_REQUIRED | No | Instagram aggressively blocks scraping |
| TikTok | RESEARCH_ONLY | No | Unreliable resolution; revisit later |
| Songkick (not evaled) | TERMS_REVIEW_REQUIRED | Unknown | Would need direct Apify |

## Cost/Yield Measurements

| Source | Cost per record | Annual est. (1K artists × 1 scrape/mo) | Estimated buyer value |
|--------|----------------|--------------------------------------|----------------------|
| Web Scrape MD | $0.0009 | $0.90 | HIGH (venue/event enrichment) |
| YouTube | $0.0045 | $4.50 | MEDIUM (attention context) |
| Google Maps | $0.0045 | $4.50 | LOW (duplication of existing data) |
| Instagram | $0.003 | $3.00 | MEDIUM (social attention) |

## Information Lift

| Source | Unique fields not in existing pipeline | Incremental value |
|--------|---------------------------------------|-------------------|
| Web Scrape MD | Full page content from any URL | High — fills any structured gap |
| YouTube | viewCount, likes, date published | Medium — attention dimension |
| Google Maps | website, phone, rating | Low — mostly duplicate |
| Instagram | followersCount, biography | Medium — social dimension |

## Decision

**ADOPT ≤2 sources:**

1. **context.dev/web/scrape/markdown** — ADOPT for venue/event page enrichment
2. **apify/streamers/youtube-scraper** — PILOT ONLY, defer to official YouTube API first

No other source meets the bar of measurable buyer value for this milestone.

Event-source gap (Songkick, Eventbrite, RA, Bandsintown) requires direct Apify
token for evaluation and remains the top priority for a follow-on milestone
(HISTORICAL_WEB_EVIDENCE_V1).