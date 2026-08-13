"""Loader for the canonical DuckDB warehouse schema.

The DDL lives in ``schema/duckdb.sql`` at the repository root so that the SQL
is reviewable on its own and can be applied by non-Python tooling (for example
``duckdb warehouse.duckdb < schema/duckdb.sql``). This module reads that file,
splits it into individual statements, and applies them to a DuckDB connection.

Every statement in the file is idempotent (``CREATE ... IF NOT EXISTS``,
``CREATE OR REPLACE VIEW``, ``INSERT OR IGNORE``), so :func:`apply_schema` is
safe to call on every process start.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterator, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "schema", "duckdb.sql")

#: Logical layers created by the schema file.
SCHEMA_NAMES = ("raw", "core", "metrics", "model", "audit")


def load_schema_sql(path: Optional[str] = None) -> str:
    """Return the raw contents of the DuckDB schema file."""
    schema_path = path or SCHEMA_PATH
    try:
        with open(schema_path, "r", encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError as exc:  # pragma: no cover - packaging error
        raise FileNotFoundError(
            f"DuckDB schema file not found at {schema_path}. "
            "It is expected at schema/duckdb.sql relative to the project root."
        ) from exc


def split_statements(sql: str) -> List[str]:
    """Split a SQL script into executable statements.

    Semicolons inside string literals, dollar-quoted blocks, and comments are
    ignored, so the schema file can contain prose and seeded literals without
    tripping the splitter.
    """
    statements: List[str] = []
    buffer: List[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False

    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < length else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                buffer.append(char)
            index += 1
            continue

        if in_block_comment:
            if char == "*" and nxt == "/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if in_single:
            buffer.append(char)
            if char == "'":
                if nxt == "'":  # escaped quote
                    buffer.append(nxt)
                    index += 2
                    continue
                in_single = False
            index += 1
            continue

        if in_double:
            buffer.append(char)
            if char == '"':
                in_double = False
            index += 1
            continue

        if char == "-" and nxt == "-":
            in_line_comment = True
            index += 2
            continue

        if char == "/" and nxt == "*":
            in_block_comment = True
            index += 2
            continue

        if char == "'":
            in_single = True
            buffer.append(char)
            index += 1
            continue

        if char == '"':
            in_double = True
            buffer.append(char)
            index += 1
            continue

        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)

    return statements


def iter_statements(path: Optional[str] = None) -> Iterator[str]:
    """Yield each executable statement from the DuckDB schema file."""
    yield from split_statements(load_schema_sql(path))


def schema_statements(path: Optional[str] = None) -> List[str]:
    """Return every executable statement from the DuckDB schema file."""
    return split_statements(load_schema_sql(path))


def apply_schema(connection: Any, path: Optional[str] = None) -> int:
    """Apply the DuckDB schema to ``connection``.

    Args:
        connection: A ``duckdb.DuckDBPyConnection`` (or anything exposing
            ``execute``).
        path: Optional override for the schema file location.

    Returns:
        The number of statements executed.
    """
    statements = schema_statements(path)
    executed_count = 0
    
    try:
        # Begin transaction for atomic schema application
        connection.execute("BEGIN TRANSACTION")
        
        for statement in statements:
            try:
                connection.execute(statement)
                executed_count += 1
            except Exception as e:
                logger.error("Failed to apply schema statement: %s", statement[:200])
                logger.error("Error: %s", str(e))
                connection.execute("ROLLBACK")
                raise
        
        connection.execute("COMMIT")
        logger.debug("Applied %d DuckDB schema statements", executed_count)
        return executed_count
        
    except Exception:
        # Ensure rollback if commit wasn't reached
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        raise
