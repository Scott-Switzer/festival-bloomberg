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

## Data Flywheel & Coverage V1

After `BASELINE_RESEARCH_V1` (verdict `COMPS_SIGNAL_ONLY`), the priority is
data scale + depth + customer utility, not more models. The flywheel layer
(`python/festival_bloomberg/flywheel/`, schema migration 017) runs four
pipelines over the warehouse:

- `EVENT_GRAPH` — MusicBrainz identity backbone (CC0) + source registry
- `OUTCOME_HUNTER` — claims-based outcome acquisition (plans/tasks writing
  into `economics.event_outcome_claims`)
- `CONTEXT_PANEL` — attention / market / weather series with PIT vintages
- `FORWARD_WATCH` — time-sensitive capture of future events

Coverage is measured against medium-term objectives
(`flywheel.objectives` / `flywheel.coverage_snapshots`) on every run — the
acquisition metric is decision coverage, not row counts. Live driver:

```bash
PYTHONPATH=python python3 -m festival_bloomberg.oa.flywheel_v1
```

See `docs/data-flywheel-and-coverage-v1.md`.

## Data Acquisition Activation V1

Turns the flywheel from measurement into an OPERATING acquisition system
(migrations 018 + 019). Success is measured by new decision-useful evidence,
not schema or plan counts:

- **OUTCOME_HUNTER execution** — real Common Crawl CDX hunts (key-free, $0)
  on the persisted source documents, era-directed across crawl collections;
  append-only attempt ledger with classified failures
  (`flywheel.outcome_hunt_attempts`); explicit P0/P1/P2 priority queue
- **PIT reconstruction** — `flywheel.pit_reconstruction_evidence` with
  OBSERVED_EXACT/DAY/MONTH, ARCHIVE_CAPTURE_UPPER_BOUND, SOURCE_PERIOD_BOUND,
  ESTIMATED_RESEARCH_ONLY, UNKNOWN; STRICT_PIT / CONSERVATIVE_BOUND_PIT /
  RESEARCH_ESTIMATED modes; unknown is never zero
- **FORWARD_WATCH activation** — MusicBrainz CC0 future events + real future
  events/snapshots already persisted by the recurring collector, migrated
  into `flywheel.forward_watch_events/_observations`
- **Acquisition economics** — per-provider runs + derived yield metrics
  (`flywheel.provider_acquisition_runs/_metrics`) with HTTP-level and
  task-level units kept strictly separate (migration 019): task counts are
  never used as HTTP response counts, and request counts are measured
  telemetry, never row-count estimates

Live driver:

```bash
PYTHONPATH=python python3 -m festival_bloomberg.oa.activation_v1
```

See `docs/data-acquisition-activation-v1.md`.

### PRE_EVENT_CUTOFF_ACQUISITION_V1

Answers the binding question: what was actually knowable BEFORE a promoter
decided to book the show. Adds the decision-time cutoff taxonomy
(`flywheel.pre_event_cutoff_evidence`, migration 020) — BOOKING_OR_OFFER /
ANNOUNCEMENT / PRESALE / GENERAL_ONSALE / TICKET_PRICE_OBSERVATION /
EVENT_DATE / RESULT_PUBLICATION / SETTLEMENT, never collapsed — plus
warm-start-by-cutoff measurement. A public announcement date is NEVER a
booking date (at most a BOUND), interval evidence is never collapsed to a
midpoint, and UNKNOWN is reported, never zeroed.

Live driver:

```bash
PYTHONPATH=python python3 -m festival_bloomberg.oa.pre_event_cutoffs
```

See `docs/pre-event-cutoff-acquisition-v1.md`.

### HISTORICAL_DECISION_EVIDENCE_ENGINE_V1

An autonomous evidence-research engine: a warm-start dependency graph that
ranks which missing historical cutoff unlocks the most downstream
PIT-comparable targets (value-of-information acquisition), an immutable
content-addressed document store, a claim support graph with evidence spans,
deterministic JSON-LD/OpenGraph/date-language extractors, a deterministic
admissibility verifier, and a strict DeepSeek V4 Pro candidate-claim contract.

> An LLM never decides truth — it proposes candidate claims; deterministic
> code decides admissibility.

Live driver:

```bash
PYTHONPATH=python python3 -m festival_bloomberg.oa.historical_decision_evidence
```

See `docs/historical-decision-evidence-engine-v1.md`. The next research
milestone is `COMPARABLE_EVENT_ENGINE_V1` — only once announcement/onsale/
booking-bound histories exist for a meaningful subset of events.

## Intelligence Terminal MVP V1

The first information product. A read-only, source-backed terminal over the
canonical warehouse (migration 022): search across artists/events/venues/
markets/festivals, an append-only activity tape of "what changed", entity
pages with boxoffice history and forward events, a DATA page that shows
provider rights/commercial status rather than hiding them, and a grounded
read-only ASK layer whose answers cite underlying evidence.

```bash
PYTHONPATH=python python3 -m festival_bloomberg.oa.intelligence_terminal
PYTHONPATH=python python3 -m festival_bloomberg.terminal.server --port 8931
# open http://127.0.0.1:8931
```

See `docs/intelligence-terminal-mvp-v1.md`.
