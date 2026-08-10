# festival-bloomberg

Festival Bloomberg specifications, backtesting assets, scraper ensemble, DuckDB warehouse, and VADER sentiment helpers.

## Quick checks

```bash
npm ci
npm test                 # TypeScript suite (scraper + metrics)
pip install -r requirements.txt
npm run test:python      # VADER + DuckDB path/init suite
```

DuckDB default path: `data/warehouse/festival_bloomberg.duckdb`  
Override with `FESTIVAL_BLOOMBERG_DUCKDB_PATH`. Legacy `FESTIVAL_INTELLIGENCE_DUCKDB_PATH` values are remapped to the bloomberg filename.
