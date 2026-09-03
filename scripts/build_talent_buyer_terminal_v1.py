#!/usr/bin/env python3
"""Build the compact Talent Buyer Terminal V1 serving database.

This is a materializer, not an acquisition job.  It reads the existing
25,000-artist estate report and an immutable terminal snapshot.  Audience
affinity is optional and must be supplied as a local Parquet file; this script
never downloads the R2 object or opens the canonical research database.

The output is a small, source-neutral DuckDB file intended for browser-time
queries.  Source-specific semantics are retained in ``source_system``,
``source_scope``, ``knowledge_time`` and ``status`` columns.  NULL is used for
unknown values throughout (in particular, a missing/zero ticket price is not
turned into a sold-out or zero-price claim).

Example (after explicitly placing the already-produced affinity object
locally)::

    PYTHONPATH=python .venv/bin/python scripts/build_talent_buyer_terminal_v1.py \
      --affinity-parquet /tmp/lb_gold_pilot.parquet

No command in this module is run as part of import.  The caller must invoke
``main`` explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "artist_security_25000_estate_v1.json"
DEFAULT_SERVING_DIR = PROJECT_ROOT / "data" / "serving"
DEFAULT_OUTPUT = PROJECT_ROOT / "serving" / "artist_security_terminal_v1" / "CURRENT.duckdb"


def _current_snapshot() -> Path:
    pointer = DEFAULT_SERVING_DIR / "CURRENT.json"
    if not pointer.exists():
        raise FileNotFoundError(f"serving pointer not found: {pointer}")
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    name = payload.get("snapshot_file") or f"{payload.get('snapshot_id', '')}.duckdb"
    path = DEFAULT_SERVING_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"serving snapshot named by CURRENT.json is missing: {path}")
    return path


def _read_estate(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    artists = payload.get("artists")
    if not isinstance(artists, list) or not artists:
        raise ValueError(f"estate report has no artists: {path}")
    keys = [a.get("key") for a in artists]
    if any(not isinstance(k, str) or not k for k in keys):
        raise ValueError("estate report contains an artist without a key")
    if len(set(keys)) != len(keys):
        raise ValueError("estate report contains duplicate artist keys")
    return payload, artists


def _q(path: Path) -> str:
    """Quote a local path for DuckDB SQL after it has been resolved."""
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE product_meta (
            product_id VARCHAR PRIMARY KEY,
            product_version VARCHAR NOT NULL,
            built_at TIMESTAMP NOT NULL,
            source_serving_snapshot VARCHAR NOT NULL,
            source_estate_report VARCHAR NOT NULL,
            source_affinity_path VARCHAR,
            artist_count INTEGER NOT NULL,
            market_count INTEGER NOT NULL,
            peer_count INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            festival_count INTEGER NOT NULL,
            future_event_count INTEGER NOT NULL,
            validation_status VARCHAR NOT NULL,
            validation_json JSON NOT NULL,
            data_boundary VARCHAR NOT NULL
        );

        CREATE TABLE artists (
            artist_key VARCHAR PRIMARY KEY,
            name VARCHAR,
            normalized_name VARCHAR,
            musicbrainz_id VARCHAR,
            tier VARCHAR NOT NULL,
            selection_bucket VARCHAR,
            selection_reason VARCHAR,
            evidence_profile VARCHAR NOT NULL,
            evidence_family_count INTEGER NOT NULL,
            market_count INTEGER NOT NULL,
            historical_event_count INTEGER NOT NULL,
            festival_appearance_count INTEGER NOT NULL,
            venues_played INTEGER,
            listenbrainz_total_listens DOUBLE,
            listenbrainz_total_users DOUBLE,
            youtube_identifiers JSON,
            disambiguation VARCHAR,
            aliases JSON,
            country VARCHAR,
            origin_city VARCHAR,
            origin_region VARCHAR,
            area VARCHAR,
            artist_type VARCHAR,
            primary_genre VARCHAR,
            life_span_begin VARCHAR,
            life_span_end VARCHAR,
            is_active BOOLEAN,
            source_system VARCHAR NOT NULL,
            source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP,
            status VARCHAR NOT NULL,
            rights_status VARCHAR,
            evidence_url VARCHAR
        );

        CREATE TABLE artist_search_terms (
            search_term_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            term VARCHAR NOT NULL,
            normalized_term VARCHAR,
            term_type VARCHAR,
            source_system VARCHAR NOT NULL,
            source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP,
            status VARCHAR NOT NULL
        );

        CREATE TABLE artist_external_ids (
            external_id_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            id_type VARCHAR NOT NULL,
            id_value VARCHAR,
            url VARCHAR,
            source_system VARCHAR,
            source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP,
            status VARCHAR NOT NULL,
            resolution_method VARCHAR,
            confidence DOUBLE
        );

        CREATE TABLE attention_observations (
            observation_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            source_system VARCHAR NOT NULL,
            metric_kind VARCHAR NOT NULL,
            period_start DATE,
            period_end DATE,
            value DOUBLE,
            value_sum DOUBLE,
            value_unit VARCHAR,
            status VARCHAR NOT NULL,
            source_url VARCHAR,
            retrieved_at TIMESTAMP,
            knowledge_time TIMESTAMP,
            source_scope VARCHAR NOT NULL,
            rights_status VARCHAR
        );

        CREATE TABLE artist_peers (
            edge_key VARCHAR PRIMARY KEY,
            subject_key VARCHAR NOT NULL,
            peer_key VARCHAR NOT NULL,
            peer_name VARCHAR,
            rank INTEGER NOT NULL,
            shared_listeners BIGINT,
            jaccard DOUBLE,
            cosine DOUBLE,
            source_system VARCHAR NOT NULL,
            source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP,
            status VARCHAR NOT NULL,
            explanation VARCHAR NOT NULL
        );

        CREATE TABLE artist_markets (
            row_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            market_key VARCHAR NOT NULL,
            observed_shows INTEGER,
            venue_count INTEGER,
            first_play_date DATE,
            last_play_date DATE,
            future_events INTEGER,
            ticket_evidence_count INTEGER,
            source_system VARCHAR NOT NULL,
            source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP,
            status VARCHAR NOT NULL,
            explanation VARCHAR NOT NULL
        );

        CREATE TABLE event_history (
            event_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            artist_name VARCHAR,
            event_name VARCHAR,
            event_date DATE,
            event_end_date DATE,
            event_type VARCHAR,
            venue_name VARCHAR,
            market_name VARCHAR,
            city VARCHAR,
            state_code VARCHAR,
            number_of_shows INTEGER,
            is_multi_show BOOLEAN,
            source_system VARCHAR NOT NULL,
            source_url VARCHAR,
            source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP,
            status VARCHAR NOT NULL,
            location_method VARCHAR
        );

        CREATE TABLE festival_appearances (
            appearance_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            event_key VARCHAR NOT NULL,
            festival_key VARCHAR,
            edition_key VARCHAR,
            festival_name VARCHAR,
            event_name VARCHAR,
            edition_year INTEGER,
            event_date DATE,
            performance_date DATE,
            market_name VARCHAR,
            venue_name VARCHAR,
            billing_order INTEGER,
            billing_tier VARCHAR,
            stage_name VARCHAR,
            artist_role VARCHAR,
            co_billed_artist_names VARCHAR,
            repeat_appearance_count INTEGER,
            source_system VARCHAR NOT NULL,
            source_url VARCHAR,
            source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP,
            status VARCHAR NOT NULL
        );

        CREATE TABLE future_events (
            future_event_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            provider_event_id VARCHAR,
            event_name VARCHAR,
            event_date DATE,
            event_time TIMESTAMP,
            event_status VARCHAR,
            venue_name VARCHAR,
            market_name VARCHAR,
            city VARCHAR,
            state_code VARCHAR,
            promoter VARCHAR,
            ticket_price_min DOUBLE,
            ticket_price_max DOUBLE,
            ticket_price_currency VARCHAR,
            ticket_price_basis VARCHAR,
            ticket_evidence_status VARCHAR NOT NULL,
            source_system VARCHAR NOT NULL,
            source_url VARCHAR,
            source_scope VARCHAR NOT NULL,
            retrieved_at TIMESTAMP,
            knowledge_time TIMESTAMP,
            status VARCHAR NOT NULL,
            rights_status VARCHAR
        );

        CREATE TABLE artist_factor_observations (
            factor_observation_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            factor_family VARCHAR NOT NULL,
            factor_name VARCHAR NOT NULL,
            platform VARCHAR,
            value DOUBLE,
            unit VARCHAR,
            observation_time TIMESTAMP,
            available_at TIMESTAMP,
            knowledge_time TIMESTAMP,
            retrieved_at TIMESTAMP,
            period_start DATE,
            period_end DATE,
            source VARCHAR,
            evidence_ref VARCHAR,
            source_scope VARCHAR,
            rights_status VARCHAR,
            commercial_use_status VARCHAR,
            quality_status VARCHAR,
            generation VARCHAR,
            evidence_json JSON
        );

        CREATE TABLE artist_sentiment_observations (
            observation_key VARCHAR PRIMARY KEY,
            artist_key VARCHAR NOT NULL,
            platform VARCHAR NOT NULL,
            "date" DATE NOT NULL,
            mention_count BIGINT NOT NULL,
            analyzed_count BIGINT NOT NULL,
            positive_share DOUBLE,
            neutral_share DOUBLE,
            negative_share DOUBLE,
            sentiment_mean DOUBLE,
            engagement_weighted_sentiment DOUBLE,
            engagement_total BIGINT,
            topic_distribution JSON,
            language_distribution JSON,
            sample_quality VARCHAR NOT NULL,
            source_generation VARCHAR NOT NULL,
            model_name VARCHAR NOT NULL,
            model_version VARCHAR NOT NULL,
            deduplicated_count BIGINT,
            spam_filtered_count BIGINT,
            source VARCHAR NOT NULL,
            evidence_ref VARCHAR,
            source_scope VARCHAR NOT NULL,
            rights_status VARCHAR NOT NULL,
            commercial_use_status VARCHAR NOT NULL,
            quality_status VARCHAR NOT NULL,
            retrieved_at TIMESTAMP NOT NULL,
            knowledge_time TIMESTAMP
        );
        """
    )


