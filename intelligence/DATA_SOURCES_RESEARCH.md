# Festival Intelligence Terminal - Data Sources Research

## Executive Summary

Comprehensive research on data scraping methods for festival and artist intelligence, with focus on C3 Presents integration and broader festival ecosystem coverage.

## 1. Primary Festival/Event APIs

### Songkick API
**Coverage:** 6M+ concerts and festivals globally
**Authentication:** API key required
**Rate Limits:** Not specified in docs
**Key Features:**
- Event search by artist, location, date
- Festival-specific filtering (type='Festival')
- Artist gigography (concert history)
- Venue and metro area search
- Location-based search (geo coordinates, IP address, metro areas)

**Endpoint Examples:**
```
GET /api/3.0/events.json?apikey={key}&artist_name=vampire+weekend&location=ip:94.228.36.39
GET /api/3.0/events.json?apikey={key}&location=sk:28426&type=Festival
```

**Pros:**
- Comprehensive global coverage
- Festival-specific filtering
- Artist-centric search
- Historical concert data

**Cons:**
- Rate limits not clearly documented
- May require partnership for commercial use

### Ticketmaster Discovery API v2.0
**Coverage:** 230K+ events across US, Canada, Mexico, Australia, NZ, UK, Ireland, Europe
**Authentication:** API key required
**Rate Limits:** Not specified
**Key Features:**
- Event search with extensive filters (keyword, attraction, venue, location, date, classification)
- Festival-specific classification filtering
- Dynamic pricing signals
- Venue capacity and details
- International coverage via separate API

**Endpoint Examples:**
```
GET /discovery/v2/events.json?apikey={key}&classificationName=music&countryCode=US
GET /discovery/v2/events.json?apikey={key}&attractionId={artist_id}&startDateTime=2025-06-01T00:00:00Z
```

**Pros:**
- Massive event database
- Detailed venue information
- Pricing and availability data
- Strong international coverage
- Official API with clear documentation

**Cons:**
- Primarily ticketed events (may miss some festivals)
- Rate limiting may be aggressive
- Commercial terms may apply

### JamBase Data API
**Coverage:** Global live music database
**Authentication:** API key required
**Rate Limits:** 1 credit/call for most endpoints
**Key Features:**
- Event search with filters
- Artist search and details
- Venue search and details
- Stream data sources integration
- Multiple data source lookups

**Endpoint Examples:**
```
GET /events?artistName={name}&dateFrom={date}
GET /artists/{artistDataSource}:{artistId}
GET /venues/id/{venueDataSource}:{venueId}
```

**Pros:**
- Multi-source integration
- Clean REST API
- Venue capacity data
- Artist/venue/event cross-references

**Cons:**
- Credit-based pricing
- May have limited festival-specific data

### Bandsintown API
**Coverage:** Largest database of upcoming concert listings
**Authentication:** app_id parameter (free for artists, partnership for organizations)
**Rate Limits:** Strict enforcement, 404 caching required
**Key Features:**
- Artist event listings (upcoming, past, date-range)
- Artist information
- Venue details
- RSVP and notification tracking

**Endpoint Examples:**
```
GET /artists/{artistname}/events?app_id={id}&date=2025-06-01,2025-12-31
GET /artists/{artistname}?app_id={id}
```

**Pros:**
- Massive concert database
- Artist-centric approach
- Fan engagement data
- Well-documented

**Cons:**
- Partnership required for broader commercial use
- Strict rate limiting and 404 monitoring
- Primarily concert-focused, less festival data

### Resident Advisor (RA)
**Coverage:** 100+ cities, electronic music focus
**Authentication:** No official API key (unofficial GraphQL endpoint)
**Rate Limits:** Not documented (ToS prohibits commercial use)
**Key Features:**
- Electronic music events and club nights
- DJ lineups and set times
- Venue capacity and details
- Promoter information
- Audience interest metrics

**Unofficial GraphQL Endpoint:**
```
POST https://ra.co/graphql
```

**Pros:**
- Best-in-class electronic music coverage
- Deep per-city EU coverage
- Structured lineups
- Venue capacity data
- Promoter information

