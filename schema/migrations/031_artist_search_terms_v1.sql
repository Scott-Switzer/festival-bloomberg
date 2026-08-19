-- ---------------------------------------------------------------------------
-- 031: ARTIST SEARCH TERM INDEX (deterministic + FTS candidate retrieval)
--
-- Materializes one row per (artist, term) so interactive search never scans
-- the 2.2M-row reference JSON aliases per query:
--
--   reference.artist_search_terms
--       artist_mbid
--       term
--       normalized_term
--       term_type            CANONICAL_NAME | SORT_NAME | ALIAS
--       normalization_version
--
-- Search hierarchy stays: exact external ID -> exact canonical -> exact
-- alias -> normalized exact -> FTS candidate retrieval. FTS/fuzzy similarity
-- NEVER defines canonical identity; it only retrieves candidates.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reference.artist_search_terms (
    artist_mbid             VARCHAR NOT NULL,
    term                    VARCHAR NOT NULL,
    normalized_term         VARCHAR NOT NULL,
    term_type               VARCHAR NOT NULL,  -- CANONICAL_NAME|SORT_NAME|ALIAS
    normalization_version   VARCHAR NOT NULL DEFAULT 'artist_name_norm_v2'
);
CREATE INDEX IF NOT EXISTS idx_artist_search_terms_mbid
  ON reference.artist_search_terms (artist_mbid);
CREATE INDEX IF NOT EXISTS idx_artist_search_terms_norm
  ON reference.artist_search_terms (normalized_term);
CREATE INDEX IF NOT EXISTS idx_artist_search_terms_type
  ON reference.artist_search_terms (term_type, normalized_term);

-- Exact canonical-name lookup is on the hot path of every search.
CREATE INDEX IF NOT EXISTS idx_artists_lower_name
  ON core.artists (lower(name));
