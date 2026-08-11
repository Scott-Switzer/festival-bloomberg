# Festival Intelligence Terminal - Architecture Documentation

## System Overview

The Festival Intelligence Terminal is a decision-support platform for festival talent buyers and promoters. It combines artist momentum, touring behavior, market economics, weather, transportation, and festival competition to evaluate booking and revenue scenarios.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         External APIs                             │
│  MusicBrainz | setlist.fm | Ticketmaster | YouTube | Wikimedia  │
│    GDELT   |    NWS/NOAA   |    BTS    | Census  |  BEA | BLS  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Data Pipelines                              │
│  pipelines/musicbrainz | pipelines/setlistfm | pipelines/tm   │
│  pipelines/youtube | pipelines/wikimedia | pipelines/gdelt     │
│  pipelines/weather | pipelines/transportation                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Entity Resolution                             │
│              pipelines/entity_resolution.py                      │
│         MusicBrainz ID as canonical identity                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Data Warehouse                               │
│  warehouse/raw/     → Immutable API snapshots (Parquet)         │
│  warehouse/normalized/ → Canonical entities (PostgreSQL/DuckDB)  │
│  warehouse/features/  → Feature store for models                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Prediction Models                              │
│  models/artist_value/     → Booking Value Index                  │
│  models/tour_prediction/  → Tour/festival appearance probability │
│  models/festival_comparison/ → Lineup analysis                  │
│  models/demand/           → Demand forecasting                   │
│  models/revenue_simulation/ → Monte Carlo revenue scenarios      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       FastAPI Backend                            │
│              apps/api/main.py (REST API)                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Next.js Frontend                             │
│         apps/web/ (Terminal-style dashboard)                     │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Data Ingestion

**Raw Layer (`warehouse/raw/`)**
- Immutable snapshots from external APIs
- Stored in Parquet format for efficient querying
- Includes API response metadata (retrieved_at, source, confidence)

**Example:**
```python
# Raw MusicBrainz artist data
{
    "musicbrainz_id": "f7d31c5f-c712-4603-8eb4-3b0b846c4f3c",
    "raw_response": {...},
    "source": "musicbrainz",
    "retrieved_at": "2024-01-15T10:30:00Z",
}
```

### 2. Entity Resolution

**Canonical Identity Layer**
- MusicBrainz ID as primary key
- Maps external IDs (Wikidata QID, Ticketmaster ID, YouTube Channel ID, etc.)
- Confidence-scored mappings
- Manual review for ambiguous matches

**Example:**
```python
ArtistMapping(
    musicbrainz_id="f7d31c5f-c712-4603-8eb4-3b0b846c4f3c",
    wikidata_id="Q173740",
    ticketmaster_id="K8vZ9175V7v",
    youtube_channel_id="UCABxRjTt5UxLW-irv2piBvQ",
    normalized_name="the weeknd",
    aliases=["weeknd", "theweeknd", "abel tesfaye"],
    confidence=1.0,
)
```

### 3. Normalization

**Normalized Layer (`warehouse/normalized/`)**
- Canonical entities (artists, festivals, venues, events)
- Cleaned and standardized data
- Foreign key relationships
- Data type validation via Pydantic contracts

### 4. Feature Engineering

**Feature Layer (`warehouse/features/`)**
- Time-series features (momentum, growth rates)
- Aggregated features (venue progression, tour frequency)
- Market features (local affinity, genre momentum)
- All features include versioning for reproducibility

### 5. Model Inference

**Models**
- Feature versions tracked
- Model versions tracked
- Point-in-time predictions for backtesting
- Confidence intervals on all predictions

### 6. API Layer

**FastAPI Backend**
- RESTful endpoints for all data and predictions
- Pydantic request/response validation
- CORS enabled for frontend
- Rate limiting (to be implemented)

### 7. Frontend

**Next.js Application**
- Terminal-style UI design
- Real-time data visualization
- Interactive scenario modeling
- Export capabilities (CSV, PDF)

## Key Design Decisions

### 1. MusicBrainz as Canonical Identity

**Rationale:**
- Open and free for commercial use
- Stable IDs that don't change
- Rich metadata and relationships
- Community-maintained but reliable

**Trade-offs:**
- Rate limited to 1 request/second
- Some artists may be missing
- Requires entity resolution for name variants

### 2. Parquet for Raw Data

