"""DuckDB warehouse initialization and sentiment persistence tests."""

from __future__ import annotations

from pathlib import Path

from festival_bloomberg.duckdb_warehouse import DuckDbWarehouse, open_warehouse


def test_open_initializes_schema(tmp_path: Path):
    db_path = tmp_path / "warehouse.duckdb"
    with open_warehouse(db_path) as db:
        tables = db.list_tables()
        for required in (
            "observations",
            "lineups",
            "costs",
            "telemetry",
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
