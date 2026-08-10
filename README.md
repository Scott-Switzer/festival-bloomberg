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

## DuckDB warehouse and ingestion

`schema/duckdb.sql` is the canonical schema used by both
`src/scraper/db.ts` and `python/festival_bloomberg/duckdb_warehouse.py`.
Initialization is safe on a fresh or existing database: it creates tables and
indexes if absent, adds ingestion columns to the legacy `observations` table,
backfills first/last-seen fields, and records schema version 1. Legacy rows keep
a null `dedup_key`, so existing ID-based behavior is unchanged; canonical
reingestion creates a new deterministic row rather than rewriting history.
DuckDB cannot safely add the fresh table's `CHECK`/`NOT NULL` constraints in
place, so upgraded legacy tables rely on the validated write APIs while fresh
tables receive the full constraints.

The schema contains:

- domain tables: `observations`, `lineups`, and `sentiment_scores`;
- operational tables: `costs` and `telemetry`;
- audit tables: `ingestion_runs` and `ingestion_logs`;
- `schema_migrations`, plus uniqueness/indexes for deterministic deduplication,
  source idempotency, festival lookup, URLs, content hashes, and audit lookup.

The TypeScript public surface is exported from `src/scraper/index.ts`:

- `IngestionSourceAdapter<Input>` maps a future source format to one
  source-neutral `IngestionRecord`. Adapters provide stable `source`,
  `version`, and `sourceRecordId` values.
- `IngestionPipeline` validates and canonicalizes adapter output and writes
  through `DuckDbAdapter.ingestion`.
- `createObservationIngestionAdapter()` adapts existing scraper observations.
- `normalizeText()`, `canonicalizeUrl()`, `canonicalJson()`, and `stableHash()`
  expose the versioned normalization primitives.

```ts
const warehouse = await createDuckDbWarehouse();
const pipeline = new IngestionPipeline(warehouse.ingestion);
const result = await pipeline.ingest(adapter, sourceRecords, {
  idempotencyKey: "provider-snapshot-2026-01-15",
  metadata: { requestedBy: "nightly-job" },
});
await warehouse.close();
```

An idempotency key is scoped to the adapter source. Reusing it with the same
normalized input and adapter version resumes failed records or returns the
completed run without writes. Reusing it with changed input/version raises
`IdempotencyConflictError`. Every item has a durable status, metadata, canonical
URL/content hash, observation link, and explicit error code/message fields.

### Normalization and deduplication policy

- Text uses Unicode NFKC, removes zero-width space/BOM, and collapses an
  explicit Unicode whitespace set. Case, punctuation, HTML, and markup are
  preserved.
- JSON payload keys are recursively sorted and strings normalized. Non-finite
  numbers and non-JSON values are rejected rather than coerced.
- HTTP(S) URLs use WHATWG parsing, lowercase scheme/host, remove credentials,
  default ports, and fragments, sort retained query keys, and remove only
  `utm_*`, `dclid`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`, and `msclkid`.
  Paths and trailing slashes are preserved.
- The default dedup key scopes normalized content by observation kind,
  festival, edition, and canonical URL. Adapters may provide `subjectKey` to
  deliberately merge equivalent content across different URLs.
- Duplicate winners are independent of arrival order: earliest `observedAt`,
  then canonical URL, source, source record ID, and input hash. Evidence is
  canonicalized, merged by locator/content, keeps the earliest fetch, and is
  sorted deterministically.

Observation merge and its successful ingestion log are one DuckDB transaction.
The adapter serializes local ingestion writes and checkpoints on close. DuckDB
is still a local embedded store: do not run concurrent Node and Python writers
against the same file.

The end-to-end fixture at
`tests/fixtures/ingestion/coachella-2026.json` exercises fresh/repeated
initialization, transient resume, logs/errors/metadata, text and URL
normalization, duplicate merging, changed content, successful replay, and
idempotency-key conflicts.
