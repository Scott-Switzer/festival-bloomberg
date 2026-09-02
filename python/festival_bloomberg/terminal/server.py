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
from contextlib import nullcontext
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
from ..planning.competitive_calendar import calendar_for_proposed_show
from ..planning.proposed_show import (
    buyer_decision_view,
    compare_proposals,
    create_proposed_show,
    get_proposed_show,
    get_revision,
    list_proposed_shows,
    list_revisions,
)
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
from . import artist_security, storage
from .connections import CompatibilityRequestLock, wrap_connection

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
        artist_security_conn=None,
        deepseek: Any = None,
        llm: Any = None,
    ) -> None:
        # Production callers pass separate file-backed connections.  Each is
        # wrapped so a request thread owns its own DuckDB connection.  Tests
        # commonly pass one in-memory connection for both roles; retain a
        # serialized compatibility mode because that database cannot be opened
        # by another thread/path.
        shared_workspace = workspace_conn is None or workspace_conn is conn
        if shared_workspace:
            shared = wrap_connection(conn, read_only=False)
            self.conn = shared
            self.workspace_conn = shared
        else:
            self.conn = wrap_connection(conn, read_only=True)
            self.workspace_conn = wrap_connection(workspace_conn, read_only=False)
        self.artist_security_conn = (
            wrap_connection(artist_security_conn, read_only=True)
            if artist_security_conn is not None else None
        )
        self.deepseek = deepseek
        self.llm = llm
        self._workspace_write_lock = threading.RLock()
        self._compatibility_lock = CompatibilityRequestLock()
        self._connections = tuple(
            dict.fromkeys(
                connection for connection in (
                    self.conn, self.workspace_conn, self.artist_security_conn
                ) if connection is not None
            )
        )
        self._compatibility_mode = any(
            getattr(connection, "compatibility_mode", False)
            for connection in self._connections
        )

    def dispatch(self, method: str, path: str, query: str = "", body: bytes = b"") -> dict[str, Any]:
        """Dispatch one request without serializing independent file-backed reads.

        File-backed serving reads use thread-local read-only connections.  Only
        workspace mutation routes take ``_workspace_write_lock``.  The
        compatibility lock is intentionally limited to injected in-memory
        fixtures, whose single DuckDB connection cannot be safely shared.
        """
        compatibility = self._compatibility_lock if self._compatibility_mode else nullcontext()
        try:
            with compatibility:
                if self._is_workspace_mutation(method, path):
                    with self._workspace_write_lock:
                        return self._dispatch_locked(method, path, query, body)
                return self._dispatch_locked(method, path, query, body)
        finally:
            for connection in self._connections:
                connection.release_current()

    @staticmethod
    def _is_workspace_mutation(method: str, path: str) -> bool:
        # Monitor baselines are intentionally persisted on GET. Serializing
        # this one endpoint prevents two simultaneous first-look requests from
        # racing their baseline insert/update transaction.
        if path == "/api/monitor":
            return True
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return False
        if path == "/api/watchlists" or path.startswith("/api/watchlists/"):
            return True
        if path in {"/api/planning/projects", "/api/planning/seed"}:
            return method == "POST"
        if not path.startswith("/api/planning/projects/"):
            return False
        segments = path[len("/api/planning/projects/"):].split("/")
        sub = segments[1] if len(segments) > 1 else None
        if sub in {"stages", "candidates", "shortlist", "scenarios", "proposed-shows"}:
            return method == "POST"
        # Economics calculate/compare/prefill are read-only computations even
        # though calculate/compare use POST; saving a scenario is a mutation.
        return sub == "economics" and len(segments) == 2 and method == "POST"

    def close(self) -> None:
        """Close all owned terminal connections."""
        for connection in self._connections:
            connection.close()

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
            limit = int(params.get("limit", 25))
            if self.artist_security_conn is not None:
                product_hits = artist_security.search_artists(
                    self.artist_security_conn, params.get("q", ""), limit
                )
                # The buyer flow is artist-first. Fall back to broader entity
                # search only when the compact 25K product has no artist hit.
                if product_hits:
                    return self._ok(product_hits)
            return self._ok(readmodels.search_entities(
                self.conn, params.get("q", ""), limit))
        if path == "/api/artist-security/search":
            if self.artist_security_conn is None:
                return self._service_unavailable("ARTIST_SECURITY_SERVING_MISSING")
            return self._ok(artist_security.search_artists(
                self.artist_security_conn,
                params.get("q", ""),
                int(params.get("limit", 25)),
            ))
        if path == "/api/artist-security/compare":
            if self.artist_security_conn is None:
                return self._service_unavailable("ARTIST_SECURITY_SERVING_MISSING")
            left = params.get("a")
            right = params.get("b")
            if not left or not right:
                return self._bad_request("compare requires a and b artist keys")
            comparison = artist_security.compare_artists(
                self.artist_security_conn, left, right
            )
            return self._ok(comparison) if comparison is not None else self._not_found()
        if path.startswith("/api/artist-security/"):
            if self.artist_security_conn is None:
                return self._service_unavailable("ARTIST_SECURITY_SERVING_MISSING")
            artist_key = unquote(path[len("/api/artist-security/"):])
            payload = artist_security.get_artist_security(
                self.artist_security_conn, artist_key
            )
            if payload is not None:
                self._enrich_artist_markets(payload)
            return self._ok(payload) if payload is not None else self._not_found()
        if path == "/api/tape":
            return self._ok(readmodels.query_tape(
                self.conn,
                entity_type=params.get("entity_type"),
                market_id=params.get("market_id"),
                activity_type=params.get("activity_type"),
                limit=int(params.get("limit", 100)),
            ))
        if path == "/api/monitor":
            return self._ok(self._monitor())
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
            if sub == "competitive-calendar":
                project = planning_repo.get_project(self.workspace_conn, pkey) or {}
                market = project.get("market") or ""
                state = params.get("state") or (
                    market.split(",")[1].strip() if market and "," in market else None
                )
                lat = float(params["lat"]) if params.get("lat") else None
                lon = float(params["lon"]) if params.get("lon") else None
                return self._ok(calendar_for_proposed_show(
                    self.conn,
                    city=params.get("city") or project.get("city"),
                    state_code=state,
                    date=params.get("date") or project.get("start_date"),
                    venue_name=params.get("venue_name") or project.get("venue_site"),
                    venue_id=params.get("venue_id"),
                    lat=lat,
                    lon=lon,
                    research_cutoff=params.get("research_cutoff"),
                ))
            if sub == "proposed-shows" and method == "POST":
                try:
                    b = json.loads(body.decode("utf-8"))
                except Exception:
                    b = {}
                return self._ok(create_proposed_show(
                    self.workspace_conn,
                    project_key=pkey,
                    artist_name=b.get("artist_name", ""),
                    artist_key=b.get("artist_key"),
                    musicbrainz_id=b.get("musicbrainz_id"),
                    market=b.get("market", ""),
                    city=b.get("city"),
                    state_code=b.get("state_code"),
                    venue_key=b.get("venue_key"),
                    venue_name=b.get("venue_name"),
                    venue_configuration=b.get("venue_configuration"),
                    proposed_date=b.get("proposed_date", ""),
                    deal_type=b.get("deal_type"),
                    artist_guarantee=b.get("artist_guarantee"),
                    backend_percentage=b.get("backend_percentage"),
                    backend_basis=b.get("backend_basis"),
                    deal_provenance=b.get("deal_provenance", "USER_ASSUMPTION"),
                    guarantee_provenance=b.get("guarantee_provenance", "USER_ASSUMPTION"),
                    backend_provenance=b.get("backend_provenance", "USER_ASSUMPTION"),
                    decision_cutoff=b.get("decision_cutoff"),
                    research_cutoff=b.get("research_cutoff"),
                    notes=b.get("notes"),
                ))
            if sub == "proposed-shows":
                return self._ok(list_proposed_shows(self.workspace_conn, pkey))
            if sub == "buyer-decision":
                return self._ok(buyer_decision_view(
                    self.conn, self.workspace_conn,
                    proposed_show_key=params.get("show", ""),
                    evidence_conn=getattr(self, "evidence_conn", None),
                ))
            if sub == "compare-proposals" and method == "POST":
                try:
                    b = json.loads(body.decode("utf-8"))
                except Exception:
                    b = {}
                return self._ok(compare_proposals(
                    self.conn, self.workspace_conn,
                    proposed_show_keys=b.get("proposed_show_keys", []),
                    project_key=pkey,
                    scenario_keys=b.get("scenario_keys"),
                ))
            if sub == "revisions":
                show_key = params.get("show", "")
                if show_key:
                    return self._ok(list_revisions(self.workspace_conn, show_key))
                else:
                    return self._bad_request("Missing 'show' parameter")
            if sub == "revision":
                scenario_key = params.get("key", "")
                if scenario_key:
                    rev = get_revision(self.workspace_conn, scenario_key)
                    return self._ok(rev) if rev else self._not_found()
                else:
                    return self._bad_request("Missing 'key' parameter")
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
    def _market_forms(self, slug: str) -> tuple[str, str, list[str]]:
        """Derive evidence-consistent match forms for a slug market key."""
        s = str(slug).lower()
        parts = [p for p in s.split("-") if p]
        city_prefix = " ".join(parts[:-1]) if len(parts) > 1 else s
        forms = [s, city_prefix, f"{city_prefix}, %", f"{city_prefix} (%", f"{city_prefix} %"]
        return s, city_prefix, forms

    def _enrich_artist_markets(self, payload: dict[str, Any]) -> None:
        """Join per-market forward evidence (serving snapshot) into the artist
        security market rows. Only real forward observations are used; markets
        with none keep UNKNOWN instead of a fabricated zero."""
        markets = (payload.get("markets") or {}).get("items") or []
        if not markets or self.conn is None:
            return
        artist_name = (payload.get("artist") or {}).get("name")
        if not artist_name:
            return
        for m in markets:
            slug = m.get("market_key") or m.get("market") or m.get("market_name")
            if not slug:
                continue
            s, city_prefix, _forms = self._market_forms(slug)
            try:
                row = self.conn.execute(
                    """
                    SELECT count(*), min(event_date)
                    FROM flywheel.forward_watch_events
                    WHERE lower(artist_name) = ?
                      AND (lower(market) = ? OR lower(market) LIKE ? || ' (%'
                           OR lower(market) LIKE ? || ', %' OR lower(market) LIKE ? || ' %')
                      AND event_date >= CURRENT_DATE
                    """,
                    [artist_name.lower(), s, city_prefix, city_prefix, city_prefix],
                ).fetchone()
            except Exception:
                row = None
            if row and row[0]:
                m["future_events"] = int(row[0])
                m["next_event_date"] = str(row[1])[:10] if row[1] else None
            next_row = None
            try:
                next_row = self.conn.execute(
                    """
                    SELECT event_date, venue_name FROM flywheel.forward_watch_events
                    WHERE lower(artist_name) = ?
                      AND (lower(market) = ? OR lower(market) LIKE ? || ' (%'
                           OR lower(market) LIKE ? || ', %' OR lower(market) LIKE ? || ' %')
                      AND event_date >= CURRENT_DATE ORDER BY event_date LIMIT 1
                    """,
                    [artist_name.lower(), s, city_prefix, city_prefix, city_prefix],
                ).fetchone()
            except Exception:
                next_row = None
            if next_row:
                m["next_event"] = {"date": str(next_row[0])[:10], "venue": next_row[1]}

    def _monitor(self) -> dict[str, Any]:
        """Buyer monitor read model for TODAY: real watched/shortlisted state,
        upcoming forward events for those artists, and observed attention
        movement — no synthetic trending."""
        out: dict[str, Any] = {"contract_version": "terminal_monitor_v1"}
        watched: list[dict[str, Any]] = []
        try:
            from ..product.workflow import list_watchlists, list_watchlist_items
            for wl in list_watchlists(self.workspace_conn):
                for item in list_watchlist_items(self.workspace_conn, wl["watchlist_key"]):
                    if item.get("entity_type") == "ARTIST":
                        watched.append({
                            "artist_key": item.get("entity_key"),
                            "artist_name": item.get("entity_name"),
                            "watchlist": wl.get("name"),
                        })
        except Exception:
            pass
        shortlisted: list[dict[str, Any]] = []
        try:
            from ..planning import repository as planning_repo
            for project in planning_repo.list_projects(self.workspace_conn):
                for s in planning_repo.list_shortlists(self.workspace_conn, project["project_key"]):
                    if str(s.get("status") or "").upper() in ("SHORTLIST", "CANDIDATE", "UNDER_REVIEW"):
                        shortlisted.append({
                            "artist_key": s.get("artist_key"),
                            "artist_name": s.get("artist_name"),
                            "project": project.get("name"),
                            "project_key": project.get("project_key"),
                            "status": s.get("status"),
                        })
        except Exception:
            pass
        out["watched"] = watched[:40]
        out["shortlisted"] = shortlisted[:40]

        # Upcoming forward events for watched + shortlisted artist names.
        names = sorted({
            str(i.get("artist_name") or "").lower()
            for i in (out["watched"] + out["shortlisted"]) if i.get("artist_name")
        })
        upcoming: list[dict[str, Any]] = []
        if names and self.conn is not None:
            try:
                rows = self.conn.execute(
                    """
                    SELECT artist_name, venue_name, market, event_date, event_status
                    FROM flywheel.forward_watch_events
                    WHERE lower(artist_name) IN (?) AND event_date >= CURRENT_DATE
                    ORDER BY event_date LIMIT 60
                    """,
                    [names],
                ).fetchall()
                upcoming = [
                    {"artist_name": r[0], "venue": r[1], "market": r[2],
                     "date": str(r[3])[:10], "status": r[4]} for r in rows
                ]
            except Exception:
                upcoming = []
        out["upcoming"] = upcoming

        # Observed attention movement: artists with a prior observation of the
        # same weekly metric whose value actually changed.
        movers: list[dict[str, Any]] = []
        if self.artist_security_conn is not None:
            try:
                rows = self.artist_security_conn.execute(
                    """
                    WITH pairs AS (
                        SELECT artist_key, metric_kind, period_end, value_sum,
                               (period_end - period_start) AS span,
                               LAG(value_sum) OVER (
                                   PARTITION BY artist_key, metric_kind, (period_end - period_start)
                                   ORDER BY period_end
                               ) AS prior_value
                        FROM attention_observations
                        WHERE metric_kind = 'LISTENBRAINZ_LISTEN_COUNT' AND value_sum IS NOT NULL
                          AND period_end IS NOT NULL AND period_start IS NOT NULL
                    )
                    SELECT p.artist_key, a.name, p.period_end, p.value_sum, p.prior_value,
                           p.value_sum - p.prior_value AS change
                    FROM pairs p JOIN artists a USING (artist_key)
                    WHERE p.prior_value IS NOT NULL AND p.value_sum <> p.prior_value
                    ORDER BY abs(p.value_sum - p.prior_value) DESC LIMIT 15
                    """
                ).fetchall()
                movers = [
                    {"artist_key": r[0], "artist_name": r[1], "as_of": str(r[2])[:10],
                     "value": r[3], "prior": r[4], "change": r[5]} for r in rows
                ]
            except Exception:
                movers = []
        out["attention_movers"] = movers
        return out

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
        # Resolve upcoming artist names to 25K artist keys so the market page
        # links into Artist Security like every other entity reference.
        if self.artist_security_conn is not None:
            for row in market.get("upcoming", []):
                name = row.get("artist_name")
                if not name:
                    continue
                hits = artist_security.search_artists(self.artist_security_conn, name, 1)
                if hits:
                    row["artist_key"] = hits[0].get("entity_id")
                    row["artist_name"] = hits[0].get("name") or row["artist_name"]
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
        return {"status": 200, "headers": {"Content-Type": ctype, "Cache-Control": "no-store"}, "body": data}

    def _not_found(self) -> dict[str, Any]:
        return {"status": 404, "headers": {"Content-Type": "application/json"},
                "body": _json({"error": "not found"})}

    def _missing(self) -> dict[str, Any]:
        return {"status": 400, "headers": {"Content-Type": "application/json"},
                "body": _json({"error": "missing entity id"})}

    def _bad_request(self, message: str) -> dict[str, Any]:
        return {"status": 400, "headers": {"Content-Type": "application/json"},
                "body": _json({"error": message})}

    def _service_unavailable(self, message: str) -> dict[str, Any]:
        return {"status": 503, "headers": {"Content-Type": "application/json"},
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
    artist_security_db: str = artist_security.DEFAULT_PRODUCT_DB,
) -> TerminalApp:
    """Build the terminal from a serving snapshot + workspace sidecar.

    The canonical warehouse is NEVER opened here: read models read the serving
    snapshot (read-only) and mutable analyst state writes to the workspace DB.
    """
    load_local_env()
    serving_conn = storage.open_serving_snapshot(serving_db)
    workspace_conn = storage.create_workspace_db(workspace_db)
    security_conn = None
    if os.path.isfile(artist_security_db):
        security_conn = artist_security.open_product_db(artist_security_db)
    deepseek = DeepSeekAskClient(api_key=os.environ.get("DEEPSEEK_API_KEY"))
    llm = NimClient()          # NVIDIA NIM (fail-closed without a key)
    app = TerminalApp(
        serving_conn,
        workspace_conn,
        artist_security_conn=security_conn,
        deepseek=deepseek,
        llm=llm,
    )
    app._serving_conn = serving_conn
    app._workspace_conn = workspace_conn
    app._artist_security_conn = security_conn
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
    parser.add_argument("--artist-security-db", default=artist_security.DEFAULT_PRODUCT_DB)
    args = parser.parse_args()
    serve(
        make_app(args.serving_db, args.workspace_db, args.artist_security_db),
        args.port,
    )


if __name__ == "__main__":
    main()
