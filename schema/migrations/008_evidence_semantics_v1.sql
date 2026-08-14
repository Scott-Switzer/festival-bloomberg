-- ===========================================================================
-- 008_evidence_semantics_v1.sql
-- ===========================================================================
-- Finance-grade evidence semantics for the Signal Fabric.
--
-- 1. content_role separates FAN_GENERATED discourse from encyclopedic,
--    editorial and promotional text so fan-sentiment aggregation can never
--    be contaminated by Wikipedia prose, press releases or venue copy.
-- 2. resolution_method records HOW a canonical object was identified
--    (exact id / url / fuzzy / manual) instead of a decorative confidence.
-- 3. source_revision_id / source_revision_time record immutable revision
--    identity for sources that expose it, so retrospective knowledge_time
--    may only be backdated when the exact version identity is proven.
-- 4. correlation_id scopes observations to an acquisition execution (an OA
--    run) so replay/counting can never be contaminated by unrelated history.
--
-- UNKNOWN / NULL always fail closed: absence of a role is not an inference.
-- ===========================================================================

ALTER TABLE acquisition.raw_observations ADD COLUMN correlation_id VARCHAR;
ALTER TABLE acquisition.raw_observations ADD COLUMN source_revision_id VARCHAR;
ALTER TABLE acquisition.raw_observations ADD COLUMN source_revision_time TIMESTAMP;

ALTER TABLE acquisition.social_observations ADD COLUMN content_role VARCHAR;
ALTER TABLE acquisition.social_observations ADD COLUMN content_role_method VARCHAR;
ALTER TABLE acquisition.social_observations ADD COLUMN resolution_method VARCHAR;
ALTER TABLE acquisition.social_observations ADD COLUMN resolution_evidence VARCHAR;

CREATE INDEX IF NOT EXISTS idx_raw_obs_correlation
  ON acquisition.raw_observations (correlation_id);
