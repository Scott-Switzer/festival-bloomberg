# Festival Intelligence Terminal

A decision-support platform for festival talent buyers, promoters, booking agencies, sponsorship teams, venue and festival finance teams, and live-entertainment investors.

## Core Question

**Which artists should a festival book, at what billing level, in which market, and what is the expected demand and financial outcome?**

## Key Features

1. **Artist Intelligence** - Booking Value Index, momentum scores, festival fit analysis
2. **Tour Prediction** - Probability of touring/festival appearances with rolling backtests
3. **Festival Comparison** - Lineup overlap, genre diversification, competitive analysis
4. **Demand & Revenue Scenarios** - Monte Carlo forecasting with sensitivity analysis
5. **Location Intelligence** - Weather risk, air access, hotel pressure, market demographics

## Data Philosophy

- **Observed metrics**: Directly measured from public sources
- **Modeled estimates**: Statistically derived predictions with confidence intervals
- **Scenario assumptions**: User-selected inputs
- **Private-data fields**: Designed but unavailable in public demo

## Tech Stack

- **Data**: Python, Polars/Pandas, DuckDB, Parquet, PostgreSQL
- **Modeling**: scikit-learn, gradient boosting, SHAP
- **Backend**: FastAPI
- **Frontend**: Next.js/React
- **Visualization**: ECharts/Plotly, MapLibre, TanStack Table

## Project Structure

```
festival-intelligence/
├── apps/
│   ├── web/          # Next.js frontend
│   └── api/          # FastAPI backend
├── pipelines/        # Data ingestion pipelines
├── models/           # Prediction models
├── warehouse/        # Data warehouse layers
├── contracts/        # Pydantic data contracts
├── tests/            # Test suite
├── notebooks/        # Exploratory analysis
├── reports/          # Generated reports
└── docs/             # Documentation
```

## Build Sequence

- **Weeks 1-2**: Data foundation (canonical entities, API integration)
- **Weeks 3-4**: Artist terminal (momentum, Booking Value Index)
- **Week 5**: Festival comparison
- **Week 6**: Tour prediction models
- **Week 7**: Weather and travel intelligence
- **Week 8**: Revenue scenario engine
- **Week 9**: Product polish
- **Week 10**: Portfolio release

## License

See LICENSE file for details.

## Data Sources Registry

See `source_registry.yml` for complete data source documentation and licensing information.