def _source_table_exists(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    """Return whether an attached immutable source table exists.

    Older serving snapshots predate the artist intelligence migration, so the
    materializer must branch on schema presence rather than fail the whole
    build when an optional source product is absent.
    """
    try:
        return bool(conn.execute(
            """
            SELECT COUNT(*)
            FROM duckdb_tables()
            WHERE database_name = 'src' AND schema_name = ? AND table_name = ?
            """,
            [schema, table],
        ).fetchone()[0])
    except Exception:
        return bool(conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_catalog = 'src' AND table_schema = ? AND table_name = ?
            """,
            [schema, table],
        ).fetchone()[0])


def _source_columns(conn: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {
        row[0] for row in conn.execute(
            f"DESCRIBE SELECT * FROM {table}"
        ).fetchall()
    }


def _source_expr(columns: set[str], alias: str, *names: str, default: str = "NULL") -> str:
    for name in names:
        if name in columns:
            return f"{alias}.{name}"
    return default


def _materialize_artist_intelligence(conn: duckdb.DuckDBPyConnection) -> None:
    """Copy optional artist factor/sentiment products into the compact DB.

    The source snapshot is immutable and the browser reads only these compact
    projections. Dynamic column expressions keep artifacts from migrations
    043-048 readable while using the explicit 049 temporal aliases when they
    exist.
    """
    factor_table = "src.metrics.artist_factor_observations"
    if _source_table_exists(conn, "metrics", "artist_factor_observations"):
        columns = _source_columns(conn, factor_table)
        value = _source_expr(columns, "o", "value")
        observation_time = _source_expr(columns, "o", "observation_time")
        as_of = _source_expr(columns, "o", "as_of")
        retrieved_at = _source_expr(columns, "o", "retrieved_at")
        conn.execute(
            f"""
            INSERT INTO artist_factor_observations (
                factor_observation_key, artist_key, factor_family, factor_name,
                platform, value, unit, observation_time, available_at, knowledge_time,
                retrieved_at, period_start, period_end, source, evidence_ref,
                source_scope, rights_status, commercial_use_status, quality_status,
                generation, evidence_json
            )
            SELECT
                {_source_expr(columns, 'o', 'factor_observation_key')},
                o.artist_key,
                o.factor_family,
                o.factor_name,
                {_source_expr(columns, 'o', 'platform', 'source_system')},
                {value},
                {_source_expr(columns, 'o', 'unit', 'value_unit')},
                COALESCE(TRY_CAST({observation_time} AS TIMESTAMP), TRY_CAST({as_of} AS TIMESTAMP)),
                TRY_CAST({_source_expr(columns, 'o', 'available_at')} AS TIMESTAMP),
                COALESCE(
                    TRY_CAST({_source_expr(columns, 'o', 'knowledge_time')} AS TIMESTAMP),
                    TRY_CAST({_source_expr(columns, 'o', 'available_at')} AS TIMESTAMP),
                    TRY_CAST({retrieved_at} AS TIMESTAMP)
                ),
                TRY_CAST({retrieved_at} AS TIMESTAMP),
                TRY_CAST({_source_expr(columns, 'o', 'period_start')} AS DATE),
                TRY_CAST({_source_expr(columns, 'o', 'period_end')} AS DATE),
                {_source_expr(columns, 'o', 'source', 'source_system')},
                {_source_expr(columns, 'o', 'evidence_ref', 'source_url')},
                COALESCE({_source_expr(columns, 'o', 'source_scope')}, 'LEGACY_ARTIST_SECURITY_FACTOR'),
                COALESCE({_source_expr(columns, 'o', 'rights_status')}, 'TERMS_REVIEW_REQUIRED'),
                COALESCE({_source_expr(columns, 'o', 'commercial_use_status')}, 'PROTOTYPE_ONLY'),
                COALESCE(
                    {_source_expr(columns, 'o', 'quality_status')},
                    CASE WHEN {value} IS NULL THEN 'UNKNOWN' ELSE 'OBSERVED' END
                ),
                COALESCE({_source_expr(columns, 'o', 'generation', 'source_version')}, 'LEGACY'),
                {_source_expr(columns, 'o', 'evidence_json')}
            FROM {factor_table} o
            JOIN selected_artists t ON t.artist_key = o.artist_key
            """
        )

    sentiment_table = "src.metrics.artist_sentiment_observations"
    if _source_table_exists(conn, "metrics", "artist_sentiment_observations"):
        columns = _source_columns(conn, sentiment_table)
        sentiment_columns = (
            "observation_key", "artist_key", "platform", "date", "mention_count",
            "analyzed_count", "positive_share", "neutral_share", "negative_share",
            "sentiment_mean", "engagement_weighted_sentiment", "engagement_total",
            "topic_distribution", "language_distribution", "sample_quality",
            "source_generation", "model_name", "model_version", "deduplicated_count",
            "spam_filtered_count", "source", "evidence_ref", "source_scope",
            "rights_status", "commercial_use_status", "quality_status", "retrieved_at",
            "knowledge_time",
        )
        expressions = {
            "observation_key": _source_expr(columns, "o", "observation_key"),
            "artist_key": "o.artist_key",
            "platform": "o.platform",
            "date": "o.\"date\"",
        }
        for column in sentiment_columns[4:]:
            expressions[column] = _source_expr(columns, "o", column)
        conn.execute(
            f"""
            INSERT INTO artist_sentiment_observations ({', '.join(sentiment_columns)})
            SELECT {', '.join(expressions[column] for column in sentiment_columns)}
            FROM {sentiment_table} o
            JOIN selected_artists t ON t.artist_key = o.artist_key
            """
        )


def _create_selected_table(conn: duckdb.DuckDBPyConnection, artists: list[dict[str, Any]]) -> None:
    conn.execute(
        """
        CREATE TEMP TABLE selected_artists (
            artist_key VARCHAR PRIMARY KEY,
            mbid VARCHAR,
            artist_name VARCHAR,
            tier VARCHAR,
            selection_bucket VARCHAR,
            selection_reason VARCHAR,
            evidence_profile VARCHAR,
            evidence_family_count INTEGER,
            market_count INTEGER,
            historical_event_count INTEGER,
            festival_appearance_count INTEGER,
            venues_played INTEGER,
            listenbrainz_total_listens DOUBLE,
            listenbrainz_total_users DOUBLE,
            youtube_identifiers JSON
        )
        """
    )
    rows = []
    for artist in artists:
        key = str(artist["key"])
        family_count = sum((
            1,  # governed MusicBrainz identity in the 25K universe
            bool(artist.get("markets")),
            bool(artist.get("event_performances")),
            bool(artist.get("festival_appearances")),
            bool(artist.get("listenbrainz")),
            bool(artist.get("youtube")),
        ))
        evidence_profile = (
            "sparse" if family_count <= 2 else
            "medium" if family_count <= 4 else
            "deep"
        )
        listenbrainz = artist.get("listenbrainz") or {}
        rows.append(
            (
                key,
                artist.get("mbid") or key.removeprefix("mbid::"),
                artist.get("name"),
                artist.get("tier") or "UNKNOWN",
                artist.get("selection_bucket"),
                artist.get("selection_reason"),
                evidence_profile,
                family_count,
                len(artist.get("markets") or []),
                int(artist.get("event_performances") or 0),
                int(artist.get("festival_appearances") or 0),
                artist.get("venues_played"),
                listenbrainz.get("LISTENBRAINZ_TOTAL_LISTEN_COUNT"),
                listenbrainz.get("LISTENBRAINZ_TOTAL_USER_COUNT"),
                json.dumps(artist.get("youtube") or []),
            )
        )
    conn.executemany(
        "INSERT INTO selected_artists VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _materialize_identity(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        INSERT INTO artists
        SELECT
            t.artist_key,
            COALESCE(s.name, t.artist_name),
            lower(trim(COALESCE(s.name, t.artist_name))),
            COALESCE(s.musicbrainz_id, t.mbid),
            t.tier,
            t.selection_bucket,
            t.selection_reason,
            t.evidence_profile,
            t.evidence_family_count,
            t.market_count,
            t.historical_event_count,
            t.festival_appearance_count,
            t.venues_played,
            t.listenbrainz_total_listens,
            t.listenbrainz_total_users,
            t.youtube_identifiers,
            s.disambiguation,
            s.aliases,
            s.country,
            s.origin_city,
            s.origin_region,
            s.area,
            s.type,
            s.primary_genre,
            s.life_span_begin,
            s.life_span_end,
            s.is_active,
            'musicbrainz',
            'PUBLIC_REFERENCE',
            COALESCE(s.source_retrieved_at, s.updated_at, s.ingested_at),
            CASE WHEN s.artist_key IS NULL THEN 'MISSING_IN_SERVING_SNAPSHOT' ELSE 'PRESENT' END,
            'PUBLIC_DOMAIN_DEDICATED',
            s.evidence_url
        FROM selected_artists t
        LEFT JOIN src.core.artists s ON s.artist_key = t.artist_key
        """
    )
    conn.execute(
        """
        INSERT INTO artist_search_terms
        SELECT sha256(artist_key || '|canonical_name|' || lower(trim(name))),
               artist_key, name, lower(trim(name)), 'canonical_name',
               'musicbrainz', 'PUBLIC_REFERENCE', knowledge_time, status
        FROM artists
        WHERE name IS NOT NULL AND trim(name) <> ''
        """
    )
    conn.execute(
        """
        INSERT INTO artist_search_terms
        SELECT
            sha256(t.artist_key || '|' || COALESCE(x.term, '') || '|' || COALESCE(x.term_type, '')),
            t.artist_key, x.term, x.normalized_term, x.term_type,
            x.source_system, 'PUBLIC_REFERENCE', x.knowledge_time, 'PRESENT'
        FROM selected_artists t
        JOIN (
            SELECT 'mbid::' || artist_mbid AS artist_key, term, normalized_term,
                   term_type, 'musicbrainz' AS source_system, NULL::TIMESTAMP AS knowledge_time
            FROM src.reference.artist_search_terms
            UNION ALL
            SELECT artist_key, alias, normalized_alias, alias_type, source_system,
                   ingested_at
            FROM src.core.artist_aliases
        ) x ON x.artist_key = t.artist_key
        WHERE x.term IS NOT NULL AND trim(x.term) <> ''
          AND COALESCE(x.term_type, '') <> 'canonical_name'
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY t.artist_key, x.term, COALESCE(x.term_type, '')
            ORDER BY x.knowledge_time DESC NULLS LAST, x.source_system
        ) = 1
        """
    )
    conn.execute(
        """
        INSERT INTO artist_external_ids
        SELECT
            sha256(e.external_id_key || '|' || e.entity_key),
            e.entity_key, e.id_type, NULLIF(trim(e.id_value), ''), e.url,
            e.source_system, 'PUBLIC_REFERENCE', e.knowledge_time,
            COALESCE(NULLIF(e.resolution_status, ''), 'PRESENT'),
            e.resolution_method, e.confidence
        FROM src.core.entity_external_ids e
        JOIN selected_artists t ON t.artist_key = e.entity_key
        WHERE lower(e.entity_type) = 'artist'
          AND NULLIF(trim(e.id_value), '') IS NOT NULL
        """
    )


def _materialize_attention(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        INSERT INTO attention_observations
        SELECT
            o.observation_key, o.artist_key, o.source_system, o.metric_kind,
            o.period_start, o.period_end, o.value, o.value_sum, o.value_unit,
            o.status, o.source_url, o.retrieved_at,
            COALESCE(
                TRY_CAST(o.provenance_json->>'knowledge_time' AS TIMESTAMP),
                o.retrieved_at
            ),
            CASE WHEN o.source_system = 'listenbrainz' THEN 'PUBLIC_CC0_ATTENTION'
                 WHEN o.source_system = 'wikimedia' THEN 'PUBLIC_PAGEVIEW_ATTENTION'
                 ELSE 'PUBLIC_SOURCE_OBSERVATION' END,
            'PUBLIC_DOMAIN_DEDICATED'
        FROM src.metrics.artist_attention_observations o
        JOIN selected_artists t ON t.artist_key = o.artist_key
        """
    )


def _materialize_markets(
    conn: duckdb.DuckDBPyConnection, artists: list[dict[str, Any]], as_of: str
) -> None:
    rows: list[tuple[Any, ...]] = []
    for artist in artists:
        key = str(artist["key"])
        for market in artist.get("markets") or []:
            market_key = market.get("market")
            if not market_key:
                continue
            rows.append(
                (
                    f"{key}|{market_key}|{as_of}",
                    key,
                    str(market_key),
                    market.get("shows"),
                    None,
                    None,
                    None,
                    None,
                    None,
                    "artist_security_estate",
                    "PILOT_25K_ESTATE_SUMMARY",
                    as_of,
                    "OBSERVED_SUMMARY",
                    "Estate provides market/show summary only; dates, venues, tickets and future activity remain UNKNOWN here.",
                )
            )
    conn.executemany(
        """
        INSERT INTO artist_markets VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS DATE), ?, ?)
        """,
        rows,
    )


def _materialize_peers(
    conn: duckdb.DuckDBPyConnection, affinity_path: Path | None,
    max_peers_per_artist: int = 12,
) -> None:
    if affinity_path is None:
        return
    if not affinity_path.exists():
        raise FileNotFoundError(f"affinity Parquet does not exist: {affinity_path}")
    path_sql = _q(affinity_path)
    cols = {row[0] for row in conn.execute(f"DESCRIBE SELECT * FROM read_parquet({path_sql})").fetchall()}
    required = {"artist_key_a", "artist_key_b", "shared_listeners", "jaccard"}
    missing = required - cols
    if missing:
        raise ValueError(f"affinity Parquet is missing columns: {sorted(missing)}")
    cosine_expr = "CAST(cosine AS DOUBLE)" if "cosine" in cols else "NULL::DOUBLE"
    knowledge_expr = (
        "TRY_CAST(knowledge_time AS TIMESTAMP)" if "knowledge_time" in cols else "NULL::TIMESTAMP"
    )
    conn.execute(
        f"""
        CREATE TEMP VIEW affinity_directed AS
        SELECT artist_key_a AS subject_key, artist_key_b AS peer_key,
               CAST(shared_listeners AS BIGINT) AS shared_listeners,
               CAST(jaccard AS DOUBLE) AS jaccard, {cosine_expr} AS cosine,
               {knowledge_expr} AS knowledge_time
        FROM read_parquet({path_sql})
        UNION ALL
        SELECT artist_key_b, artist_key_a,
               CAST(shared_listeners AS BIGINT), CAST(jaccard AS DOUBLE),
               {cosine_expr}, {knowledge_expr}
        FROM read_parquet({path_sql})
        """
    )
    conn.execute(
        """
        INSERT INTO artist_peers
        SELECT
            sha256(subject_key || '|' || peer_key || '|pilot'), subject_key, peer_key,
            p.name,
            ROW_NUMBER() OVER (
                PARTITION BY subject_key
                ORDER BY shared_listeners DESC NULLS LAST, jaccard DESC NULLS LAST, peer_key
            )::INTEGER,
            shared_listeners, jaccard, cosine,
            'listenbrainz', 'PILOT_AUDIENCE_DATA', knowledge_time,
            'DESCRIPTIVE_PILOT',
            'Shared listeners and Jaccard from the 1% pilot; this is audience affinity, not local demand or ticket intent.'
        FROM (
            SELECT subject_key, peer_key, shared_listeners, jaccard, cosine, knowledge_time,
                   ROW_NUMBER() OVER (
                       PARTITION BY subject_key, peer_key
                       ORDER BY shared_listeners DESC NULLS LAST, jaccard DESC NULLS LAST
                   ) AS duplicate_rank
            FROM affinity_directed
            WHERE subject_key IN (SELECT artist_key FROM selected_artists)
              AND peer_key IN (SELECT artist_key FROM selected_artists)
        ) d
        LEFT JOIN src.core.artists p ON p.artist_key = d.peer_key
        WHERE d.duplicate_rank = 1
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY subject_key
            ORDER BY shared_listeners DESC NULLS LAST, jaccard DESC NULLS LAST, peer_key
        ) <= {int(max_peers_per_artist)}
        """
    )


def _materialize_event_history(conn: duckdb.DuckDBPyConnection, max_per_artist: int) -> None:
    conn.execute(
        f"""
        INSERT INTO event_history
        SELECT * EXCLUDE (artist_rank)
        FROM (
            SELECT
                'mb-event::' || e.mbid || '::' || p.artist_mbid AS event_key,
                'mbid::' || p.artist_mbid AS artist_key,
                p.artist_name,
                e.name,
                TRY_CAST(SUBSTR(e.begin_date, 1, 10) AS DATE),
                TRY_CAST(SUBSTR(e.end_date, 1, 10) AS DATE),
                e.event_type,
                NULLIF(REGEXP_EXTRACT(e.name, '(?i)(?: at | @ )(.+)$', 1), ''),
                NULL, NULL, NULL, NULL, NULL,
                'musicbrainz', 'https://musicbrainz.org/event/' || e.mbid,
                'PUBLIC_REFERENCE', e.knowledge_time,
                CASE WHEN TRY_CAST(SUBSTR(e.begin_date, 1, 10) AS DATE) IS NULL
                     THEN 'OBSERVED_UNKNOWN_DATE' ELSE 'OBSERVED' END,
                CASE WHEN REGEXP_MATCHES(e.name, '(?i)( at | @ )')
                     THEN 'EVENT_NAME_EXPLICIT' ELSE NULL END,
                ROW_NUMBER() OVER (
                    PARTITION BY 'mbid::' || p.artist_mbid
                    ORDER BY TRY_CAST(SUBSTR(e.begin_date, 1, 10) AS DATE) DESC NULLS LAST, e.mbid
                ) AS artist_rank
            FROM src.core.event_performers p
            JOIN src.raw.musicbrainz_event e ON e.mbid = p.event_mbid
            JOIN selected_artists t ON t.artist_key = 'mbid::' || p.artist_mbid
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY e.mbid, p.artist_mbid
                ORDER BY p.knowledge_time DESC NULLS LAST, p.performer_key
            ) = 1
        ) bounded
        WHERE artist_rank <= {int(max_per_artist)}
        """
    )
    # The public box-office corpus is an additional source family.  It is
    # deliberately kept separate from MusicBrainz event observations.
    conn.execute(
        f"""
        INSERT INTO event_history
        SELECT * EXCLUDE (artist_rank)
        FROM (
            SELECT
                'boxoffice::' || b.canonical_engagement_id || '::' || a.artist_key,
                a.artist_key, a.name, a.name, b.start_date, b.end_date,
                'BOXOFFICE_ENGAGEMENT', b.venue, b.market, b.city, b.state,
                b.number_of_shows, b.is_multi_show,
                'boxoffice', NULL, 'PUBLIC_RESEARCH_CORPUS', NULL,
                CASE WHEN b.start_date IS NULL THEN 'OBSERVED_UNKNOWN_DATE' ELSE 'OBSERVED' END,
                NULL,
                ROW_NUMBER() OVER (
                    PARTITION BY a.artist_key
                    ORDER BY b.start_date DESC NULLS LAST, b.canonical_engagement_id
                ) AS artist_rank
            FROM src.research.canonical_boxoffice_engagements b
            JOIN artists a ON lower(a.name) = lower(b.artist)
        ) bounded
        WHERE artist_rank <= {int(max_per_artist)}
        """
    )


def _materialize_festivals(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        INSERT INTO festival_appearances (
            appearance_key, artist_key, event_key, festival_key, edition_key,
            festival_name, event_name, edition_year, event_date, performance_date,
            market_name, venue_name, billing_order, billing_tier, stage_name,
            artist_role, co_billed_artist_names, repeat_appearance_count,
            source_system, source_url, source_scope, knowledge_time, status
        )
        SELECT
            l.slot_key, t.artist_key, 'festival-edition::' || l.edition_key,
            l.festival_key, l.edition_key,
            f.name, f.name, l.year, l.performance_date, l.performance_date,
            NULL, NULL, l.billing_order, l.billing_tier, l.stage_name, l.artist_role,
            (
                SELECT string_agg(DISTINCT other.artist_name, ', ' ORDER BY other.artist_name)
                FROM src.core.lineup_slots other
                WHERE other.edition_key = l.edition_key
                  AND COALESCE(other.artist_key, '') <> COALESCE(l.artist_key, '')
            ),
            COUNT(*) OVER (PARTITION BY t.artist_key, l.festival_key),
            'festival_spine', l.source_url, 'PUBLIC_FESTIVAL_ARCHIVE',
            COALESCE(l.source_retrieved_at, l.updated_at, l.ingested_at),
            CASE WHEN l.is_cancelled THEN 'OBSERVED_CANCELLED' ELSE 'OBSERVED' END
        FROM src.core.lineup_slots l
        JOIN selected_artists t
          ON t.artist_key = l.artist_key OR t.mbid = l.musicbrainz_id
        LEFT JOIN src.core.festivals f ON f.festival_key = l.festival_key
        """
    )
    # The larger MusicBrainz event-series graph is the existing source of the
    # 11K+ festival/series appearances summarized by the 25K estate.  Keep it
    # source-separated from the curated festival spine and retain co-bills as
    # descriptive public-reference evidence.
    conn.execute(
        """
        INSERT INTO festival_appearances (
            appearance_key, artist_key, event_key, festival_key, edition_key,
            festival_name, event_name, edition_year, event_date, performance_date,
            market_name, venue_name, billing_order, billing_tier, stage_name,
            artist_role, co_billed_artist_names, repeat_appearance_count,
            source_system, source_url, source_scope, knowledge_time, status
        )
        SELECT
            sha256(t.artist_key || '|' || se.series_event_key),
            t.artist_key,
            'mb-event::' || se.event_mbid,
            es.series_key,
            se.series_event_key,
            es.name,
            se.event_name,
            TRY_CAST(SUBSTR(se.event_begin_date, 1, 4) AS INTEGER),
            TRY_CAST(SUBSTR(se.event_begin_date, 1, 10) AS DATE),
            TRY_CAST(SUBSTR(se.event_begin_date, 1, 10) AS DATE),
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            p.performer_role,
            (
                SELECT string_agg(DISTINCT other.artist_name, ', ' ORDER BY other.artist_name)
                FROM src.core.event_performers other
                WHERE other.event_mbid = p.event_mbid
                  AND other.artist_mbid <> p.artist_mbid
            ),
            COUNT(*) OVER (PARTITION BY t.artist_key, es.series_key),
            COALESCE(se.source_system, es.source_system, p.source_system, 'musicbrainz'),
            COALESCE(es.source_url, 'https://musicbrainz.org/event/' || se.event_mbid),
            'PUBLIC_MUSICBRAINZ_EVENT_SERIES',
            COALESCE(se.knowledge_time, es.knowledge_time, p.knowledge_time),
            CASE WHEN TRY_CAST(SUBSTR(se.event_begin_date, 1, 10) AS DATE) IS NULL
                 THEN 'OBSERVED_UNKNOWN_DATE' ELSE 'OBSERVED' END
        FROM src.core.event_performers p
        JOIN selected_artists t ON t.mbid = p.artist_mbid
        JOIN src.core.series_events se ON se.event_mbid = p.event_mbid
        JOIN src.core.event_series es ON es.series_key = se.series_key
        WHERE upper(COALESCE(es.series_type, '')) IN ('FESTIVAL', 'CONCERT SERIES', 'SERIES')
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY t.artist_key, se.series_event_key
            ORDER BY se.knowledge_time DESC NULLS LAST, es.series_key
        ) = 1
        """
    )


