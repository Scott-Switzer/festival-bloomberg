"""DuckDB connection ownership for the terminal HTTP servers.

DuckDB connections are not safe to use concurrently.  Production terminal
instances therefore open one connection per request thread against the
immutable serving file, while mutable workspace writes are protected by a
small application-level write lock.  Unit tests frequently inject an
in-memory connection, which cannot be reopened by path; those callers retain a
serialized compatibility path instead of receiving unsafe concurrent access.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import duckdb


_MEMORY_DATABASE_NAMES = {"", ":memory:", "memory", "temp", "temporary"}


def database_path_for(connection: Any) -> str | None:
    """Return the main DuckDB file path, or ``None`` for in-memory databases.

    DuckDB exposes the active database list through ``PRAGMA database_list``.
    This intentionally probes only the caller-owned connection and never
    creates or copies a database.
    """
    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
    except Exception:
        return None
    for row in rows:
        # DuckDB currently returns (database_id, database_name, file_path).
        if len(row) < 3:
            continue
        name = str(row[1] or "").lower()
        raw_path = str(row[2] or "")
        if name in {"memory", "temp", "temporary"}:
            continue
        if raw_path and raw_path.lower() not in _MEMORY_DATABASE_NAMES:
            return os.path.abspath(raw_path)
    return None


class ThreadLocalDuckDBConnection:
    """Delegate DuckDB operations to one connection owned by each thread.

    The base connection belongs to the thread that constructed this proxy.  A
    non-owner thread lazily opens the same database with the requested mode.
    ``release_current`` is called by the HTTP dispatcher after each request so
    a server that creates one thread per request does not retain connections
    forever.  The proxy remains compatible with existing repository code via
    ``__getattr__``.
    """

    def __init__(
        self,
        base_connection: duckdb.DuckDBPyConnection,
        *,
        database_path: str | None,
        read_only: bool,
    ) -> None:
        self._base_connection = base_connection
        self._database_path = (
            os.path.abspath(database_path) if database_path else None
        )
        self._read_only = read_only
        self._owner_thread_id = threading.get_ident()
        self._local = threading.local()
        self._connections: dict[int, duckdb.DuckDBPyConnection] = {
            self._owner_thread_id: base_connection
        }
        self._connections_lock = threading.Lock()

    @property
    def compatibility_mode(self) -> bool:
        """Whether the injected connection cannot be reopened per thread."""
        return self._database_path is None

    @property
    def database_path(self) -> str | None:
        return self._database_path

    @property
    def current_connection(self) -> duckdb.DuckDBPyConnection:
        """Expose the current owned connection for diagnostics/tests."""
        return self._current()

    def _current(self) -> duckdb.DuckDBPyConnection:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            return connection

        thread_id = threading.get_ident()
        if thread_id == self._owner_thread_id:
            connection = self._base_connection
        elif self._database_path:
            connection = duckdb.connect(self._database_path, read_only=self._read_only)
        else:
            # The application-level compatibility lock serializes callers when
            # a fixture supplies an in-memory connection.
            connection = self._base_connection

        self._local.connection = connection
        with self._connections_lock:
            self._connections[thread_id] = connection
        return connection

    def release_current(self) -> None:
        """Close and forget a non-owner request connection."""
        thread_id = threading.get_ident()
        connection = getattr(self._local, "connection", None)
        if connection is None:
            return
        try:
            del self._local.connection
        except AttributeError:
            pass
        if thread_id == self._owner_thread_id or connection is self._base_connection:
            return
        with self._connections_lock:
            self._connections.pop(thread_id, None)
        try:
            connection.close()
        except Exception:
            pass

    def execute(self, *args: Any, **kwargs: Any):
        return self._current().execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any):
        return self._current().executemany(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._current(), name)

    def close(self) -> None:
        """Close all connections owned by this proxy."""
        with self._connections_lock:
            connections = list(
                {id(conn): conn for conn in self._connections.values()}.values()
            )
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass


class CompatibilityRequestLock:
    """RLock used for non-reopenable in-memory fixture connections."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def __enter__(self) -> "CompatibilityRequestLock":
        self._lock.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._lock.release()


def wrap_connection(
    connection: Any,
    *,
    read_only: bool,
    database_path: str | None = None,
) -> ThreadLocalDuckDBConnection:
    """Wrap a raw DuckDB connection without changing its database contents."""
    if isinstance(connection, ThreadLocalDuckDBConnection):
        return connection
    return ThreadLocalDuckDBConnection(
        connection,
        database_path=database_path or database_path_for(connection),
        read_only=read_only,
    )