**Cons:**
- **ToS explicitly bans automated extraction for commercial purposes**
- No official API
- Using GraphQL endpoint is ToS violation
- Partnership required for legitimate access

**Recommendation:** Send partnership email to RA, do not scrape without permission

## 2. Artist Data Sources

### Spotify Web API
**Coverage:** Global streaming platform
**Authentication:** OAuth 2.0
**Rate Limits:** App rate limit (requests per 30s)
**Key Features:**
- Artist search and details
- Top tracks by country
- Audio features (danceability, energy, tempo, etc.)
- Related artists
- Album and release data
- Popularity scores (0-100)

**Endpoint Examples:**
```
GET /v1/search?q=artist:{name}&type=artist
GET /v1/artists/{id}/top-tracks?market=US
GET /v1/artists/{id}/related-artists
GET /v1/audio-features/{track_id}
```

**Pros:**
- Comprehensive artist data
- Rich audio features for ML
- Global popularity metrics
- Related artist discovery
- Official, stable API

**Cons:**
- Requires OAuth authentication
- Rate limiting
- Limited to Spotify catalog

### MusicBrainz API
**Coverage:** Comprehensive music database (community-driven)
**Authentication:** No API key required (meaningful User-Agent required)
**Rate Limits:** 1 request/second
**Key Features:**
- Artist, release, label, venue data
- ISRC/UPC lookups
- Cross-platform identifier mapping
- Genre and style classifications
- Historical release data

**Endpoint Examples:**
```
GET /ws/2/artist/{mbid}?inc=releases+url-rels+tags
GET /ws/2/search?type=artist&query={name}
GET /ws/2/release/{mbid}?inc=artist-credits+labels
```

**Pros:**
- Free for non-commercial use
- Comprehensive database
- Cross-platform ID mapping
- No authentication required
- Rich metadata

**Cons:**
- Rate limited (1 req/sec)
- Commercial use requires paid plan
- Data quality varies (community-edited)

### Discogs API v2.0
**Coverage:** Music database (releases, artists, labels)
**Authentication:** OAuth 1.0a for user data, no auth for most queries
**Rate Limits:** 60 requests/minute (authenticated), 25/minute (unauthenticated)
**Key Features:**
- Artist profiles and releases
- Label catalogs
- Marketplace data
- Release versions and formats
- Genre/style classifications

**Endpoint Examples:**
```
GET /artists/{artist_id}
GET /artists/{artist_id}/releases
GET /database/search?q={query}&type=artist
GET /labels/{label_id}/releases
```

**Pros:**
- Comprehensive release data
- Label information
- Marketplace insights
- Rich metadata
- Official Python client available

**Cons:**
- Rate limiting
- Search requires authentication
- Less focused on live events

### SAMBL (Streaming Artist MusicBrainz Lookup)
**Coverage:** Cross-platform artist lookup tool
**Authentication:** No API key (public API)
**Rate Limits:** Not specified
**Key Features:**
- Cross-platform artist lookup (Spotify, Deezer, Tidal, Bandcamp, SoundCloud)
- ISRC/UPC lookups across platforms
- Album matching across services
- MusicBrainz ID resolution

**Endpoint Examples:**
```
GET /api/find?type=UPC&code={upc}
GET /api/lookupArtist?provider_id={id}&provider=spotify
GET /api/compareArtistAlbums?provider_id={id}&provider=spotify&mbid={mbid}
```

**Pros:**
- Cross-platform integration
- Resolves artist identities across services
- Open source
- No authentication required

**Cons:**
- Third-party service (uptime risk)
- Limited documentation
- May have rate limits

## 3. Festival App Scraping Methods

### GreenCopper/Aloompa FestApp
**Coverage:** Major festivals using Aloompa platform (Bonnaroo, Lightning in a Bottle, etc.)
**Authentication:** None (local SQLite extraction)
**Method:** Extract SQLite database from iOS app container
**Key Features:**
- Complete festival schedules
- Artist stage assignments
- Set times
- Venue information
- Real-time updates

