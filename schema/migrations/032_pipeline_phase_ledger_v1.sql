-- ---------------------------------------------------------------------------
-- 032: PIPELINE PHASE LEDGER (durable resumability)
--
-- Every milestone phase records its execution here so expensive source
-- streams are never re-run merely to reconstruct a report. The rule:
--
--   same input fingerprint
--   + same source snapshot
--   + compatible software version
--   + status = COMPLETE
--   => SKIP the expensive phase
--
-- Final reports are derived from the warehouse + this ledger, never by
-- replaying archive ingestion.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit.pipeline_phase_runs (
    run_id              VARCHAR NOT NULL,     -- milestone run id (e.g. oa start ts)
    milestone           VARCHAR NOT NULL,
    phase               VARCHAR NOT NULL,
    source_snapshot     VARCHAR,
    software_version    VARCHAR,
    input_fingerprint   VARCHAR,
    started_at          TIMESTAMP NOT NULL DEFAULT now(),
    completed_at        TIMESTAMP,
    status              VARCHAR NOT NULL DEFAULT 'RUNNING',  -- RUNNING|COMPLETE|ERROR|SKIPPED
    rows_read           BIGINT NOT NULL DEFAULT 0,
    rows_written        BIGINT NOT NULL DEFAULT 0,
    duration_seconds    DOUBLE,
    checkpoint          VARCHAR,
    error_code          VARCHAR,
    error_message       VARCHAR,
    PRIMARY KEY (run_id, milestone, phase)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_phase_status
  ON audit.pipeline_phase_runs (milestone, phase, status);
CREATE INDEX IF NOT EXISTS idx_pipeline_phase_fingerprint
  ON audit.pipeline_phase_runs (input_fingerprint, software_version, status);
