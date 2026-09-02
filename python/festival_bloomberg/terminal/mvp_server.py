"""Talent Buyer MVP terminal — product-only read models over the compact artifact.

This server intentionally has NO dependency on the canonical warehouse or the
full terminal snapshot. It opens exactly one file: the compact
``artist_security_terminal_v1`` serving DuckDB (read-only) plus a tiny mutable
workspace DB for the buyer shortlist. Every response reuses the existing
``festival_bloomberg.terminal.artist_security`` contract so the product schema,
semantics (UNKNOWN != 0, source/status/knowledge-time boundaries) and
naming stay identical to the existing terminal.

Run locally (self-healing launcher does this)::

    ./scripts/run_terminal.sh

or manually::

    PYTHONPATH=python python -m festival_bloomberg.terminal.mvp_server \
        --serving-db serving/artist_security_terminal_v1/terminal.duckdb \
        [--port 8931]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import duckdb

from . import artist_security
from .storage import WORKSPACE_DEFAULT_DB

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERVING_DB = _PROJECT_ROOT / "serving" / "artist_security_terminal_v1" / "terminal.duckdb"
DEFAULT_CURRENT_JSON = _PROJECT_ROOT / "serving" / "artist_security_terminal_v1" / "CURRENT.json"
MVP_STATIC_DIR = _PROJECT_ROOT / "apps" / "terminal" / "mvp"
SHORTLIST_DB = Path(WORKSPACE_DEFAULT_DB).parent / "terminal_mvp_shortlists.duckdb"
DEFAULT_PORT = 8931

_SHORTLIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS shortlist_items (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    artist_key VARCHAR,
    market VARCHAR,
    event_date VARCHAR,
    venue VARCHAR,
    capacity VARCHAR,
    notes VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _json(payload: Any) -> bytes:
    return json.dumps(payload, default=str).encode("utf-8")


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [column[0] for column in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _market_pretty(market_key: str) -> str:
    """'chicago-il' -> 'Chicago, IL'; 'london-gb' -> 'London, GB'."""
    parts = [p for p in str(market_key or "").split("-") if p]
    if not parts:
        return str(market_key or "")
    if len(parts) >= 2:
        city = " ".join(parts[:-1]).title()
        region = parts[-1].upper()
        return f"{city}, {region}"
    return str(market_key).title()


def open_workspace(path: str = str(SHORTLIST_DB)) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(path)
    conn.execute(_SHORTLIST_SCHEMA)
    conn.commit()
    return conn


def serving_metadata(
    db_path: Path, current_json_path: Path | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, Any]:
    """Serve metadata: CURRENT.json contents (when a launcher wrote it), the
    artifact's own product_meta row, and live file stats."""
    meta: dict[str, Any] = {
        "artifact": "artist_security_terminal_v1",
        "db_path": str(db_path),
        "db_exists": db_path.is_file(),
        "db_bytes": db_path.stat().st_size if db_path.is_file() else None,
        "current_json": None,
        "product_meta": None,
        "demo_artists": [],
    }
    if current_json_path is not None and current_json_path.is_file():
        try:
            meta["current_json"] = json.loads(current_json_path.read_text(encoding="utf-8"))
        except Exception:
            meta["current_json"] = None
    if conn is not None:
        try:
            meta["product_meta"] = _rows(conn, "SELECT * FROM product_meta LIMIT 1")
            try:
                meta["demo_artists"] = _rows(
                    conn, "SELECT * FROM demo_artists ORDER BY completeness DESC LIMIT 12"
                )
            except Exception:
                meta["demo_artists"] = []
        except Exception:
            meta["product_meta"] = None
    if meta.get("current_json"):
        meta["generation"] = meta["current_json"].get("generation")
        meta["sha256"] = meta["current_json"].get("sha256")
        meta["row_counts"] = meta["current_json"].get("row_counts")
        if not meta["demo_artists"] and meta["current_json"].get("demo_artists"):
            meta["demo_artists"] = meta["current_json"]["demo_artists"]
    return meta