**Extraction Process:**
1. Download iOS festival app from App Store
2. Launch app and let it update data
3. Locate app container in `~/Library/Containers/`
4. Copy `Documents/db.sqlite` database
5. Export schedule JSON
6. Transform to desired format

**Tools:**
- `clashfinder` GitHub project (porkcharsui/clashfinder)
- Custom SQLite extraction scripts

**Pros:**
- Complete, official data
- Real-time updates
- No API authentication
- High data quality

**Cons:**
- Requires physical iOS device or macOS
- Manual process per festival
- May violate ToS
- Not scalable

**Legal Considerations:** Gray area - extracting local data vs. scraping API

### Appmiral
**Coverage:** Shambhala Festival
**Authentication:** None (web scraping)
**Method:** Web scraping of festival app data
**Key Features:**
- Schedule data
- Artist information
- Stage assignments

**Pros:**
- Festival-specific data
- No authentication

**Cons:**
- Single festival coverage
- May violate ToS
- Fragile to UI changes

### Clashfinder Feed Tools
**Coverage:** Multiple festivals via official apps
**Authentication:** Clashfinder cookie for upload
**Method:** Extract from official festival apps
**Key Features:**
- Schedule and artist data
- Stage information
- Set times
- Clashfinder-compatible output

**Supported Apps:**
- Appmiral (Shambhala)
- GreenCopper/Aloompa (Lightning in a Bottle)

**Pros:**
- Standardized output format
- Multiple festival support
- Community-maintained

**Cons:**
- Manual setup per festival
- Requires app installation
- Limited to supported festivals

## 4. Social Media Scraping

### Twitter/X API
**Coverage:** Global social media platform
**Authentication:** API key (paid tiers available)
**Rate Limits:** Varies by tier
**Key Features:**
- Tweet search and filtering
- User timelines
- Sentiment analysis data
- Engagement metrics
- Real-time streaming

**Use Cases:**
- Artist sentiment tracking
- Festival buzz monitoring
- Trend detection
- Audience engagement analysis

**Pros:**
- Real-time data
- Rich engagement metrics
- Historical data available
- Official API

**Cons:**
- Paid tiers for full access
- Rate limiting
- Content restrictions

### Instagram Scraping
**Coverage:** Global visual social platform
**Authentication:** No official API for scraping
**Methods:**
- RapidAPI integration
- Browser automation (Selenium)
- Mobile app automation

**Tools:**
- `ArtistScrapping` GitHub project (pouriamrt/ArtistScrapping)
- `invi` social media scraper (Maddy824/invi)

**Pros:**
- Visual content analysis
- Engagement metrics
- Artist lifestyle insights

**Cons:**
- No official scraping API
- ToS violations likely
- High detection risk
- Fragile to platform changes

### Facebook Scraping
**Coverage:** Global social platform
**Authentication:** Limited official API
**Methods:**
- Selenium browser automation
- Official Graph API (limited)

**Tools:**
- `ArtistScrapping` (Facebook scraping via Selenium)

**Pros:**
- Event data
- Page insights
- Fan engagement

**Cons:**
- Strict API limitations
- Scraping violates ToS
- High detection risk

### YouTube Data API v3
**Coverage:** Global video platform
**Authentication:** API key
**Rate Limits:** 10,000 units/day
**Key Features:**
- Video performance metrics
- Channel analytics
- Comment data
- Search functionality

**Use Cases:**
- Artist music video performance
- Engagement tracking
- Comment sentiment analysis

**Pros:**
- Official API
- Rich analytics
- Large dataset

**Cons:**
- Rate limiting
- Quota-based pricing
- Limited to YouTube content

## 5. C3 Presents Integration Strategy

### C3 Portfolio
**Festivals:**
- Austin City Limits (ACL) - Austin, TX
- Lollapalooza - Chicago, IL (plus international editions)
- Bonnaroo - Manchester, TN
- Governors Ball - New York, NY
- Ohana - Huntington Beach, CA
- Darker Waves - Huntington Beach, CA
- 33 total festivals including 7 Lollapaloozas

