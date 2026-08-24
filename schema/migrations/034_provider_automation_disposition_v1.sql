-- Provider automation is independent from access, rights, and commercial use.
ALTER TABLE flywheel.source_registry
  ADD COLUMN IF NOT EXISTS automation_status VARCHAR;

UPDATE flywheel.source_registry
SET automation_status = 'AUTOMATION_ENABLED'
WHERE automation_status IS NULL;

UPDATE flywheel.source_registry
SET automation_status = 'AUTOMATION_DISABLED',
    updated_at = CURRENT_TIMESTAMP
WHERE provider = 'seatgeek' OR source_id = 'seatgeek';
