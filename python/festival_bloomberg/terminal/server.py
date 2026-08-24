"""Terminal server: JSON read models + the static SPA over a serving snapshot.

Storage roles: the terminal reads a READ-ONLY serving snapshot (published from
canonical) and writes mutable analyst state (watchlists, monitors, planning
projects) to a separate WORKSPACE DB. It NEVER opens the canonical research
warehouse, so canonical ingestion can write it while the terminal stays online.

Run locally::

    python -m festival_bloomberg.terminal.server [--port 8931]
        [--serving-db path] [--workspace-db path]

No credentials, no arbitrary SQL, no live provider calls per render.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..festivals.repository import FestivalSpineRepository
from ..intelligence import readmodels
from ..product.workflow import (
    add_watchlist_item, build_today, create_watchlist, list_alerts,
    list_monitors, list_watchlist_items, list_watchlists, remove_watchlist_item,
)
from ..intelligence.ask import answer as ask_answer
from ..intelligence.ask import DeepSeekAskClient
from ..intelligence.llm import NimClient
from ..localenv import load_local_env
from ..planning import candidates as planning_candidates
from ..planning import repository as planning_repo
from ..planning import scenario as planning_scenario
from ..economics.show_economics import scenario_from_dict
from ..economics.show_economics_product import (
    PRIVATE_DATA_READINESS,
    calculate_workbench,
    capacity_prefill,
    compare_saved_scenarios,
)
from ..economics.show_economics_repository import (
    duplicate_show_economics_scenario,
    list_show_economics_revisions,
    list_show_economics_scenarios,
    load_show_economics_scenario,
    save_show_economics_scenario,
)
from . import storage

STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "apps", "terminal", "static",
)

DEFAULT_PORT = 8931


def _json(payload: Any) -> bytes:
    return json.dumps(payload, default=str).encode("utf-8")


def _count(conn, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def _body_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _public_scenario(record: dict[str, Any]) -> dict[str, Any]:
    """Remove the in-process dataclass while preserving its JSON contract."""
    return {key: value for key, value in record.items() if key != "scenario"}


def _data_coverage(conn, workspace_conn) -> dict[str, Any]:
    """DATA control-panel payload: coverage, attention, providers, quality.

    ``conn`` is the serving snapshot (evidence/system data); ``workspace_conn``
    holds mutable analyst state (watchlists, monitors)."""
    return {
        "identity": {
            "canonical_artists": _count(conn, "SELECT COUNT(*) FROM core.artists"),
            "artists_with_mbid": _count(conn, "SELECT COUNT(*) FROM core.artists WHERE musicbrainz_id IS NOT NULL"),
            "artists_with_isni": _count(conn, "SELECT COUNT(*) FROM core.artists WHERE isni IS NOT NULL"),
            "artists_with_wikidata": _count(conn, "SELECT COUNT(DISTINCT entity_key) FROM core.entity_external_ids WHERE id_type='wikidata'"),
            "artists_with_youtube": _count(conn, "SELECT COUNT(DISTINCT entity_key) FROM core.entity_external_ids WHERE id_type='youtube'"),
            "artists_with_spotify": _count(conn, "SELECT COUNT(DISTINCT entity_key) FROM core.entity_external_ids WHERE id_type='spotify'"),
            "external_ids_total": _count(conn, "SELECT COUNT(*) FROM core.entity_external_ids"),
        },
        "reference": {
            "artists": _count(conn, "SELECT COUNT(*) FROM reference.musicbrainz_artists"),
            "areas": _count(conn, "SELECT COUNT(*) FROM reference.musicbrainz_areas"),
            "events": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_event"),
            "places": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_place"),
            "series": _count(conn, "SELECT COUNT(*) FROM core.event_series"),
            "performers": _count(conn, "SELECT COUNT(*) FROM core.event_performers"),
            "relationships": _count(conn, "SELECT COUNT(*) FROM core.entity_relationships"),
        },
        "attention": {
            "listenbrainz_rows": _count(conn, "SELECT COUNT(*) FROM metrics.artist_attention_observations WHERE source_system='listenbrainz'"),
            "listenbrainz_artists": _count(conn, "SELECT COUNT(DISTINCT artist_key) FROM metrics.artist_attention_observations WHERE source_system='listenbrainz'"),
            "wikimedia_rows": _count(conn, "SELECT COUNT(*) FROM metrics.artist_attention_observations WHERE source_system='wikimedia'"),
        },
        "product": {
            "watchlists": _count(workspace_conn, "SELECT COUNT(*) FROM core.watchlists"),
            "watchlist_items": _count(workspace_conn, "SELECT COUNT(*) FROM core.watchlist_items WHERE removed_at IS NULL"),
            "monitors": _count(workspace_conn, "SELECT COUNT(*) FROM terminal.saved_monitors"),
            "alerts": _count(conn, "SELECT COUNT(*) FROM core.alerts WHERE status='ACTIVE'"),
            "tm_resolutions": _count(conn, "SELECT COUNT(*) FROM identity.ticketmaster_artist_resolutions"),
            "deprecated_columns": _count(conn, "SELECT COUNT(*) FROM core.deprecated_columns"),
        },
        "live": {
            "tm_snapshots": _count(conn, "SELECT COUNT(*) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"),
            "tm_events": _count(conn, "SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"),
        },
        "resolutions": {
            "total": _count(conn, "SELECT COUNT(*) FROM identity.ticketmaster_artist_resolutions"),
            "by_status": dict(conn.execute(
                "SELECT resolution_status, COUNT(*) FROM identity.ticketmaster_artist_resolutions GROUP BY 1").fetchall()),
        },
    }


class TerminalApp:
    """WSGI-style dispatcher over the read models (no sockets, testable).

    ``conn`` is the serving snapshot (read models). ``workspace_conn`` holds
    mutable analyst state (watchlists, monitors, planning projects). When
    ``workspace_conn`` is omitted (tests), both roles share ``conn``.
    """

    def __init__(
        self,
        conn,
        workspace_conn=None,
        *,
        deepseek: Any = None,
        llm: Any = None,
    ) -> None:
        self.conn = conn
        self.workspace_conn = workspace_conn if workspace_conn is not None else conn
        self.deepseek = deepseek
        self.llm = llm
        # DuckDB connections are not thread-safe; ThreadingHTTPServer serves
        # concurrent requests, so serialize every dispatch on one lock.
        self._lock = threading.Lock()

    def dispatch(self, method: str, path: str, query: str = "", body: bytes = b"") -> dict[str, Any]:
        with self._lock:
            return self._dispatch_locked(method, path, query, body)

    def _dispatch_locked(self, method: str, path: str, query: str = "", body: bytes = b"") -> dict[str, Any]:
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
        if path == "/api/status":
            return self._ok(readmodels.get_recent_changes(
                self.conn, int(params.get("limit", 100))))
        if path == "/api/news":
            return self._ok(readmodels.get_recent_news(
                self.conn, int(params.get("limit", 100))))
        if path == "/api/attention":
            return self._ok(readmodels.get_attention_coverage(
                self.conn, int(params.get("limit", 100))))
        if path == "/api/events/live":
            return self._ok(readmodels.get_live_events(
                self.conn, market=params.get("market"), limit=int(params.get("limit", 100))))
        if path == "/api/festivals":
            return self._ok(FestivalSpineRepository(self.conn).list_festivals())
        if path == "/api/tours":
            return self._ok(readmodels.list_tours(
                self.conn, market=params.get("market"),
                limit=int(params.get("limit", 100))))

        # ---- product workflow -------------------------------------------
        if path == "/api/today":
            return self._ok(build_today(
                self.conn, self.workspace_conn, limit=int(params.get("limit", 50))))
        if path == "/api/watchlists" and method == "POST":
            try:
                body = json.loads(body.decode("utf-8"))
            except Exception:
                body = {}
            return self._ok(create_watchlist(
                self.workspace_conn, name=body.get("name", ""),
                description=body.get("description"),
                entity_type=body.get("entity_type"),
                is_system=bool(body.get("is_system", False))))
        if path == "/api/watchlists" and method == "GET":
            return self._ok(list_watchlists(self.workspace_conn))
        if path == "/api/monitors":
            return self._ok(list_monitors(self.workspace_conn))
        if path == "/api/alerts":
            return self._ok(list_alerts(
                self.conn, limit=int(params.get("limit", 100)),
                entity_key_value=params.get("entity_key")))
        if path == "/api/data":
            return self._ok(_data_coverage(self.conn, self.workspace_conn))
        if path == "/api/coverage":
            from ..intelligence.coverage_voi import coverage_dashboard
            return self._ok(coverage_dashboard(self.conn))
        if path == "/api/voi":
            from ..intelligence.coverage_voi import voi_ranking
            return self._ok(voi_ranking(self.conn))
        if path == "/api/venues/coverage":
            from ..intelligence.venue_intel import venue_coverage
            return self._ok(venue_coverage(self.conn))
        # ---- planning workspace (talent-buyer workbench) ------------------
        if path == "/api/planning/projects" and method == "POST":
            try:
                body = json.loads(body.decode("utf-8"))
            except Exception:
                body = {}
            return self._ok(planning_repo.create_project(
                self.workspace_conn, name=body.get("name", ""),
                city=body.get("city"), market=body.get("market"),
                venue_site=body.get("venue_site"),
                start_date=body.get("start_date"), end_date=body.get("end_date"),
                num_days=body.get("num_days"), num_stages=body.get("num_stages"),
                talent_budget_usd=body.get("talent_budget_usd"),
                genre_objectives=body.get("genre_objectives"),
                target_audience=body.get("target_audience"),
                min_billing_tier=body.get("min_billing_tier"),
                max_billing_tier=body.get("max_billing_tier"),
                notes=body.get("notes"),
                scenario_class=body.get("scenario_class", "SYNTHETIC_PLANNING_SCENARIO")))
        if path == "/api/planning/projects":
            return self._ok(planning_repo.list_projects(self.workspace_conn))
        if path == "/api/planning/seed" and method == "POST":
            return self._ok(planning_repo.seed_synthetic_project(self.workspace_conn))
        if path == "/api/planning/scorecard":
            return self._ok(planning_candidates.artist_scorecard(
                self.conn, artist_key=params.get("artist_key"),
                artist_name=params.get("artist_name")))
        if path == "/api/planning/economics/readiness":
            return self._ok({"fields": PRIVATE_DATA_READINESS})
        if path.startswith("/api/planning/economics/"):
            rest = path[len("/api/planning/economics/"):]
            segs = rest.split("/")
            scenario_key = segs[0]
            action = segs[1] if len(segs) > 1 else None
            try:
                if action == "duplicate" and method == "POST":
                    b = _body_object(body)
                    return self._ok(_public_scenario(duplicate_show_economics_scenario(
                        self.workspace_conn,
                        source_scenario_key=scenario_key,
                        name=b.get("name", "Scenario copy"),
                    )))
                if action == "revisions" and method == "GET":
                    return self._ok(list_show_economics_revisions(
                        self.workspace_conn, scenario_key
                    ))
                if action is None and method == "GET":
                    return self._ok(_public_scenario(load_show_economics_scenario(
                        self.workspace_conn, scenario_key
                    )))
            except (KeyError, ValueError) as exc:
                return self._bad_request(str(exc))
        if path.startswith("/api/planning/projects/"):
            rest = path[len("/api/planning/projects/"):]
            segs = rest.split("/")
            pkey = segs[0]
            sub = segs[1] if len(segs) > 1 else None
            if sub is None:
                return self._ok(planning_repo.get_project(self.workspace_conn, pkey) or self._not_found())
            if sub == "stages" and method == "POST":
                try:
                    b = json.loads(body.decode("utf-8"))
                except Exception:
                    b = {}
                return self._ok(planning_repo.add_stage(
                    self.workspace_conn, project_key=pkey, stage_name=b.get("stage_name", ""),
                    capacity_claim=b.get("capacity_claim"),
                    capacity_evidence_class=b.get("capacity_evidence_class"),
                    indoor_outdoor=b.get("indoor_outdoor")))
            if sub == "candidates" and method == "POST":
                try:
                    b = json.loads(body.decode("utf-8"))
                except Exception:
                    b = {}
                if b.get("generate"):
                    return self._ok(planning_candidates.build_candidate_universe(
                        self.conn, self.workspace_conn, project_key=pkey,
                        market=b.get("market"), limit=int(b.get("limit", 200))))
                return self._ok(planning_repo.add_candidate(
                    self.workspace_conn, project_key=pkey, artist_key=b.get("artist_key"),
                    artist_name=b.get("artist_name", ""),
                    musicbrainz_id=b.get("musicbrainz_id"),
                    inclusion_reasons=b.get("inclusion_reasons"),
                    availability_status=b.get("availability_status", "UNKNOWN"),
                    availability_evidence=b.get("availability_evidence"),
                    scorecard_snapshot=b.get("scorecard_snapshot")))
            if sub == "candidates":
                return self._ok(planning_repo.list_candidates(self.workspace_conn, pkey))
            if sub == "shortlist" and method == "POST":
                try:
                    b = json.loads(body.decode("utf-8"))
                except Exception:
                    b = {}
                return self._ok(planning_repo.set_shortlist(
                    self.workspace_conn, project_key=pkey, artist_key=b.get("artist_key"),
                    artist_name=b.get("artist_name", ""), status=b.get("status", "DISCOVERED"),
                    candidate_day=b.get("candidate_day"),
                    candidate_stage=b.get("candidate_stage"),
                    candidate_billing_tier=b.get("candidate_billing_tier"),
                    notes=b.get("notes")))
            if sub == "shortlist":
                return self._ok(planning_repo.list_shortlists(self.workspace_conn, pkey))
            if sub == "scenarios" and method == "POST":
                try:
                    b = json.loads(body.decode("utf-8"))
                except Exception:
                    b = {}
                return self._ok(planning_scenario.persist_scenario(
                    self.workspace_conn, project_key=pkey, name=b.get("name", "Scenario"),
                    slots=b.get("slots", []), notes=b.get("notes")))
            if sub == "scenarios":
                return self._ok(planning_repo.list_scenarios(self.workspace_conn, pkey))
            if sub == "economics":
                action = segs[2] if len(segs) > 2 else None
                try:
                    if action == "calculate" and method == "POST":
                        b = _body_object(body)
                        return self._ok(calculate_workbench(
                            b.get("inputs", {}),
                            sensitivity_requests=b.get("sensitivities"),
                            boundary_request=b.get("boundary"),
                        ))
                    if action == "compare" and method == "POST":
                        b = _body_object(body)
                        records = [
                            load_show_economics_scenario(self.workspace_conn, key)
                            for key in b.get("scenario_keys", [])
                        ]
                        if any(record["project_key"] != pkey for record in records):
                            raise ValueError("all comparison scenarios must belong to this project")
                        return self._ok(compare_saved_scenarios(records))
                    if action == "prefill" and method == "GET":
                        return self._ok(capacity_prefill(
                            self.conn,
                            venue_key=params.get("venue", ""),
                            event_configuration=params.get("configuration"),
                        ))
                    if action is None and method == "POST":
                        b = _body_object(body)
                        scenario_key = b.get("scenario_key")
                        if scenario_key:
                            existing = load_show_economics_scenario(
                                self.workspace_conn, scenario_key
                            )
                            if existing["project_key"] != pkey:
                                raise ValueError(
                                    "show economics scenario does not belong to this project"
                                )
                        record = save_show_economics_scenario(
                            self.workspace_conn,
                            name=b.get("name", ""),
                            project_key=pkey,
                            scenario=scenario_from_dict(b.get("inputs", {})),
                            scenario_key=scenario_key,
                            identity_context=b.get("identity_context"),
                        )
                        return self._ok(_public_scenario(record))
                    if action is None and method == "GET":
                        return self._ok(list_show_economics_scenarios(
                            self.workspace_conn, project_key=pkey
                        ))
                except (KeyError, ValueError, TypeError) as exc:
                    return self._bad_request(str(exc))

        if path.startswith("/api/watchlists/") and method == "POST":
            try:
                body = json.loads(body.decode("utf-8"))
            except Exception:
                body = {}
            wl_key = path[len("/api/watchlists/"):]
            action = body.get("action")
            if action == "add":
                return self._ok({"added": add_watchlist_item(
                    self.workspace_conn, watchlist_key_value=wl_key,
                    entity_type=body.get("entity_type", ""),
                    entity_key_value=body.get("entity_key", ""),
                    entity_name=body.get("entity_name"),
                    notes=body.get("notes"), tags=body.get("tags"))})
            if action == "remove":
                return self._ok({"removed": remove_watchlist_item(
                    self.workspace_conn, watchlist_key_value=wl_key,
                    entity_type=body.get("entity_type", ""),
                    entity_key_value=body.get("entity_key", ""))})
            return self._ok(list_watchlist_items(self.workspace_conn, wl_key))
        if path.startswith("/api/watchlists/"):
            return self._ok(list_watchlist_items(
                self.workspace_conn, path[len("/api/watchlists/"):]))

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
            if entity_type == "tours":
                return self._ok(readmodels.get_tour(self.conn, entity_id))

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

    def _bad_request(self, message: str) -> dict[str, Any]:
        return {"status": 400, "headers": {"Content-Type": "application/json"},
                "body": _json({"error": message})}


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


def make_app(
    serving_db: str = storage.SERVING_DIR,
    workspace_db: str = storage.WORKSPACE_DEFAULT_DB,
) -> TerminalApp:
    """Build the terminal from a serving snapshot + workspace sidecar.

    The canonical warehouse is NEVER opened here: read models read the serving
    snapshot (read-only) and mutable analyst state writes to the workspace DB.
    """
    load_local_env()
    serving_conn = storage.open_serving_snapshot(serving_db)
    workspace_conn = storage.create_workspace_db(workspace_db)
    deepseek = DeepSeekAskClient(api_key=os.environ.get("DEEPSEEK_API_KEY"))
    llm = NimClient()          # NVIDIA NIM (fail-closed without a key)
    app = TerminalApp(serving_conn, workspace_conn, deepseek=deepseek, llm=llm)
    app._serving_conn = serving_conn
    app._workspace_conn = workspace_conn
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
    parser.add_argument("--serving-db", default=storage.SERVING_DIR)
    parser.add_argument("--workspace-db", default=storage.WORKSPACE_DEFAULT_DB)
    args = parser.parse_args()
    serve(make_app(args.serving_db, args.workspace_db), args.port)


if __name__ == "__main__":
    main()