**Data Availability:**
- Economic impact studies (AngelouEconomics partnership)
- Attendance data (75K-81K daily for ACL)
- Lineup data (140-150 artists)
- Vendor data (64-77 local vendors)
- Ticket pricing data
- Venue capacity data

### Integration Approaches

#### 1. Official Partnership (Recommended)
**Contact:** C3 Presents business development
**Data Access:**
- Direct API access to festival data
- Lineup announcements
- Ticket sales data
- Attendance metrics
- Historical performance data

**Benefits:**
- Legitimate data access
- Real-time updates
- High data quality
- Partnership opportunities

**Challenges:**
- Partnership negotiation
- Potential costs
- Data sharing agreements

#### 2. Public Data Scraping
**Sources:**
- Official festival websites
- Social media announcements
- Ticketmaster listings
- Press releases
- Economic impact reports

**Methods:**
- Web scraping (respect robots.txt)
- Social media monitoring
- RSS feeds
- Email newsletters

**Benefits:**
- No partnership required
- Immediate start
- Multiple data sources

**Challenges:**
- Data quality varies
- May violate ToS
- Not real-time
- Fragile to changes

#### 3. Third-Party Aggregators
**Sources:**
- Songkick (C3 festivals listed)
- Ticketmaster (official ticketing)
- Bandsintown (artist tour data)
- Resident Advisor (electronic festivals)

**Benefits:**
- Standardized APIs
- Reliable data
- Multiple festivals

**Challenges:**
- May not have all C3 festivals
- Rate limiting
- Commercial terms

#### 4. Festival App Extraction
**Sources:**
- Official C3 festival apps (Aloompa-powered)
- SQLite database extraction
- App API reverse engineering

**Benefits:**
- Complete data
- Real-time updates
- Official source

**Challenges:**
- ToS violations
- Technical complexity
- Manual process
- Not scalable

### Recommended C3 Strategy

**Phase 1: Public Data Aggregation**
- Scrape official festival websites
- Monitor social media announcements
- Use Ticketmaster/Songkick APIs
- Build historical dataset

**Phase 2: Partnership Outreach**
- Contact C3 Presents for data partnership
- Propose value exchange (analytics, insights)
- Negotiate data access terms
- Build direct integration

**Phase 3: Enhanced Data Collection**
- Integrate official C3 data feed
- Add real-time ticket sales data
- Incorporate attendance metrics
- Build predictive models

## 6. Scraping Best Practices

### Legal and Ethical Considerations

**Do:**
- Respect robots.txt
- Use official APIs when available
- Implement rate limiting
- Cache responses appropriately
- Monitor for ToS changes
- Send partnership requests for commercial use

**Don't:**
- Ignore rate limits
- Scrape without permission when ToS prohibits
- Overload servers with requests
- Use data for prohibited purposes
- Reverse engineer private APIs
- Violate terms of service

### Technical Best Practices

**Rate Limiting:**
- Implement exponential backoff
- Respect documented rate limits
- Use request queues
- Monitor response times

**Data Quality:**
- Validate data structures
- Handle missing fields gracefully
- Implement data deduplication
- Cross-reference multiple sources

**Error Handling:**
- Retry failed requests
- Log errors appropriately
- Implement circuit breakers
- Handle API changes gracefully

**Caching:**
- Cache 404 responses (24h+)
- Cache successful responses (appropriate TTL)
- Use conditional requests (ETag, Last-Modified)
- Implement cache invalidation

**Detection Avoidance:**
- Use realistic user agents
- Vary request timing
- Rotate IP addresses if needed
- Respect robots.txt
- Consider mobile app extraction for hard-to-scrape sites

## 7. Recommended Data Pipeline Architecture

### Multi-Source Ingestion

```
┌─────────────────┐
│  Data Sources   │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
┌───▼───┐ ┌──▼────┐
│ APIs  │ │Scrapers│
└───┬───┘ └───┬───┘
    │         │
    └────┬────┘
         │
    ┌────▼────┐
    │ Ingestion│
    │  Engine  │
    └────┬────┘
         │
    ┌────▼────┐
    │  Entity  │
    │Resolution│
    └────┬────┘
         │
    ┌────▼────┐
    │ Database │
    └─────────┘
```

