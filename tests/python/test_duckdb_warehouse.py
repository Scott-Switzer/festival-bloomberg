"""DuckDB warehouse initialization and sentiment persistence tests."""

from __future__ import annotations

from pathlib import Path

import duckdb

from festival_bloomberg.duckdb_warehouse import DuckDbWarehouse, open_warehouse


def test_open_initializes_schema(tmp_path: Path):
    db_path = tmp_path / "warehouse.duckdb"
    with open_warehouse(db_path) as db:
        tables = db.list_tables()
        for required in (
            "observations",
            "lineups",
            "costs",
            "ingestion_logs",
            "ingestion_runs",
            "telemetry",
            "schema_migrations",
            "sentiment_scores",
        ):
            assert required in tables


def test_migrate_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "warehouse.duckdb"
    with open_warehouse(db_path) as db1:
        db1.migrate()
        db1.migrate()
        assert "sentiment_scores" in db1.list_tables()

    # Re-open same file after close — schema + writes survive.
    with open_warehouse(db_path) as db2:
        assert "observations" in db2.list_tables()


def test_migrate_upgrades_legacy_observations_without_data_loss(tmp_path: Path):
    db_path = tmp_path / "legacy.duckdb"
    connection = duckdb.connect(str(db_path))
    connection.execute(
        """
        CREATE TABLE observations (
          id VARCHAR PRIMARY KEY,
          source_url VARCHAR NOT NULL,
          raw_content VARCHAR,
          content_hash VARCHAR,
          retrieved_at TIMESTAMP NOT NULL,
          status VARCHAR,
          kind VARCHAR NOT NULL,
          festival_id VARCHAR,
          edition_id VARCHAR,
          source_domain VARCHAR NOT NULL,
          tier VARCHAR,
          evidence_json VARCHAR,
          payload_json VARCHAR
        );
        INSERT INTO observations VALUES (
          'legacy-1',
          'https://example.com/lineup?utm_source=legacy',
          'Legacy lineup',
          'legacy-hash',
          '2026-01-01T00:00:00',
          'ok',
          'lineup',
          'fest_example',
          'ed_example_2026',
          'example.com',
          'local_http',
          '[]',
          '"Legacy lineup"'
        );
        """
    )
    connection.close()

    with open_warehouse(db_path) as db:
        columns = {
            row[1]
            for row in db.connection.execute(
                "PRAGMA table_info('observations')"
            ).fetchall()
        }
        assert {
            "canonical_url",
            "normalized_content",
            "dedup_key",
            "first_seen_at",
            "last_seen_at",
            "seen_count",
            "winner_key",
            "published_at",
            "published_at_precision",
        }.issubset(columns)
        row = db.connection.execute(
            """
            SELECT source_url, canonical_url, raw_content, first_seen_at,
                   last_seen_at, seen_count, dedup_key
            FROM observations WHERE id = 'legacy-1'
            """
        ).fetchone()
        assert row is not None
        assert row[0] == "https://example.com/lineup?utm_source=legacy"
        assert row[1] == row[0]
        assert row[2] == "Legacy lineup"
        assert row[3] == row[4]
        assert row[5] == 1
        # Legacy rows remain outside canonical dedup to preserve history.
        assert row[6] is None
        # Upgraded legacy tables keep their original relaxed table constraints.
        status_column = next(
            row for row in db.connection.execute(
                "PRAGMA table_info('observations')"
            ).fetchall()
            if row[1] == "status"
        )
        assert status_column[3] == 0  # notnull
        assert db.connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 1"
        ).fetchone() == (1,)
        assert db.connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 2"
        ).fetchone() == (1,)


def test_upsert_and_get_sentiment(tmp_path: Path):
    db_path = tmp_path / "warehouse.duckdb"
    with DuckDbWarehouse(db_path) as db:
        scored = db.upsert_sentiment(
            score_id="s1",
            text="Fans love the brilliant new Coachella lineup announcement!",
            source_id="obs-1",
            festival_id="fest_coachella",
            scored_at="2026-04-01T12:00:00",
        )
        assert scored.label == "positive"
        row = db.get_sentiment("s1")
        assert row is not None
        assert row["id"] == "s1"
        assert row["festival_id"] == "fest_coachella"
        assert row["label"] == "positive"
        assert float(row["compound"]) > 0.05

        # Upsert updates in place
        db.upsert_sentiment(
            score_id="s1",
            text="Disaster — refunds and anger after a sudden cancellation.",
            festival_id="fest_coachella",
            scored_at="2026-04-02T12:00:00",
        )
        updated = db.get_sentiment("s1")
        assert updated is not None
        assert updated["label"] == "negative"
