-- 047_data_estate_scale_25k_v1.sql
-- DATA_ESTATE_SCALE_25K_V1 — immutable bulk-source acquisition manifest.
--
-- Every bulk artifact (MusicBrainz / ListenBrainz / Wikimedia dumps, TM
-- estate exports) is recorded here BEFORE normalization: source, version,
-- bytes, checksum, license, rights state, raw R2 key, and the normalized
-- dataset(s) produced from it. Nothing is downloaded without a recorded
-- product-use justification; nothing is retained without lineage.

CREATE TABLE IF NOT EXISTS security.bulk_source_manifest (
    source_manifest_key   VARCHAR PRIMARY KEY,   -- hash(source, source_version, source_url)
    source                VARCHAR NOT NULL,      -- musicbrainz_event_dump | musicbrainz_place_dump | musicbrainz_series_dump | musicbrainz_artist_dump | musicbrainz_release_group_dump | listenbrainz_bulk_popularity | wikimedia_pageview_complete | ticketmaster_estate_export
    source_version        VARCHAR NOT NULL,      -- snapshot date / dump id
    source_url            VARCHAR,
    retrieved_at          TIMESTAMP,
    compressed_bytes      BIGINT,
    sha256                VARCHAR,
    license               VARCHAR,               -- CC0 | API-terms | ...
    rights_status         VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    product_use_justification VARCHAR,
    raw_r2_key            VARCHAR,               -- immutable source artifact in raw bulk R2
    normalized_dataset    VARCHAR,               -- lake/... partition or manifest key
    row_count             BIGINT,
    date_min              DATE,
    date_max              DATE,
    ingested_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, source_version, source_url)
);