def _materialize_future(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        INSERT INTO future_events
        SELECT * EXCLUDE (snapshot_rank)
        FROM (
        SELECT
            'tm::' || s.platform_object_id || '::' || r.artist_key,
            r.artist_key, s.platform_object_id, s.event_name,
            TRY_CAST(SUBSTR(s.local_date, 1, 10) AS DATE),
            TRY_CAST(s.event_time AS TIMESTAMP), s.event_status, s.venue_name,
            NULLIF(CONCAT_WS(', ', s.city, s.state_code, s.country_code), ''),
            s.city, s.state_code, s.promoter,
            CASE WHEN s.price_min > 0 THEN CAST(s.price_min AS DOUBLE) ELSE NULL END,
            CASE WHEN s.price_max > 0 THEN CAST(s.price_max AS DOUBLE) ELSE NULL END,
            s.price_currency,
            CASE WHEN s.price_min > 0 OR s.price_max > 0
                 THEN 'ADVERTISED_STRUCTURED_RANGE' ELSE NULL END,
            CASE WHEN s.price_min > 0 OR s.price_max > 0
                 THEN 'ADVERTISED_RANGE' ELSE 'NO_CURRENT_TICKET_EVIDENCE' END,
            'ticketmaster', s.canonical_url, 'TICKETMASTER_DISCOVERY_API',
            s.retrieved_at, s.knowledge_time,
            CASE WHEN s.price_min > 0 OR s.price_max > 0
                 THEN 'OBSERVED_ADVERTISED_RANGE' ELSE 'OBSERVED_NO_CURRENT_PRICE' END,
            s.rights_status,
            ROW_NUMBER() OVER (
                PARTITION BY s.platform_object_id, r.artist_key
                ORDER BY s.knowledge_time DESC, s.retrieved_at DESC, s.snapshot_key DESC
            ) AS snapshot_rank
        FROM src.events.provider_event_snapshots s
        CROSS JOIN json_each(COALESCE(s.attractions, '[]'::JSON)) attraction
        JOIN src.identity.ticketmaster_artist_resolutions r
          ON r.attraction_id = json_extract_string(attraction.value, '$.ticketmaster_attraction_id')
         AND r.resolution_status = 'MATCHED_ARTIST'
        JOIN selected_artists t ON t.artist_key = r.artist_key
        WHERE s.provider = 'ticketmaster'
          AND TRY_CAST(SUBSTR(s.local_date, 1, 10) AS DATE) >= CURRENT_DATE
        ) latest
        WHERE snapshot_rank = 1
        """
    )


def _create_indexes(conn: duckdb.DuckDBPyConnection) -> None:
    for sql in (
        "CREATE INDEX artists_name_idx ON artists(name)",
        "CREATE INDEX artists_tier_idx ON artists(tier)",
        "CREATE INDEX search_terms_term_idx ON artist_search_terms(normalized_term)",
        "CREATE INDEX search_terms_artist_idx ON artist_search_terms(artist_key)",
        "CREATE INDEX attention_artist_idx ON attention_observations(artist_key)",
        "CREATE INDEX factor_artist_time_idx ON artist_factor_observations(artist_key, observation_time)",
        "CREATE INDEX sentiment_artist_date_idx ON artist_sentiment_observations(artist_key, \"date\")",
        "CREATE INDEX peers_subject_rank_idx ON artist_peers(subject_key, rank)",
        "CREATE INDEX markets_artist_idx ON artist_markets(artist_key, market_key)",
        "CREATE INDEX history_artist_date_idx ON event_history(artist_key, event_date)",
        "CREATE INDEX festival_artist_year_idx ON festival_appearances(artist_key, edition_year)",
        "CREATE INDEX future_artist_date_idx ON future_events(artist_key, event_date)",
    ):
        conn.execute(sql)


def build(
    *,
    report_path: Path = DEFAULT_REPORT,
    serving_snapshot: Path | None = None,
    affinity_parquet: Path | None = None,
    output_path: Path = DEFAULT_OUTPUT,
    max_events_per_artist: int = 250,
    max_peers_per_artist: int = 12,
) -> dict[str, Any]:
    """Materialize the product and return its compact validation summary."""
    if max_events_per_artist < 1:
        raise ValueError("max_events_per_artist must be positive")
    if max_peers_per_artist < 1:
        raise ValueError("max_peers_per_artist must be positive")
    report, artists = _read_estate(report_path)
    source = serving_snapshot or _current_snapshot()
    if not source.exists():
        raise FileNotFoundError(source)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = Path(tempfile.mkdtemp(prefix="talent-buyer-terminal-", dir=str(output_path.parent)))
    temp_path = temp_dir / (output_path.name + ".tmp.duckdb")
    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = duckdb.connect(str(temp_path))
        conn.execute("PRAGMA threads=2")
        conn.execute(f"ATTACH {_q(source)} AS src (READ_ONLY)")
        _create_schema(conn)
        _create_selected_table(conn, artists)
        _materialize_identity(conn)
        _materialize_attention(conn)
        _materialize_artist_intelligence(conn)
        _materialize_peers(
            conn, affinity_parquet.resolve() if affinity_parquet else None,
            max_peers_per_artist=max_peers_per_artist,
        )
        _materialize_markets(conn, artists, str(report.get("created_at", ""))[:10] or "1970-01-01")
        _materialize_event_history(conn, max_events_per_artist)
        _materialize_festivals(conn)
        _materialize_future(conn)
        _create_indexes(conn)

        counts = {
            "artists": _count(conn, "artists"),
            "search_terms": _count(conn, "artist_search_terms"),
            "external_ids": _count(conn, "artist_external_ids"),
            "attention_observations": _count(conn, "attention_observations"),
            "artist_factor_observations": _count(conn, "artist_factor_observations"),
            "artist_sentiment_observations": _count(conn, "artist_sentiment_observations"),
            "peers": _count(conn, "artist_peers"),
            "markets": _count(conn, "artist_markets"),
            "event_history": _count(conn, "event_history"),
            "festival_appearances": _count(conn, "festival_appearances"),
            "future_events": _count(conn, "future_events"),
        }
        expected_markets = int((report.get("counts") or {}).get("total_markets", counts["markets"]))
        affinity_source_rows = (
            int(conn.execute(
                f"SELECT COUNT(*) FROM read_parquet({_q(affinity_parquet)})"
            ).fetchone()[0])
            if affinity_parquet else None
        )
        affinity_sha256 = (
            hashlib.sha256(affinity_parquet.read_bytes()).hexdigest()
            if affinity_parquet else None
        )
        validation = {
            "expected_artist_count": len(artists),
            "actual_artist_count": counts["artists"],
            "expected_estate_market_links": expected_markets,
            "actual_estate_market_links": counts["markets"],
            "affinity_supplied": affinity_parquet is not None,
            "affinity_source_rows": affinity_source_rows,
            "affinity_source_sha256": affinity_sha256,
            "unknown_preserved": True,
            "ticket_zero_prices_null": True,
            "browser_reads_compact_file_only": True,
        }
        if counts["artists"] != len(artists):
            raise ValueError(f"materialized {counts['artists']} artists; expected {len(artists)}")
        if counts["markets"] != expected_markets:
            raise ValueError(f"materialized {counts['markets']} markets; expected {expected_markets}")
        if affinity_parquet and (not affinity_source_rows or not counts["peers"]):
            raise ValueError("affinity source was supplied but no pilot peer evidence materialized")
        conn.execute(
            """
            INSERT INTO product_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "TALENT_BUYER_TERMINAL_V1",
                "artist_security_terminal_v1",
                datetime.now(UTC).replace(tzinfo=None),
                str(source.resolve()),
                str(report_path.resolve()),
                str(affinity_parquet.resolve()) if affinity_parquet else None,
                counts["artists"], counts["markets"], counts["peers"],
                counts["event_history"], counts["festival_appearances"], counts["future_events"],
                "VERIFIED_COMPACT_BUILD",
                json.dumps(validation),
                "Read-only buyer evidence; pilot audience affinity is descriptive; no score, demand forecast, booking advice, attendance or gross prediction.",
            ],
        )
        conn.execute("CHECKPOINT")
        conn.close()
        conn = None
        os.replace(temp_path, output_path)
        return {"output": str(output_path), "counts": counts, "validation": validation}
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--serving-snapshot", type=Path, default=None)
    parser.add_argument(
        "--affinity-parquet",
        type=Path,
        default=None,
        help="Optional local 1%% pilot Gold Parquet; never downloaded by this script.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-events-per-artist", type=int, default=250)
    args = parser.parse_args()
    result = build(
        report_path=args.report,
        serving_snapshot=args.serving_snapshot,
        affinity_parquet=args.affinity_parquet,
        output_path=args.output,
        max_events_per_artist=args.max_events_per_artist,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