**Rationale:**
- Columnar format for efficient querying
- Schema evolution support
- Compression for storage efficiency
- Compatible with Polars, Pandas, DuckDB

### 3. PostgreSQL for Production

**Rationale:**
- ACID compliance for data integrity
- Complex queries and joins
- User authentication and permissions
- Mature ecosystem and tooling

**Development Alternative:**
- DuckDB for local development
- No database server required
- Same SQL interface
- Easy to switch to PostgreSQL

### 4. Pydantic for Data Contracts

**Rationale:**
- Runtime validation
- Automatic API documentation
- Type safety
- Easy serialization/deserialization

### 5. Booking Value Index vs. Dollar Estimates

**Rationale:**
- Without actual guarantee data, dollar estimates would be misleading
- BVI provides relative ranking without false precision
- Residual analysis identifies underbooked opportunities
- Credible for portfolio demonstration

### 6. Monte Carlo for Revenue

**Rationale:**
- Accounts for uncertainty in inputs
- Provides probability distributions (P10, P50, P90)
- More defensible than single-point estimates
- Enables scenario analysis

## Scalability Considerations

### Current Design (Portfolio Stage)

- **Database**: DuckDB (local) or PostgreSQL (single instance)
- **Scheduling**: Local scripts with APScheduler
- **Caching**: In-memory or file-based
- **Processing**: Single-machine batch processing

### Production Scale-Up Path

1. **Database**: 
   - PostgreSQL read replicas for query scaling
   - Connection pooling (PgBouncer)
   - Partitioning by date for time-series data

2. **Scheduling**:
   - Airflow or Prefect for orchestration
   - Celery for distributed task processing
   - Kubernetes for container orchestration

3. **Caching**:
   - Redis for API response caching
   - CDN for static assets
   - Materialized views for common queries

4. **Processing**:
   - Spark for large-scale data processing
   - Feature store (Feast) for model serving
   - Batch processing on scheduled intervals

## Security Considerations

### API Keys
- Stored in environment variables
- Never committed to version control
- Rotated regularly in production
- Different keys for dev/staging/prod

### Data Access
- Database authentication
- API rate limiting
- CORS restrictions
- User authentication (to be added)

### Data Privacy
- No personal data collected
- Only publicly available information
- Clear attribution for all sources
- Compliance with data source terms

## Monitoring and Observability

### Metrics to Track

**Data Pipeline:**
- API success/failure rates
- Data freshness indicators
- Entity resolution confidence
- Missing data alerts

**Model Performance:**
- Prediction accuracy (backtest results)
- Calibration metrics
- Feature importance drift
- Model version deployment

**Application:**
- API response times
- Error rates
- User engagement
- Feature usage

### Logging

- Structured logging with JSON format
- Log levels: DEBUG, INFO, WARNING, ERROR
- Include correlation IDs for request tracing
- Sensitive data redacted

## Testing Strategy

### Unit Tests
- Pipeline functions
- Model calculations
- Entity resolution logic
- Data contract validation

### Integration Tests
- API endpoints
- Database operations
- End-to-end data flows

### Backtests
- Tour prediction models
- Revenue simulation accuracy
- Momentum signal validity

### Manual Testing
- Data quality inspection
- UI usability testing
- Scenario validation

## Deployment

### Development
```bash
# Local development
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn apps.api.main:app --reload
cd apps/web && npm run dev
```

### Production
```bash
# Docker Compose
docker-compose up -d

# Or Kubernetes (future)
kubectl apply -f k8s/
```

## Data Source Limitations

### Public Data Constraints

The public-data version has inherent limitations:

1. **No actual ticket sales data**
   - Cannot measure real demand
   - Cannot validate revenue predictions
   - Must rely on proxies

2. **No artist guarantees**
   - Cannot estimate actual booking costs
   - BVI is relative, not absolute
   - Commercial value requires private data

3. **API rate limits**
   - MusicBrainz: 1 req/sec
   - Ticketmaster: 5,000/day
   - YouTube: Quota-based

4. **Data freshness**
   - Some sources have delays
   - Historical data only
   - No real-time sales velocity

### Commercial Path

The commercial version would integrate:

1. **Customer data**
   - Ticket sales curves
   - Customer geography
   - Artist guarantees
   - Marketing spend

2. **Proprietary learning**
   - Which signals predict real demand
   - Which artists outperform guarantees
   - Market-specific preferences
   - Pricing optimization

This proprietary data becomes the defensible moat.
