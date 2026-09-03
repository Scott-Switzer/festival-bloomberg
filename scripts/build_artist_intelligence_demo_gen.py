#!/usr/bin/env python3
"""Build a local Artist Intelligence demo generation from a serving snapshot.

The compact serving generations published before the artist-intelligence
migration carry attention/listenership observations but not the canonical
factor tape tables. This tool copies a serving snapshot and extends it with
the ``artist_factor_observations`` + ``artist_sentiment_observations`` schema
and materializes factor rows from the snapshot's own real observations under
the canonical temporal contract (migration 049 / TAPE_FIELDS).

Everything stays local: the output generation is never published to R2 and
never replaces the cloud CURRENT pointer. Sentiment is intentionally left
unseeded (no raw social text exists in a serving snapshot; inventing a daily
aggregate would violate UNKNOWN != 0). Panels that need a licensed provider
surface as PROVIDER_READY / AUTH_REQUIRED in the terminal.

Example:
    python scripts/build_artist_intelligence_demo_gen.py \
        --serving-db serving/artist_security_terminal_v1/terminal.duckdb \
        --output serving/artist_security_terminal_v1/terminal_demo_ai_v1.duckdb
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

GENERATION_PREFIX = "artist_factor_tape_demo_v1"


def _factor_ddl() -> str:
    """Create-table DDL identical to the serving materializer contract."""
    return """
    CREATE TABLE IF NOT EXISTS artist_factor_observations (
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

    CREATE TABLE IF NOT EXISTS artist_sentiment_observations (
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


def build_demo_generation(
    serving_db: Path,
    output: Path,
    *,
    rights_by_source: dict[str, str] | None = None,
) -> dict:
    """Copy the snapshot and materialize canonical factor rows from it."""
    rights = rights_by_source or {"listenbrainz": "CC0"}
    if not serving_db.is_file():
        raise FileNotFoundError(f"serving snapshot not found: {serving_db}")
    if output == serving_db:
        raise ValueError("output must differ from the source snapshot")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(serving_db, output)

    import duckdb

    conn = duckdb.connect(str(output))
    generation = f"{GENERATION_PREFIX}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    try:
        conn.execute(_factor_ddl())
        rights_case = "CASE " + " ".join(
            f"WHEN source_system = '{name}' THEN '{label}'"
            for name, label in rights.items()
        ) + " ELSE 'SOURCE_LICENSE_REVIEWED' END"
        conn.execute(
            f"""
            INSERT INTO artist_factor_observations (
                factor_observation_key, artist_key, factor_family, factor_name,
                platform, value, unit, observation_time, available_at,
                knowledge_time, retrieved_at, period_start, period_end, source,
                evidence_ref, source_scope, rights_status,
                commercial_use_status, quality_status, generation, evidence_json
            )
            SELECT
                lower(hex(sha256(concat_ws('|',
                    o.artist_key,
                    CASE
                        WHEN o.metric_kind IN ('LISTENBRAINZ_LISTENER_COUNT', 'LISTENBRAINZ_LISTEN_COUNT')
                        THEN 'STREAMING'
                        ELSE 'LISTENERSHIP'
                    END,
                    o.metric_kind, o.source_system,
                    COALESCE(CAST(o.period_start AS VARCHAR), CAST(o.retrieved_at AS VARCHAR)),
                    o.source_system, '{generation}'
                )))),
                o.artist_key,
                CASE
                    WHEN o.metric_kind IN ('LISTENBRAINZ_LISTENER_COUNT', 'LISTENBRAINZ_LISTEN_COUNT')
                    THEN 'STREAMING'
                    ELSE 'LISTENERSHIP'
                END,
                o.metric_kind,
                o.source_system,
                o.value,
                o.value_unit,
                COALESCE(CAST(o.period_start AS TIMESTAMP), o.retrieved_at),
                COALESCE(o.retrieved_at, o.knowledge_time),
                COALESCE(o.knowledge_time, o.retrieved_at),
                o.retrieved_at,
                o.period_start,
                o.period_end,
                o.source_system,
                o.source_url,
                COALESCE(o.source_scope, 'R2_EXPORTED_OBSERVATION'),
                {rights_case},
                'PROTOTYPE_ONLY',
                CASE WHEN o.value IS NULL THEN 'UNKNOWN' ELSE 'OBSERVED' END,
                '{generation}',
                NULL
            FROM attention_observations o
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS factor_artist_time_idx "
            "ON artist_factor_observations(artist_key, observation_time)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS sentiment_artist_date_idx "
            "ON artist_sentiment_observations(artist_key, \"date\")"
        )
        counts = {
            "artists": int(conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]),
            "factor_rows": int(
                conn.execute("SELECT COUNT(*) FROM artist_factor_observations").fetchone()[0]
            ),
            "factor_artists": int(
                conn.execute(
                    "SELECT COUNT(DISTINCT artist_key) FROM artist_factor_observations"
                ).fetchone()[0]
            ),
            "sentiment_rows": int(
                conn.execute("SELECT COUNT(*) FROM artist_sentiment_observations").fetchone()[0]
            ),
            "series_artists": int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT artist_key
                        FROM artist_factor_observations
                        GROUP BY artist_key, factor_name, platform
                        HAVING COUNT(*) >= 2
                    )
                    """
                ).fetchone()[0]
            ),
        }
        families = conn.execute(
            "SELECT factor_family, COUNT(*) FROM artist_factor_observations GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        duplicate_keys = int(
            conn.execute(
                "SELECT COUNT(*) FROM (SELECT factor_observation_key FROM artist_factor_observations "
                "GROUP BY 1 HAVING COUNT(*) > 1)"
            ).fetchone()[0]
        )
        if duplicate_keys:
            raise RuntimeError(f"duplicate factor keys materialized: {duplicate_keys}")
        counts["families"] = {name: int(n) for name, n in families}
        counts["generation"] = generation
        counts["status"] = "PASS"
        counts["published_to_r2"] = False
        counts["sentiment_note"] = (
            "PROVIDER_READY: no raw social text exists in serving snapshots; "
            "daily aggregates arrive only from the acquisition boundary."
        )
        return counts
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, default=None)
    args = parser.parse_args()
    counts = build_demo_generation(args.serving_db, args.output)
    print(json.dumps(counts, indent=2, sort_keys=True))
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
