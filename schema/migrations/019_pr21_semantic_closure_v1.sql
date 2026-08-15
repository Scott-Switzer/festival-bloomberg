-- ===========================================================================
-- 019_pr21_semantic_closure_v1.sql
-- ===========================================================================
-- PR #21 semantic closure: acquisition accounting must NEVER mix units.
--
-- Migration 018 stored one ambiguous set of counters. A CDX run could report
-- ``requests = 6`` (HTTP interactions) while ``successful_responses`` was
-- computed from TASK outcomes (CLAIM_FOUND + NOT_FOUND = 22+), yielding an
-- impossible "successful responses per 1,000 requests" > 1,000.
--
-- This migration separates the two units explicitly:
--
--   HTTP level (one row per provider interaction):
--       http_requests                HTTP interactions attempted
--       http_successful_responses    HTTP 2xx/valid responses
--       http_rate_limited            HTTP 429 responses
--       http_failures                HTTP 5xx / network / timeout failures
--
--   TASK level (one row per hunt task attempt):
--       tasks_attempted              tasks attempted
--       tasks_claim_found            tasks with a found claim
--       tasks_not_found              tasks whose retrieval succeeded with no
--                                    qualifying evidence
--
-- The migration-018 columns are KEPT and re-documented: ``requests`` mirrors
-- ``http_requests`` (nullable -> UNKNOWN when not measured), and
-- ``successful_responses`` mirrors ``http_successful_responses``. The 018
-- failure columns (``not_found``, ``rate_limited``, ``http_failed``,
-- ``parser_failed``, ``rights_blocked``, ``auth_failed``, ``other_failure``)
-- remain TASK-level counters keyed by hunt attempt status. Task counts are
-- NEVER used as HTTP response counts; HTTP counts are NEVER used as task
-- outcomes.
--
-- Derived yield metrics gain explicit denominators:
--       http_success_rate               http_successful_responses / http_requests
--       claims_per_1000_http_requests   new_claims / http_requests * 1000
--       claims_per_1000_tasks_attempted new_claims / tasks_attempted * 1000
--       new_events_per_1000_http_requests
--
-- ``request_count_status`` records MEASURED vs UNKNOWN. A request count is
-- NEVER estimated from returned row counts; when it cannot be measured it is
-- stored NULL with request_count_status = 'UNKNOWN'.-- ===========================================================================
-- DuckDB refuses to ALTER a table that has dependent indexes, so the index on
-- each altered table is dropped first and recreated afterwards (identical to
-- the migration-018 definitions).

DROP INDEX IF EXISTS flywheel.idx_acq_runs_provider;
DROP INDEX IF EXISTS flywheel.idx_acq_metrics_run;

-- Make ``requests`` nullable so "not measured" is stored as NULL, never 0.
ALTER TABLE flywheel.provider_acquisition_runs
  ALTER COLUMN requests DROP NOT NULL;

-- HTTP-level counters (nullable; the row builder always writes a value,
-- NULL only ever appears when a legacy row predates this migration).
ALTER TABLE flywheel.provider_acquisition_runs
  ADD COLUMN IF NOT EXISTS http_requests INTEGER;
ALTER TABLE flywheel.provider_acquisition_runs
  ADD COLUMN IF NOT EXISTS http_successful_responses INTEGER;
ALTER TABLE flywheel.provider_acquisition_runs
  ADD COLUMN IF NOT EXISTS http_rate_limited INTEGER;
ALTER TABLE flywheel.provider_acquisition_runs
  ADD COLUMN IF NOT EXISTS http_failures INTEGER;

-- Task-level counters.
ALTER TABLE flywheel.provider_acquisition_runs
  ADD COLUMN IF NOT EXISTS tasks_attempted INTEGER;
ALTER TABLE flywheel.provider_acquisition_runs
  ADD COLUMN IF NOT EXISTS tasks_claim_found INTEGER;
ALTER TABLE flywheel.provider_acquisition_runs
  ADD COLUMN IF NOT EXISTS tasks_not_found INTEGER;

-- MEASURED | UNKNOWN — request counts are never inferred from row counts.
ALTER TABLE flywheel.provider_acquisition_runs
  ADD COLUMN IF NOT EXISTS request_count_status VARCHAR;

-- Explicit-denominator derived metrics.
ALTER TABLE flywheel.provider_acquisition_metrics
  ADD COLUMN IF NOT EXISTS http_success_rate DOUBLE;
ALTER TABLE flywheel.provider_acquisition_metrics
  ADD COLUMN IF NOT EXISTS claims_per_1000_http_requests DOUBLE;
ALTER TABLE flywheel.provider_acquisition_metrics
  ADD COLUMN IF NOT EXISTS claims_per_1000_tasks_attempted DOUBLE;
ALTER TABLE flywheel.provider_acquisition_metrics
  ADD COLUMN IF NOT EXISTS new_events_per_1000_http_requests DOUBLE;

CREATE INDEX IF NOT EXISTS idx_acq_runs_provider
  ON flywheel.provider_acquisition_runs (provider, started_at);
CREATE INDEX IF NOT EXISTS idx_acq_metrics_run
  ON flywheel.provider_acquisition_metrics (run_id);
