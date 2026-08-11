# Festival Bloomberg Specification Gap Analysis

## Executive Summary

The current Festival Intelligence Terminal implementation is a basic MVP that needs significant architectural expansion to meet the Festival Bloomberg specification requirements. The spec defines an enterprise-grade data warehouse with comprehensive source lineage, legal compliance, cost optimization, and historical backtesting capabilities.

## Current Implementation vs Festival Bloomberg Spec

### 1. Database Schema

**Current Implementation:**
- Basic tables: Artist, Festival, StreamingHistory, SocialHistory, FestivalAppearance, FestivalLineup, Contact, User, Message, Group, NewsItem, Prediction, DataQualityLog
- Simple relationships without source lineage
- No evidence tracking or identity resolution tables
- Missing source registry, cost tracking, audit trails

**Festival Bloomberg Requirements:**
- Comprehensive source registry with policy gates
- Evidence-backed lineage tracking
- Identity resolution with MBID/QID candidates
- Cost event tracking and budget management
- Portfolio relationship management
- Format-specific parser metadata
- Historical backtest tables with point-in-time safety
- Quality control and quarantine tables

**Gap:** **CRITICAL** - Complete schema redesign required

### 2. Architecture

**Current Implementation:**
- FastAPI backend with PostgreSQL
- Basic Monid.ai integration
- Simple frontend with React
- No object storage or analytical warehouse
- No cost optimization or tiered scraping

**Festival Bloomberg Requirements:**
- Tiered scraping: HTTP → Playwright → Monid → Apify
- Cloudflare R2 for immutable object storage
- DuckDB for analytical aggregation
- Supabase for serving canonical data
- Cost optimization engine with budget classes
- Policy gates and legal compliance checks
- Multi-stage pipeline with idempotency

**Gap:** **CRITICAL** - Complete architectural redesign required

### 3. Data Collection

**Current Implementation:**
- Basic API integrations (MusicBrainz, Setlist.fm, Ticketmaster)
- No source registry or policy controls
- No evidence retention or lineage tracking
- No cost optimization
- No legal compliance framework

**Festival Bloomberg Requirements:**
- Source registry with robots/terms review
- Policy gates for every acquisition
- Evidence-first approach with content hashing
- Multi-tier acquisition with cost optimization
- Legal compliance boundaries (no SSL bypass, no private API interception)
- Comprehensive observability and cost tracking
- Cache-first approach with conditional retrieval

**Gap:** **CRITICAL** - Complete data collection redesign required

### 4. Entity Resolution

**Current Implementation:**
- Basic name normalization
- No MBID/QID integration
- No probabilistic matching
- No merge history or conflict resolution

**Festival Bloomberg Requirements:**
- MBID and Wikidata QID candidate generation
- Probabilistic matching with confidence scores
- Evidence-backed identity resolution
- Merge history with reversibility
- Hard conflict detection
- Review queue for ambiguous matches

**Gap:** **HIGH** - Entity resolution system required

### 5. C3 Festival Integration

**Current Implementation:**
- Generic festival support
- No C3-specific logic
- No format-specific parsers
- No portfolio relationship tracking

**Festival Bloomberg Requirements:**
- Full C3 portfolio coverage (30+ festivals)
- Format-specific parsers (poster_grid, day_stage_schedule, multi_weekend, etc.)
- Production role tracking (producer, co_producer, presenter, promoter)
- International Lollapalooza editions with local partners
- Portfolio evidence registry
- Format-aware analytics

**Gap:** **HIGH** - C3-specific implementation required

### 6. Analytics & Backtesting

**Current Implementation:**
- Basic prediction endpoints
- No historical backtesting
- No point-in-time feature construction
- No momentum scoring or arbitrage detection

**Festival Bloomberg Requirements:**
- Historical backtest system with rolling-origin evaluation
- Point-in-time safe feature construction
- Momentum scoring from Last.fm, RYM, Wikipedia
- Booking arbitrage detection
- Placement scoring with format-specific logic
- Economic modeling with uncertainty intervals
- Leave-one-property-out evaluation

**Gap:** **HIGH** - Analytics system redesign required

### 7. Data Quality & Governance

**Current Implementation:**
- Basic error handling and logging
- No data quality controls
- No audit trails
- No legal compliance tracking
- No retention schedules

**Festival Bloomberg Requirements:**
- Comprehensive data quality checks
- Audit trails for all operations
- Legal review tracking per source
- Retention schedules for personal data
- Role-based access control
- Evidence coverage requirements
- Quarantine system for failed records
- Policy violation detection

**Gap:** **HIGH** - Governance system required

### 8. LLM Extraction

**Current Implementation:**
- No LLM extraction
- No structured data extraction from unstructured sources

**Festival Bloomberg Requirements:**
- Python Instructor with Pydantic schemas
- LLM extraction for unstructured content
- Evidence binding for extracted fields
- Model and prompt versioning
- Token usage tracking
- Validation with deterministic rules

**Gap:** **MEDIUM** - LLM extraction system required

## Implementation Priority Matrix

### Phase 1: Foundation (Critical - 4-6 weeks)
1. **Database Schema Redesign**
   - Implement source registry tables
   - Add lineage tracking and evidence binding
   - Create identity resolution tables
   - Add cost tracking and audit tables