### Data Sources Priority

**Tier 1 (Official APIs - Start Here):**
1. Spotify Web API (artist data, streaming metrics)
2. MusicBrainz API (artist metadata, cross-platform IDs)
3. Ticketmaster Discovery API (event data, venues)
4. Songkick API (festival events, artist tours)

**Tier 2 (Partnership Required):**
1. Bandsintown API (concert data)
2. Resident Advisor (electronic music events)
3. C3 Presents (direct festival data)

**Tier 3 (Scraping - Use with Caution):**
1. Festival websites (lineup announcements)
2. Social media (sentiment, buzz)
3. Festival apps (schedule data)
4. Third-party aggregators (complementary data)

### Entity Resolution Strategy

**Artist Identity:**
- MusicBrainz ID as canonical identifier
- Cross-reference Spotify ID, Discogs ID
- Normalize artist names
- Handle aliases and variations

**Festival Identity:**
- Festival name + location + year
- Cross-reference multiple sources
- Handle name variations
- Track festival editions

**Venue Identity:**
- Venue name + location
- Cross-reference Ticketmaster, Songkick
- Handle venue name changes
- Track venue capacity changes

## 8. Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2)
- Set up Spotify API integration
- Implement MusicBrainz API client
- Build entity resolution system
- Create database schema for multi-source data

### Phase 2: Event Data (Weeks 3-4)
- Integrate Ticketmaster Discovery API
- Add Songkick API
- Build event normalization pipeline
- Implement festival-specific data models

### Phase 3: Artist Enrichment (Weeks 5-6)
- Add Discogs API for release data
- Implement audio features from Spotify
- Build artist similarity graphs
- Create artist momentum metrics

### Phase 4: Social Signals (Weeks 7-8)
- Implement Twitter/X API integration
- Add sentiment analysis pipeline
- Build buzz tracking system
- Create social engagement metrics

### Phase 5: C3 Integration (Weeks 9-10)
- Scrape official C3 festival websites
- Monitor C3 social media
- Build C3-specific data models
- Prepare partnership outreach materials

### Phase 6: Advanced Features (Weeks 11-12)
- Implement festival app extraction (if approved)
- Add real-time data streaming
- Build predictive models
- Create analytics dashboards

## 9. Risk Assessment

### Technical Risks
- **API Rate Limiting:** Mitigate with caching, rate limiting, request queuing
- **API Changes:** Monitor for changes, implement versioned clients
- **Data Quality:** Cross-reference sources, validate data, implement quality checks
- **Scalability:** Design for horizontal scaling, use message queues

### Legal Risks
- **ToS Violations:** Respect robots.txt, use official APIs, seek partnerships
- **Data Ownership:** Clarify data rights in partnerships, comply with terms
- **Privacy:** Anonymize user data, comply with GDPR/CCPA
- **Copyright:** Respect data rights, provide attribution

### Business Risks
- **Partnership Costs:** Budget for API costs, partnership fees
- **Data Availability:** Have fallback sources, implement graceful degradation
- **Competition:** Differentiate with analytics, predictions, insights
- **Market Changes:** Monitor industry trends, adapt to new data sources

## 10. Conclusion

The Festival Intelligence Terminal should prioritize official APIs for data collection, with strategic partnerships for enhanced access. C3 Presents represents a high-value integration target that should be approached through official partnership channels. Scraping should be used cautiously and only when no legitimate alternatives exist, with proper respect for terms of service and rate limits.

**Key Recommendations:**
1. Start with Spotify, MusicBrainz, Ticketmaster, and Songkick APIs
2. Implement robust entity resolution for artist/festival/venue identities
3. Reach out to C3 Presents for data partnership
4. Use scraping only for public data with clear ToS compliance
5. Build flexible architecture to accommodate new data sources
6. Implement comprehensive error handling and rate limiting
7. Monitor for API changes and ToS updates
8. Create data quality monitoring and alerting