class MvpTerminalApp:
    """Product-only dispatcher: compact serving DB + workspace shortlist DB."""

    def __init__(self, conn, workspace_conn, *, db_path: Path, current_json_path: Path) -> None:
        self.conn = conn
        self.workspace_conn = workspace_conn
        self._lock = threading.Lock()
        self._meta_lock = threading.Lock()
        self._meta_cache: dict[str, Any] | None = None
        self._db_path = db_path
        self._current_json_path = current_json_path

    def dispatch(self, method: str, path: str, query: str = "", body: bytes = b"") -> dict[str, Any]:
        with self._lock:
            return self._dispatch_locked(method, path, query, body)

    def _serving_meta(self) -> dict[str, Any]:
        with self._meta_lock:
            if self._meta_cache is None:
                self._meta_cache = serving_metadata(
                    self._db_path, self._current_json_path, self.conn
                )
            return self._meta_cache

    def _dispatch_locked(self, method: str, path: str, query: str = "", body: bytes = b"") -> dict[str, Any]:
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        params = {k: v[0] for k, v in parse_qs(query).items()}

        if path == "/api/status":
            return self._ok(self._serving_meta())
        if path == "/api/coverage":
            return self._ok(self._coverage())

        if path == "/api/search":
            return self._ok(artist_security.search_artists(
                self.conn, params.get("q", ""), int(params.get("limit", 25))))
        if path == "/api/artist-security/search":
            return self._ok(artist_security.search_artists(
                self.conn, params.get("q", ""), int(params.get("limit", 25))))
        if path == "/api/artist-security/compare":
            left, right = params.get("a"), params.get("b")
            if not left or not right:
                return self._bad_request("compare requires a and b artist keys")
            comparison = artist_security.compare_artists(self.conn, left, right)
            return self._ok(comparison) if comparison is not None else self._not_found()

        if path == "/api/demo":
            return self._ok(self._demo())
        if path == "/api/markets":
            return self._ok(self._markets(params.get("q", ""), int(params.get("limit", 200))))
        if path.startswith("/api/market/"):
            market_key = path[len("/api/market/"):]
            return self._ok(self._market_detail(market_key))

        if path == "/api/shortlist" and method == "GET":
            return self._ok(self._list_shortlist())
        if path == "/api/shortlist" and method == "POST":
            return self._ok(self._add_shortlist(body))
        if path.startswith("/api/shortlist/") and method == "DELETE":
            item_id = path[len("/api/shortlist/"):]
            return self._ok(self._delete_shortlist(item_id))

        if path.startswith("/api/artist-security/"):
            artist_key = path[len("/api/artist-security/"):]
            payload = artist_security.get_artist_security(self.conn, artist_key)
            return self._ok(payload) if payload is not None else self._not_found()

        return self._not_found()

    # ── product read models ──────────────────────────────────────

    def _coverage(self) -> dict[str, Any]:
        meta = self._serving_meta()
        counts = {}
        for table in ("artists", "artist_search_terms", "artist_external_ids",
                      "attention_observations", "artist_peers", "artist_markets",
                      "event_history", "festival_appearances", "future_events"):
            try:
                counts[table] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except Exception:
                counts[table] = 0
        try:
            counts["artists_with_peers"] = int(self.conn.execute(
                "SELECT COUNT(DISTINCT subject_key) FROM artist_peers").fetchone()[0])
        except Exception:
            counts["artists_with_peers"] = 0
        try:
            counts["demo_artist_count"] = int(self.conn.execute(
                "SELECT COUNT(*) FROM demo_artists").fetchone()[0])
        except Exception:
            counts["demo_artist_count"] = 0
        return {
            "contract_version": artist_security.CONTRACT_VERSION,
            "counts": counts,
            "generation": meta.get("generation"),
            "built_at": (meta.get("product_meta") or [{}])[0].get("built_at"),
            "validation_status": (meta.get("product_meta") or [{}])[0].get("validation_status"),
            "unknown_semantics": "UNKNOWN != 0; place != venue; listing != sale; offer != transaction",
            "no_composite_score": True,
        }

    def _demo(self) -> list[dict[str, Any]]:
        try:
            rows = _rows(
                self.conn,
                """SELECT * FROM demo_artists ORDER BY completeness DESC,
                   market_count DESC, historical_event_count DESC, name LIMIT 12""",
            )
        except Exception:
            rows = []
        if rows:
            for row in rows:
                row["entity_id"] = row.get("artist_key")
            return rows
        fallback = (self._serving_meta().get("current_json") or {}).get("demo_artists") or []
        return [dict(d) | {"entity_id": d.get("artist_key")} for d in fallback]

    def _markets(self, q: str, limit: int) -> dict[str, Any]:
        q = q.strip().lower()
        where = f"WHERE lower(market_key) LIKE '%{q.replace(chr(39), chr(39)*2)}%'" if q else ""
        rows = _rows(
            self.conn,
            f"""SELECT market_key,
                       COUNT(*) AS artist_count,
                       COALESCE(SUM(observed_shows), 0) AS total_shows,
                       MAX(last_play_date) AS last_play_date,
                       COALESCE(SUM(COALESCE(future_events, 0)), 0) AS future_events
                FROM artist_markets
                {where}
                GROUP BY market_key
                ORDER BY total_shows DESC, artist_count DESC, market_key
                LIMIT {max(1, min(int(limit), 1000))}""",
        )
        for row in rows:
            row["pretty"] = _market_pretty(row["market_key"])
        return {"count": len(rows), "items": rows}

    def _market_detail(self, market_key: str) -> dict[str, Any]:
        rows = _rows(
            self.conn,
            """SELECT a.artist_key, a.name, a.tier, m.observed_shows,
                      m.first_play_date, m.last_play_date, m.future_events,
                      m.ticket_evidence_count
               FROM artist_markets m JOIN artists a USING (artist_key)
               WHERE m.market_key = ?
               ORDER BY m.observed_shows DESC NULLS LAST, a.name
               LIMIT 300""",
            [market_key],
        )
        return {
            "market_key": market_key,
            "pretty": _market_pretty(market_key),
            "count": len(rows),
            "items": rows,
        }

    # ── shortlist workspace ──────────────────────────────────────

    def _list_shortlist(self) -> list[dict[str, Any]]:
        return _rows(
            self.workspace_conn,
            "SELECT * FROM shortlist_items ORDER BY created_at DESC, name",
        )

    def _add_shortlist(self, body: bytes) -> dict[str, Any]:
        try:
            item = json.loads(body.decode("utf-8"))
        except Exception:
            return self._bad_request("invalid JSON body")
        import hashlib
        import uuid
        name = str(item.get("name") or "").strip()
        if not name:
            return self._bad_request("name is required")
        item_id = str(item.get("id") or uuid.uuid4().hex)
        self.workspace_conn.execute(
            """
            INSERT INTO shortlist_items (id, name, artist_key, market, event_date,
                                         venue, capacity, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [item_id, name,
             str(item.get("artist_key") or "") or None,
             str(item.get("market") or "") or None,
             str(item.get("date") or item.get("event_date") or "") or None,
             str(item.get("venue") or "") or None,
             str(item.get("capacity") or "") or None,
             str(item.get("notes") or "") or None],
        )
        self.workspace_conn.commit()
        # id is user-visible for delete/persistence confirmation.
        return {"added": True, "id": item_id}

    def _delete_shortlist(self, item_id: str) -> dict[str, Any]:
        self.workspace_conn.execute(
            "DELETE FROM shortlist_items WHERE id = ?", [item_id]
        )
        self.workspace_conn.commit()
        return {"removed": True, "id": item_id}

    # ── helpers ──────────────────────────────────────────────────

    def _ok(self, payload: Any) -> dict[str, Any]:
        return {"status": 200, "headers": {"Content-Type": "application/json"}, "body": _json(payload)}

    def _static(self, name: str) -> dict[str, Any]:
        safe = re.sub(r"[^A-Za-z0-9_.\-/]", "", name)
        fp = MVP_STATIC_DIR / safe
        if not fp.is_file():
            return self._not_found()
        ctype = (
            "text/html" if fp.suffix == ".html"
            else "text/css" if fp.suffix == ".css"
            else "application/javascript"
        )
        return {
            "status": 200,
            "headers": {"Content-Type": ctype, "Cache-Control": "no-store"},
            "body": fp.read_bytes(),
        }

    def _bad_request(self, message: str) -> dict[str, Any]:
        return {"status": 400, "headers": {"Content-Type": "application/json"},
                "body": _json({"error": message})}

    def _not_found(self) -> dict[str, Any]:
        return {"status": 404, "headers": {"Content-Type": "application/json"},
                "body": _json({"error": "not found"})}


class _Handler(BaseHTTPRequestHandler):
    app: MvpTerminalApp

    def _respond(self, result: dict[str, Any]) -> None:
        body = result["body"]
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(result["status"])
        for k, v in result["headers"].items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._respond(self.app.dispatch("GET", parsed.path, parsed.query))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self._respond(self.app.dispatch("POST", parsed.path, parsed.query, body))

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._respond(self.app.dispatch("DELETE", parsed.path, parsed.query))

    def log_message(self, *args: Any) -> None:
        pass


def make_app(
    serving_db: str | Path = DEFAULT_SERVING_DB,
    current_json: str | Path | None = None,
    workspace_db: str | Path = str(SHORTLIST_DB),
) -> MvpTerminalApp:
    db_path = Path(serving_db)
    current_path = Path(current_json) if current_json else db_path.parent / "CURRENT.json"
    conn = artist_security.open_product_db(str(db_path))
    workspace_conn = open_workspace(str(workspace_db))
    return MvpTerminalApp(conn, workspace_conn, db_path=db_path, current_json_path=current_path)


def serve(app: MvpTerminalApp, port: int, host: str = "127.0.0.1") -> None:
    handler = type("BoundHandler", (_Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Talent Buyer MVP terminal: http://{host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Talent Buyer MVP terminal")
    parser.add_argument("--serving-db", default=str(DEFAULT_SERVING_DB))
    parser.add_argument("--current-json", default=None)
    parser.add_argument("--workspace-db", default=str(SHORTLIST_DB))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    serve(make_app(args.serving_db, args.current_json, args.workspace_db), args.port, args.host)


if __name__ == "__main__":
    main()