2. **Architecture Setup**
   - Integrate Cloudflare R2 for object storage
   - Set up DuckDB warehouse
   - Configure Supabase for serving layer
   - Implement tiered scraping framework

3. **Source Registry & Policy Gates**
   - Build source registration system
   - Implement robots/terms review tracking
   - Add policy decision engine
   - Create cost optimization gateway

### Phase 2: Data Collection (High - 6-8 weeks)
1. **Tiered Scraping Implementation**
   - HTTP/Python parser layer
   - Playwright browser automation
   - Monid integration behind adapter
   - Apify integration for scale

2. **Evidence & Lineage System**
   - Content hashing and caching
   - Evidence binding for all assertions
   - Source document tracking
   - Parser versioning

3. **Legal Compliance Framework**
   - Policy violation detection
   - Prohibited pattern blocking
   - Retention schedule enforcement
   - Access control boundaries

### Phase 3: Entity Resolution (High - 4-6 weeks)
1. **MBID/QID Integration**
   - MusicBrainz API integration
   - Wikidata API integration
   - Candidate generation pipeline
   - Probabilistic matching

2. **Identity Resolution System**
   - Merge history tracking
   - Conflict detection
   - Review queue implementation
   - Reversibility guarantees

### Phase 4: C3 Integration (High - 6-8 weeks)
1. **Portfolio Registry**
   - Seed all C3 festivals
   - Production role tracking
   - International edition support
   - Portfolio evidence system

2. **Format-Specific Parsers**
   - Poster OCR for Lollapalooza/ACL
   - Schedule JSON parsing
   - Multi-weekend handling
   - Genre-curated grid parsing

3. **C3-Specific Analytics**
   - Format-aware placement scoring
   - Currency conversion
   - Local partner tracking
   - International normalization

### Phase 5: Analytics & Backtesting (High - 8-10 weeks)
1. **Historical Data Ingestion**
   - Last.fm API integration
   - RYM permitted exports
   - Wikipedia pageview analytics
   - Historical lineup reconstruction

2. **Feature Engineering**
   - Point-in-time safe features
   - Momentum scoring
   - Placement scoring
   - Missingness indicators

3. **Backtest System**
   - Rolling-origin evaluation
   - Booking arbitrage detection
   - Economic modeling
   - Leave-one-property-out validation

### Phase 6: LLM Extraction (Medium - 4-6 weeks)
1. **Extraction Pipeline**
   - Python Instructor setup
   - Pydantic schema definitions
   - LLM integration
   - Evidence binding

2. **Quality Control**
   - Validation rules
   - Confidence scoring
   - Review queue
   - Model versioning

### Phase 7: Governance & Operations (Medium - 4-6 weeks)
1. **Data Quality System**
   - Automated quality checks
   - Quarantine workflow
   - QA report generation
   - Alerting system

2. **Observability**
   - Cost tracking dashboards
   - Performance monitoring
   - Source health tracking
   - Policy violation alerts

## Technical Debt Migration

### Current Components to Preserve
- FastAPI backend structure (expand with new endpoints)
- React frontend (enhance with new features)
- Basic database models (migrate to new schema)
- Monid.ai integration (rebuild behind adapter)
- Configuration system (expand for new settings)

### Components to Deprecate
- Current database schema (migrate data to new schema)
- Simple scraping methods (replace with tiered approach)
- Basic error handling (expand with comprehensive governance)
- Current prediction models (rebuild with backtest framework)

## Resource Requirements

### Infrastructure
- Cloudflare R2 account for object storage
- Supabase account for serving layer
- DuckDB for local analytics
- Apify account for managed scraping
- Monid.ai production integration

### Development Resources
- Backend engineer (Python/FastAPI)
- Data engineer (pipeline/ETL)
- ML engineer (backtesting/analytics)
- Frontend engineer (React enhancements)
- DevOps engineer (infrastructure)

### Timeline Estimate
- **Phase 1:** 4-6 weeks
- **Phase 2:** 6-8 weeks  
- **Phase 3:** 4-6 weeks
- **Phase 4:** 6-8 weeks
- **Phase 5:** 8-10 weeks
- **Phase 6:** 4-6 weeks
- **Phase 7:** 4-6 weeks

**Total:** 36-50 weeks for full Festival Bloomberg compliance

## Recommended Approach

### Option 1: Full Migration (Recommended)
- Complete architectural redesign to match Festival Bloomberg spec
- 36-50 week timeline
- Enterprise-grade result
- Future-proof for scaling

### Option 2: Hybrid Approach
- Keep current MVP for basic functionality
- Build Festival Bloomberg system in parallel
- Gradual migration of features
- Longer timeline but lower risk

### Option 3: Incremental Enhancement
- Enhance current system with Festival Bloomberg components gradually
- Start with highest-priority gaps (schema, source registry)
- 12-18 month timeline
- Risk of technical debt accumulation

## Conclusion

The Festival Bloomberg specification represents a significant expansion beyond the current MVP. The current implementation provides a foundation but requires substantial architectural changes to meet the spec's requirements for enterprise-grade data warehousing, legal compliance, cost optimization, and historical backtesting.

**Recommendation:** Pursue Option 1 (Full Migration) with a phased approach, starting with Phase 1 (Foundation) to establish the new architecture while preserving current functionality where possible.
