# PR #10 review map

PR #10 (`refactor: canonical PIT foundation and Signal Fabric evidence layer`)
is a large, multi-concern PR because it consolidates the canonical foundation
and removes the legacy duplicate implementation tree in the same change. This
map breaks the diff into reviewable sections.

## A. Legacy duplicate deletion

- **Files:** the entire `intelligence/` subtree and root `warehouse/` (deleted by
  the foundation branch's first commit, kept in history).
- **Reason:** they were duplicate warehouses / template scrapers / heuristic
  models; the canonical backend is `python/festival_bloomberg/`.
- **Risk:** low at runtime (nothing imports them), but high for review noise.
- **Reviewer attention:** skim only — confirm nothing in the canonical path
  still references the deleted tree.

## B. Canonical PIT schema / repository

- **Files:** `schema/duckdb.sql`, `schema/migrations/005_*.sql`,
  `schema/migrations/006_*.sql`, `python/festival_bloomberg/warehouse/repository.py`,
  `python/festival_bloomberg/warehouse/duckdb_manager.py`.
- **Reason:** one warehouse, one schema, one migration system; PIT temporal
  fields on the canonical tables; the ticket-spread `TicketRepository` port.
- **Risk:** medium — migration compatibility with current `main`.
- **Reviewer attention:** verify `insert_artist_metric` retains history across
  knowledge times (metric key includes `knowledge_time`), and that migration 006
  upgrades a current-main database without losing rows.

## C. Signal Fabric acquisition

- **Files:** `python/festival_bloomberg/acquisition/{contracts,base,transport,router,costs,health,policy}.py`,
  `python/festival_bloomberg/acquisition/providers/{http,monid,apify,youtube,scrapling,wikimedia}.py`.
- **Reason:** one canonical acquisition interface; fail-closed policy gate;
  mechanism-aware licensing; cost tracking; provider registry.
- **Risk:** medium — provider statuses must fail closed (never placeholder
  success).
- **Reviewer attention:** confirm the router never returns fabricated success,
  and that `wikimedia` is the only provider usable with no credentials.

## D. Evidence model

- **Files:** `schema/migrations/007_*.sql`, `schema/migrations/008_*.sql`,
  `python/festival_bloomberg/evidence/{repository,dedup,provenance,semantics}.py`.
- **Reason:** immutable raw observations, canonical dedup, timestamped
  engagement, versioned text inferences, content-role taxonomy, resolution
  method, revision identity, correlation scoping.
- **Risk:** medium — semantic correctness (content role, knowledge time).
- **Reviewer attention:** verify knowledge time is backdated only when
  `knowledge_time_source == "source_revision"` (immutable revision proven),
  else falls back to retrieval time.

## E. Social / NLP

- **Files:** `python/festival_bloomberg/social/{normalize,sentiment,intent,features}.py`,
  `python/festival_bloomberg/vader_sentiment.py`.
- **Reason:** VADER baseline, optional TweetNLP, experimental intent heuristics
  (explicitly `EXPERIMENTAL_HEURISTIC_NOT_VALIDATED`), PIT-safe features.
- **Risk:** medium — fan sentiment must only use `FAN_GENERATED` /
  `FORUM_DISCUSSION` roles.
- **Reviewer attention:** confirm `fan_sentiment_distribution` fails closed to
  `UNKNOWN` when no fan evidence exists.

## F. Operational acceptance

- **Files:** `python/festival_bloomberg/oa/operational_acceptance.py`,
  `python/festival_bloomberg/labels.py`, `python/festival_bloomberg/cli/main.py`.
- **Reason:** live OA driver (router → provider → evidence → PIT → NLP), honest
  separated statuses, deterministic fan-text labeling export.
- **Risk:** low — live-only, never runs in CI.
- **Reviewer attention:** confirm OA never invents locality, confidence, or
  sentiment; statuses are reported independently (never upgraded to look better).

## G. CI / security

- **Files:** `.github/workflows/ci.yml`, `.gitignore`, `SECURITY.md`.
- **Reason:** Node + Python + gitleaks jobs; historical Hetzner credential is
  documented as `ROTATION_REQUIRED` (value never reproduced).
- **Risk:** medium — secret exposure is a real finding; rotation is required.
- **Reviewer attention:** confirm no credential value appears anywhere in this
  PR, and that the security job scans the working tree (not just tracked files).

## H. Tests

- **Files:** `tests/python/test_*.py`, `tests/scraper/migrations.test.ts`.
- **Reason:** offline regression coverage for PIT, ticket spread, acquisition,
  evidence, dedup, sentiment, source policy, prompt-injection boundary, CLI,
  and OA semantics (scoped PIT replay, content roles, market vs demand).
- **Risk:** low.
- **Reviewer attention:** confirm the OA contamination and fan-sentiment
  fail-closed tests are present and meaningful.
