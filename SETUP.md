# Festival Intelligence Terminal - Setup Guide

This guide will help you set up and run the Festival Intelligence Terminal locally.

## Prerequisites

- Python 3.10 or higher
- Node.js 18 or higher
- Docker and Docker Compose (optional, for containerized setup)
- Git

## Quick Start

### Option 1: Local Development

#### 1. Clone the Repository

```bash
cd /Users/scottthomasswitzer/CascadeProjects/festival-intelligence
```

#### 2. Set Up Python Environment

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt
```

#### 3. Set Up Environment Variables

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your API keys
# Required for full functionality:
# - MUSICBRAINZ_USER_AGENT (your email)
# - SETLISTFM_API_KEY
# - TICKETMASTER_API_KEY
# - YOUTUBE_API_KEY
# - BEA_API_KEY
# - BLS_API_KEY
```

#### 4. Initialize Database

```bash
# Using PostgreSQL (recommended)
# Install PostgreSQL locally or use Docker
docker-compose up -d postgres

# Run schema initialization
psql -h localhost -U festival_user -d festival_intelligence -f warehouse/schema.sql
```

Or use DuckDB for development (no database server required):

```bash
# The application will use DuckDB automatically if DATABASE_URL is not set
```

#### 5. Collect Sample Data

```bash
# Run data collection script
python scripts/collect_lineup_data.py --all
```

#### 6. Start the API Server

```bash
cd apps/api
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`

#### 7. Start the Frontend

```bash
cd apps/web
npm install
npm run dev
```

The frontend will be available at `http://localhost:3000`

### Option 2: Docker Compose

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## Project Structure

```
festival-intelligence/
├── apps/
│   ├── api/              # FastAPI backend
│   └── web/              # Next.js frontend
├── pipelines/           # Data ingestion pipelines
├── models/              # Prediction models
├── warehouse/           # Data warehouse layers
├── contracts/           # Pydantic data contracts
├── scripts/             # Utility scripts
├── notebooks/           # Jupyter notebooks
├── tests/               # Test suite
└── docs/                # Documentation
```

## API Endpoints

### Health Check
- `GET /health` - Health check endpoint

### Festivals
- `GET /festivals` - Get all festivals
- `GET /festivals/{festival_id}` - Get festival by ID
- `GET /festivals/{festival_id}/comparison` - Get festival comparison metrics
- `GET /festivals/compare?festival_ids=...` - Compare multiple festivals
- `GET /festivals/{festival_id}/location-intelligence` - Get location intelligence

### Artists
- `POST /artists/search` - Search for artists
- `GET /artists/{artist_id}` - Get artist by ID
- `GET /artists/{artist_id}/momentum` - Get artist momentum metrics
- `GET /artists/{artist_id}/booking-value` - Get booking value index
- `GET /artists/{artist_id}/tour-prediction` - Get tour prediction

### Revenue
- `POST /revenue/simulate` - Run revenue simulation

### Market
- `GET /market/overview` - Get market overview

## Data Collection

### Manual Data Collection

Use the provided scripts to collect data:

```bash
# Collect festival data
python scripts/collect_lineup_data.py --festivals

# Create sample lineup data
python scripts/collect_lineup_data.py --lineups

# Create sample artist data
python scripts/collect_lineup_data.py --artists

# Collect all data
python scripts/collect_lineup_data.py --all
```

### Using Jupyter Notebooks

```bash
# Start Jupyter
jupyter notebook notebooks/

# Open 01_data_collection.ipynb to explore data collection
```

## API Keys Setup

### MusicBrainz
- No API key required
- Set `MUSICBRAINZ_USER_AGENT` to your email address
- Rate limit: 1 request per second

### setlist.fm
- Apply for API key at https://api.setlist.fm/docs/1.0/index.html
- Set `SETLISTFM_API_KEY` in `.env`

### Ticketmaster
- Apply for API key at https://developer.ticketmaster.com/
- Set `TICKETMASTER_API_KEY` in `.env`
- Rate limit: 5,000 calls daily

### YouTube
- Create project at https://console.cloud.google.com/
- Enable YouTube Data API v3
- Set `YOUTUBE_API_KEY` in `.env`

### BEA (Bureau of Economic Analysis)
- Apply for API key at https://apps.bea.gov/api/signup/
- Set `BEA_API_KEY` in `.env`

### BLS (Bureau of Labor Statistics)
- Apply for API key at https://api.bls.gov/publicAPI/v2/
- Set `BLS_API_KEY` in `.env`

## Development

### Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-cov

# Run tests
pytest tests/

# Run with coverage
pytest --cov=. tests/
```

### Code Style

```bash
# Format code
black .

# Lint code
ruff check .

# Type checking
mypy .
```

### Adding New Data Sources

1. Create a new pipeline in `pipelines/{source_name}/`
2. Add data contracts in `contracts/`
3. Update `source_registry.yml`
4. Add integration tests in `tests/`

## Troubleshooting

### Database Connection Issues

```bash
# Check PostgreSQL status
docker-compose ps postgres

# View PostgreSQL logs
docker-compose logs postgres

# Reset database
docker-compose down -v
docker-compose up -d postgres
```

### API Rate Limiting

If you encounter rate limiting errors:
- Increase delays in pipeline clients
- Implement caching for API responses
- Use the provided rate limit settings

### Frontend Build Errors

```bash
# Clear Next.js cache
rm -rf apps/web/.next

# Reinstall dependencies
cd apps/web
rm -rf node_modules package-lock.json
npm install
```

## Production Deployment

### Environment Variables

Set the following environment variables in production:

- `DATABASE_URL` - PostgreSQL connection string
- `ENVIRONMENT` - Set to `production`
- `LOG_LEVEL` - Set to `INFO` or `WARNING`
- All API keys for data sources

### Security Considerations

- Use strong database passwords
- Enable HTTPS
- Implement API authentication
- Use secrets management for API keys
- Enable CORS only for trusted domains

## Data Freshness

The system supports incremental data updates:

- **Full rebuild**: Weekly (recommended)
- **Daily updates**: Artist momentum, tour predictions
- **Hourly updates**: Weather data, future events

Configure scheduling using APScheduler or external cron jobs.

## Support

For issues or questions:
- Check the documentation in `docs/`
- Review data source terms in `source_registry.yml`
- Examine model cards in `docs/model_cards/`

## License

See LICENSE file for details.
