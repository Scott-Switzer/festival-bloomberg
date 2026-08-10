"""
Python DuckDB warehouse aligned with the TypeScript scraper schema.

Ensures database initialization (CREATE TABLE IF NOT EXISTS) before use — a
common root cause of CI failures when tests queried an uninitialized file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb

from .migrations import apply_pending_migrations
from .paths import resolve_warehouse_path
from .vader_sentiment import SentimentScore, score_text


class DuckDbWarehouse:
    """Thin warehouse helper with idempotent migrations and sentiment persistence."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path: Path = resolve_warehouse_path(path, create_parent=True)
        self._conn = duckdb.connect(str(self.path))
        self.migrate()

    def migrate(self) -> int:
        return apply_pending_migrations(self._conn)

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def upsert_sentiment(
        self,
        *,
        score_id: str,
        text: str,
        source_id: str | None = None,
        festival_id: str | None = None,
        scored_at: str | None = None,
        score: SentimentScore | None = None,
    ) -> SentimentScore:
        result = score or score_text(text)
        ts_sql = "?" if scored_at else "CURRENT_TIMESTAMP"
        params: list[Any] = [
            score_id,
            source_id,
            festival_id,
            result.text,
            result.compound,
            result.pos,
            result.neu,
            result.neg,
            result.label,
        ]
        if scored_at:
            params.append(scored_at)

        self._conn.execute(
            f"""
            INSERT INTO sentiment_scores
              (id, source_id, festival_id, text, compound, pos, neu, neg, label, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {ts_sql})
            ON CONFLICT (id) DO UPDATE SET
              source_id = excluded.source_id,
              festival_id = excluded.festival_id,
              text = excluded.text,
              compound = excluded.compound,
              pos = excluded.pos,
              neu = excluded.neu,
              neg = excluded.neg,
              label = excluded.label,
              scored_at = excluded.scored_at
            """,
            params,
        )
        return result

    def get_sentiment(self, score_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, source_id, festival_id, text, compound, pos, neu, neg, label, scored_at "
            "FROM sentiment_scores WHERE id = ? LIMIT 1",
            [score_id],
        ).fetchone()
        if not row:
            return None
        keys = [
            "id",
            "source_id",
            "festival_id",
            "text",
            "compound",
            "pos",
            "neu",
            "neg",
            "label",
            "scored_at",
        ]
        return dict(zip(keys, row))

    def list_tables(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DuckDbWarehouse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_warehouse(path: str | os.PathLike[str] | None = None) -> DuckDbWarehouse:
    """Open (and initialize) the local warehouse at the resolved bloomberg path."""
    return DuckDbWarehouse(path)


def dumps_payload(payload: Any) -> str:
    return json.dumps(payload, default=str)
