-- ===========================================================================
-- 009_youtube_fan_signal_v1.sql
-- ===========================================================================
-- Source-object market context, search cohort, and source-updated timestamps
-- for live YouTube fan-signal observations.
--
-- market_context_method records HOW a source object was tied to a market
-- (EXPLICIT_SOURCE_TEXT / TRUSTED_EVENT_RELATION / PUBLIC_GEOTAG / UNKNOWN).
-- Search query membership is never stored as market evidence.
-- commenter_location is independent and remains NULL unless explicit public
-- evidence supports it.
-- ===========================================================================

ALTER TABLE acquisition.social_observations ADD COLUMN IF NOT EXISTS search_cohort VARCHAR;
ALTER TABLE acquisition.social_observations ADD COLUMN IF NOT EXISTS market_context_method VARCHAR;
ALTER TABLE acquisition.social_observations ADD COLUMN IF NOT EXISTS commenter_location VARCHAR;
ALTER TABLE acquisition.social_observations ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMP;

ALTER TABLE acquisition.raw_observations ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMP;
