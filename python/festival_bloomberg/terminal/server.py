"""Read-only terminal server: JSON read models + the static SPA.

The terminal NEVER writes: every handler is a thin wrapper over a read model.
The activity tape and provider health are written by the OA driver
(``oa/intelligence_terminal.py``), not here. The warehouse is opened once and
read through a single connection.

Run locally::

    python -m festival_bloomberg.terminal.server [--port 8931] [--db path]

No credentials, no arbitrary SQL, no live provider calls per render.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..festivals.repository import FestivalSpineRepository
from ..flywheel.repository import FlywheelRepository
from ..intelligence import readmodels
from ..intelligence.ask import answer as ask_answer
from ..intelligence.ask import DeepSeekAskClient
from ..intelligence.llm import NimClient
from ..localenv import load_local_env
from ..research.repository import ResearchRepository
from ..warehouse.repository import FestivalRepository

STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "apps", "terminal", "static",
)

DEFAULT_PORT = 8931
DEFAULT_DB = "data/warehouse/boxoffice_research_v2.duckdb"


def _json(payload: Any) -> bytes:
    return json.dumps(payload, default=str).encode("utf-8")


class TerminalApp:
    """WSGI-style dispatcher over the read models (no sockets, testable)."""

    def __init__(self, conn, *, deepseek: Any = None, llm: Any = None) -> None:
        self.conn = conn
        self.deepseek = deepseek
        self.llm = llm

    def dispatch(self, method: str, path: str, query: str = "", body: bytes = b"") -> dict[str, Any]:
        parts = [unquote(p) for p in path.split("/") if p]
        params = {k: v[0] for k, v in parse_qs(query).items()}

        # ---- static SPA ------------------------------------------------
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])

        # ---- API --------------------------------------------------------
        if path == "/api/search":
            return self._ok(readmodels.search_entities(
                self.conn, params.get("q", ""), int(params.get("limit", 25))))
        if path == "/api/tape":
            return self._ok(readmodels.query_tape(
                self.conn,
                entity_type=params.get("entity_type"),
                market_id=params.get("market_id"),
                activity_type=params.get("activity_type"),
                limit=int(params.get("limit", 100)),
            ))
        if path == "/api/sources":
            return self._ok(readmodels.get_sources(self.conn))
        if path == "/api/festivals":
            return self._ok(FestivalSpineRepository(self.conn).list_festivals())

        if path == "/api/ask" and method == "POST":
            try:
                q = json.loads(body.decode("utf-8")).get("question", "")
            except Exception:
                q = ""
            return self._ok(ask_answer(self.conn, q, deepseek=self.deepseek, llm=self.llm))

        # ---- entity routes ---------------------------------------------
        if len(parts) >= 2 and parts[0] == "api":
            entity_type = parts[1]
            entity_id = parts[2] if len(parts) >= 3 else None
            if entity_id is None:
                return self._missing()
            sub = parts[3] if len(parts) >= 4 else None

            if entity_type == "artists":
                return self._entity_artist(entity_id, sub)
            if entity_type == "events":
                return self._entity_event(entity_id, sub)
            if entity_type == "venues":
                return self._entity_venue(entity_id, sub)
            if entity_type == "markets":
                return self._entity_market(entity_id, sub)
            if entity_type == "festivals":
                if sub == "editions" and len(parts) >= 5:
                    return self._ok(readmodels.get_festival_edition(self.conn, parts[4]))
                return self._ok(readmodels.get_festival(self.conn, entity_id))

        return self._not_found()

    # -- entity sub-routes ------------------------------------------------
    def _entity_artist(self, entity_id: str, sub: str | None) -> dict[str, Any]:
        artist = readmodels.get_artist(self.conn, entity_id)
        if artist is None:
            return self._not_found()
        if sub == "events":
            return self._ok({"history": artist["history"], "upcoming": artist["upcoming"]})
        if sub == "history":
            return self._ok(artist["history"])
        if sub == "attention":
            return self._ok(artist["attention"])
        if sub == "news":
            return self._ok(artist["news"])
        if sub == "billing":
            return self._ok(readmodels.get_artist_billing_trajectory(self.conn, artist["name"]))
        if sub == "co-occurrence":
            return self._ok(readmodels.get_artist_co_occurrence(self.conn, artist["name"]))
        return self._ok(artist)

    def _entity_event(self, entity_id: str, sub: str | None) -> dict[str, Any]:
        event = readmodels.get_event(self.conn, entity_id)
        if event is None:
            return self._not_found()
        if sub == "timeline":
            return self._ok(event.get("timeline", []))
        if sub == "competition":
            return self._ok(event.get("competition", []))
        if sub == "evidence":
            return self._ok(event.get("evidence", []))
        return self._ok(event)

    def _entity_venue(self, entity_id: str, sub: str | None) -> dict[str, Any]:
        venue = readmodels.get_venue(self.conn, entity_id)
        if venue is None:
            return self._not_found()
        if sub == "calendar":
            return self._ok({"history": venue["history"], "upcoming": venue["upcoming"]})
        if sub == "history":
            return self._ok(venue["history"])
        return self._ok(venue)

    def _entity_market(self, entity_id: str, sub: str | None) -> dict[str, Any]:
        market = readmodels.get_market(self.conn, entity_id)
        if market is None:
            return self._not_found()
        if sub == "calendar":
            return self._ok(market["upcoming"])
        if sub == "profile":
            return self._ok(market["profile"])
        return self._ok(market)

    # -- helpers ----------------------------------------------------------
    def _ok(self, payload: Any) -> dict[str, Any]:
        return {"status": 200, "headers": {"Content-Type": "application/json"}, "body": _json(payload)}

    def _static(self, name: str) -> dict[str, Any]:
        safe = re.sub(r"[^A-Za-z0-9_.\-/]", "", name)
        fp = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(fp):
            return self._not_found()
        ctype = "text/html" if fp.endswith(".html") else "text/css" if fp.endswith(".css") else "application/javascript"
        with open(fp, "rb") as fh:
            data = fh.read()
        return {"status": 200, "headers": {"Content-Type": ctype}, "body": data}

    def _not_found(self) -> dict[str, Any]:
        return {"status": 404, "headers": {"Content-Type": "application/json"},
                "body": _json({"error": "not found"})}

    def _missing(self) -> dict[str, Any]:
        return {"status": 400, "headers": {"Content-Type": "application/json"},
                "body": _json({"error": "missing entity id"})}


class _Handler(BaseHTTPRequestHandler):
    app: TerminalApp

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

    def log_message(self, *args: Any) -> None:
        pass  # quiet; the transcript is the UI


def make_app(db_path: str = DEFAULT_DB) -> TerminalApp:
    load_local_env()
    repo = FestivalRepository(db_path)
    conn = repo.conn
    FlywheelRepository(conn)   # apply pending migrations (intelligence schema)
    ResearchRepository(conn)
    deepseek = DeepSeekAskClient(api_key=os.environ.get("DEEPSEEK_API_KEY"))
    llm = NimClient()          # NVIDIA NIM (fail-closed without a key)
    app = TerminalApp(conn, deepseek=deepseek, llm=llm)
    app._repo = repo  # keep alive; close on shutdown
    return app


def serve(app: TerminalApp, port: int) -> None:
    handler = type("BoundHandler", (_Handler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Festival Intelligence terminal: http://127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Festival Intelligence terminal")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args()
    serve(make_app(args.db), args.port)


if __name__ == "__main__":
    main()
