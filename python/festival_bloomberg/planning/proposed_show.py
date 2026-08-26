"""Unified proposed-show object for the Buyer Decision Workspace V2.

A proposed show is a first-class product object that organizes existing
evidence around one coherent underwriting unit:

    ARTIST x MARKET x DATE x VENUE x DEAL

Each section is assembled by calling into proven components (competitive calendar,
show economics, venue capacity, artist scorecard, comparable events) -- never by
reimplementing them.

Identity model:
    proposed_show_key = hash(project, artist, market, date, venue, deal_type)
    scenario_key = hash(proposed_show_key, revision_number, created_at)
    Old revisions are preserved in planning.proposed_show_revisions.

Evidence provenance:
    USER_ASSUMPTION != KNOWN. Presence of a non-null value does not automatically
    mean the field is known. If the user typed it, it's an assumption.

Error handling:
    Source absence may produce UNKNOWN.
    Programming errors (schema mismatch, wrong function signature) must produce
    ERROR or fail — never silently report UNKNOWN.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .competitive_calendar import calendar_for_proposed_show
from .candidates import artist_scorecard

EVIDENCE_KNOWN = "KNOWN"
EVIDENCE_ASSUMED = "ASSUMED"
EVIDENCE_UNKNOWN = "UNKNOWN"
EVIDENCE_CONFLICTING = "CONFLICTING"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _h(material: str, n: int = 32) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:n]


def _d(value: Any) -> date | None:
    if value is None:
        return None
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _ensure_schema(conn) -> None:
    """Ensure workspace schema tables exist (idempotent)."""
    conn.execute("CREATE SCHEMA IF NOT EXISTS planning")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS planning.proposed_shows (
            proposed_show_key VARCHAR PRIMARY KEY,
            project_key VARCHAR NOT NULL,
            artist_key VARCHAR, artist_name VARCHAR NOT NULL,
            musicbrainz_id VARCHAR, market VARCHAR NOT NULL,
            city VARCHAR, state_code VARCHAR,
            venue_key VARCHAR, venue_name VARCHAR,
            venue_configuration VARCHAR,
            proposed_date DATE NOT NULL,
            deal_type VARCHAR, artist_guarantee DOUBLE,
            backend_percentage DOUBLE, backend_basis VARCHAR,
            deal_provenance VARCHAR NOT NULL DEFAULT 'USER_ASSUMPTION',
            guarantee_provenance VARCHAR NOT NULL DEFAULT 'USER_ASSUMPTION',
            backend_provenance VARCHAR NOT NULL DEFAULT 'USER_ASSUMPTION',
            decision_cutoff TIMESTAMP, research_cutoff TIMESTAMP,
            current_revision INTEGER NOT NULL DEFAULT 1,
            notes VARCHAR,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS planning.proposed_show_revisions (
            scenario_key VARCHAR PRIMARY KEY,
            proposed_show_key VARCHAR NOT NULL,
            revision_number INTEGER NOT NULL,
            snapshot_json JSON NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes VARCHAR,
            FOREIGN KEY (proposed_show_key) REFERENCES planning.proposed_shows(proposed_show_key)
        )
    """)
    # Create index for fast revision lookups.
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_revisions_show
        ON planning.proposed_show_revisions(proposed_show_key, revision_number)
    """)


# ---------------------------------------------------------------------------
# CRUD — with correct identity (ARTIST x MARKET x DATE x VENUE x DEAL)
# ---------------------------------------------------------------------------
def _proposed_show_key(
    project_key: str,
    artist_name: str,
    market: str,
    proposed_date: str,
    venue_key: str | None = None,
    venue_name: str | None = None,
    deal_type: str | None = None,
) -> str:
    """Deterministic key including venue and deal dimensions.

    Two proposed shows with same artist/market/date but different venue
    or different deal type must NOT collide.
    """
    material = (
        f"psv2::{project_key}::{artist_name}::{market}::{proposed_date}"
        f"::{venue_key or ''}::{venue_name or ''}::{deal_type or ''}"
    )
    return _h(material)


def create_proposed_show(
    conn,
    *,
    project_key: str,
    artist_name: str,
    artist_key: str | None = None,
    musicbrainz_id: str | None = None,
    market: str = "",
    city: str | None = None,
    state_code: str | None = None,
    venue_key: str | None = None,
    venue_name: str | None = None,
    venue_configuration: str | None = None,
    proposed_date: str,
    deal_type: str | None = None,
    artist_guarantee: float | None = None,
    backend_percentage: float | None = None,
    backend_basis: str | None = None,
    deal_provenance: str = "USER_ASSUMPTION",
    guarantee_provenance: str = "USER_ASSUMPTION",
    backend_provenance: str = "USER_ASSUMPTION",
    decision_cutoff: str | None = None,
    research_cutoff: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create or update a proposed show. Immutable revision snapshots are
    preserved on each update.
    """
    _ensure_schema(conn)

    key = _proposed_show_key(
        project_key, artist_name, market, str(proposed_date)[:10],
        venue_key=venue_key, venue_name=venue_name, deal_type=deal_type,
    )

    existing = get_proposed_show(conn, key)
    new_revision = (existing.get("current_revision", 0) + 1) if existing else 1

    if existing:
        # Snapshot the current state before overwriting.
        conn.execute(
            """INSERT INTO planning.proposed_show_revisions
               (scenario_key, proposed_show_key, revision_number, snapshot_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            [
                _h(f"rev::{key}::{existing['current_revision']}::{existing.get('updated_at', '')}"),
                key,
                existing["current_revision"],
                json.dumps(_show_to_dict(existing), default=str),
                _now(),
            ],
        )

    conn.execute(
        """INSERT INTO planning.proposed_shows
           (proposed_show_key, project_key, artist_key, artist_name, musicbrainz_id,
            market, city, state_code, venue_key, venue_name, venue_configuration,
            proposed_date, deal_type, artist_guarantee, backend_percentage,
            backend_basis, deal_provenance, guarantee_provenance, backend_provenance,
            decision_cutoff, research_cutoff, current_revision, notes,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now(), now())
           ON CONFLICT (proposed_show_key) DO UPDATE SET
            artist_name = excluded.artist_name,
            musicbrainz_id = excluded.musicbrainz_id,
            venue_key = excluded.venue_key,
            venue_name = excluded.venue_name,
            venue_configuration = excluded.venue_configuration,
            deal_type = excluded.deal_type,
            artist_guarantee = excluded.artist_guarantee,
            backend_percentage = excluded.backend_percentage,
            backend_basis = excluded.backend_basis,
            deal_provenance = excluded.deal_provenance,
            guarantee_provenance = excluded.guarantee_provenance,
            backend_provenance = excluded.backend_provenance,
            decision_cutoff = excluded.decision_cutoff,
            research_cutoff = excluded.research_cutoff,
            current_revision = excluded.current_revision,
            notes = excluded.notes,
            updated_at = now()""",
        [
            key, project_key, artist_key, artist_name, musicbrainz_id,
            market, city, state_code, venue_key, venue_name, venue_configuration,
            proposed_date, deal_type, artist_guarantee, backend_percentage,
            backend_basis, deal_provenance, guarantee_provenance, backend_provenance,
            decision_cutoff, research_cutoff, new_revision, notes,
        ],
    )
    return get_proposed_show(conn, key)


def _show_to_dict(show: dict[str, Any]) -> dict[str, Any]:
    """Subset of show fields suitable for revision snapshots."""
    fields = [
        "proposed_show_key", "project_key", "artist_key", "artist_name",
        "musicbrainz_id", "market", "city", "state_code", "venue_key",
        "venue_name", "venue_configuration", "proposed_date", "deal_type",
        "artist_guarantee", "backend_percentage", "backend_basis",
        "deal_provenance", "guarantee_provenance", "backend_provenance",
        "decision_cutoff", "research_cutoff", "current_revision", "notes",
    ]
    return {k: show.get(k) for k in fields}


def list_proposed_shows(conn, project_key: str) -> list[dict[str, Any]]:
    _ensure_schema(conn)
    return _rows(
        conn,
        "SELECT * FROM planning.proposed_shows WHERE project_key = ? ORDER BY proposed_date, artist_name",
        [project_key],
    )


def get_proposed_show(conn, proposed_show_key: str) -> dict[str, Any] | None:
    _ensure_schema(conn)
    rows = _rows(
        conn, "SELECT * FROM planning.proposed_shows WHERE proposed_show_key = ?",
        [proposed_show_key],
    )
    return rows[0] if rows else None


def get_revision(conn, scenario_key: str) -> dict[str, Any] | None:
    """Retrieve an immutable revision snapshot by scenario_key."""
    _ensure_schema(conn)
    rows = _rows(
        conn,
        "SELECT * FROM planning.proposed_show_revisions WHERE scenario_key = ?",
        [scenario_key],
    )
    if not rows:
        return None
    rev = rows[0]
    snapshot = json.loads(rev.get("snapshot_json", "{}"))
    snapshot["scenario_key"] = rev["scenario_key"]
    snapshot["revision_number"] = rev["revision_number"]
    snapshot["revision_created_at"] = rev.get("created_at")
    return snapshot


def list_revisions(conn, proposed_show_key: str) -> list[dict[str, Any]]:
    """List all immutable revisions for a proposed show, oldest first."""
    _ensure_schema(conn)
    return _rows(
        conn,
        "SELECT scenario_key, revision_number, notes, created_at "
        "FROM planning.proposed_show_revisions "
        "WHERE proposed_show_key = ? ORDER BY revision_number",
        [proposed_show_key],
    )


# ---------------------------------------------------------------------------
# Evidence classification
# ---------------------------------------------------------------------------
def _classify(value: Any, provenance: str) -> str:
    """Classify a field value + its provenance into one evidence status.

    USER_ASSUMPTION is never KNOWN. A non-null value alone does not make
    a field KNOWN — provenance must indicate an external observation.
    """
    if value is None:
        return EVIDENCE_UNKNOWN
    if provenance in ("UNKNOWN", None, ""):
        return EVIDENCE_UNKNOWN
    if provenance == "USER_ASSUMPTION":
        return EVIDENCE_ASSUMED
    if provenance in ("OBSERVED_PUBLIC", "OBSERVED_PRIVATE", "DERIVED"):
        return EVIDENCE_KNOWN
    # Unknown provenance strings — don't silently promote to KNOWN.
    return EVIDENCE_UNKNOWN


# ---------------------------------------------------------------------------
# Venue coordinate resolution
# ---------------------------------------------------------------------------
def _resolve_venue_coordinates(
    serving_conn, venue_key: str | None
) -> dict[str, Any]:
    """Resolve a venue to its canonical coordinates for calendar geography."""
    if not venue_key:
        return {"lat": None, "lon": None, "city": None, "state_code": None}
    try:
        # Try canonical venue table first.
        rows = _rows(
            serving_conn,
            "SELECT latitude, longitude, city, state_code "
            "FROM events.venues WHERE venue_key = ? LIMIT 1",
            [venue_key],
        )
        if rows and rows[0].get("latitude") is not None:
            r = rows[0]
            return {
                "lat": float(r["latitude"]),
                "lon": float(r["longitude"]),
                "city": r.get("city"),
                "state_code": r.get("state_code"),
            }
    except Exception:
        pass
    # Fallback: venue_source_ids table.
    try:
        rows = _rows(
            serving_conn,
            "SELECT s.venue_name, s.latitude, s.longitude, s.city, s.state_code "
            "FROM economics.venue_source_ids s "
            "WHERE s.canonical_venue_id = ? LIMIT 1",
            [venue_key],
        )
        if rows and rows[0].get("latitude") is not None:
            r = rows[0]
            return {
                "lat": float(r["latitude"]),
                "lon": float(r["longitude"]),
                "city": r.get("city"),
                "state_code": r.get("state_code"),
            }
    except Exception:
        pass
    return {"lat": None, "lon": None, "city": None, "state_code": None}


# ---------------------------------------------------------------------------
# Buyer decision view (the Bloomberg-style dense page)
# ---------------------------------------------------------------------------
def buyer_decision_view(
    serving_conn,
    workspace_conn,
    *,
    proposed_show_key: str,
    evidence_conn=None,
) -> dict[str, Any]:
    """Assemble the full buyer decision view for one proposed show.

    Calls into existing components; never reimplements their logic.
    Programming errors are exposed as ERROR, never silently converted to UNKNOWN.

    evidence_conn (optional): read-only connection to the evidence estate
    (acquisition.ticket_market_snapshots / external_event_observations from
    migration 039). When omitted, the TICKET MARKET section reports UNKNOWN
    rather than failing the whole view.
    """
    _ensure_schema(workspace_conn)
    show = get_proposed_show(workspace_conn, proposed_show_key)
    if show is None:
        return {"status": "NOT_FOUND", "proposed_show_key": proposed_show_key}

    artist_name = show.get("artist_name") or ""
    artist_key = show.get("artist_key")
    venue_name = show.get("venue_name")
    venue_key = show.get("venue_key")
    proposed_date = show.get("proposed_date")
    city = show.get("city") or show.get("market")
    market = show.get("market")
    state_code = show.get("state_code")
    research_cutoff = (
        str(show["research_cutoff"])[:19] if show.get("research_cutoff") else None
    )
    deal_provenance = show.get("deal_provenance", "USER_ASSUMPTION")

    # Resolve venue coordinates for calendar geography.
    coords = _resolve_venue_coordinates(serving_conn, venue_key)

    view: dict[str, Any] = {
        "status": "OBSERVED",
        "proposed_show_key": proposed_show_key,
        # ---- 1. SHOW HEADER -----------------------------------------------
        "header": {
            "artist_name": artist_name,
            "artist_key": artist_key,
            "market": market,
            "city": city,
            "state_code": state_code,
            "venue_name": venue_name,
            "venue_key": venue_key,
            "venue_configuration": show.get("venue_configuration"),
            "proposed_date": str(proposed_date)[:10] if proposed_date else None,
            "deal_type": show.get("deal_type"),
            "artist_guarantee": show.get("artist_guarantee"),
            "guarantee_provenance": show.get("guarantee_provenance", "USER_ASSUMPTION"),
            "deal_provenance": deal_provenance,
            "backend_provenance": show.get("backend_provenance", "USER_ASSUMPTION"),
            "decision_cutoff": (
                str(show["decision_cutoff"])[:19]
                if show.get("decision_cutoff") else None
            ),
            "research_cutoff": research_cutoff,
            "current_revision": show.get("current_revision"),
        },
        # ---- 2. EVIDENCE STATUS -------------------------------------------
        "evidence_status": {
            EVIDENCE_KNOWN: [],
            EVIDENCE_ASSUMED: [],
            EVIDENCE_UNKNOWN: [],
            EVIDENCE_CONFLICTING: [],
        },
        # ---- 3. VENUE / CAPACITY ------------------------------------------
        "venue_capacity": _venue_section(
            serving_conn, venue_key, show.get("venue_configuration"),
        ),
        # ---- 4. COMPETITIVE CALENDAR --------------------------------------
        "competitive_calendar": _calendar_section(
            serving_conn, show, coords, research_cutoff,
        ),
        # ---- 5. COMPARABLE EVENTS -----------------------------------------
        "comparable_events": _comparable_section(serving_conn, artist_name),
        # ---- 6. ARTIST / ATTENTION CONTEXT --------------------------------
        "artist_context": _artist_section(serving_conn, artist_key, artist_name),
        # ---- 7. SHOW ECONOMICS --------------------------------------------
        "show_economics": _economics_section(workspace_conn, proposed_show_key),
        # ---- 8. TICKET MARKET (evidence rail) -----------------------------
        "ticket_market": _ticket_market_section(
            evidence_conn, show, proposed_show_key,
        ),
        # ---- 9. RISKS / WARNINGS ------------------------------------------
        "risks": [],
        # ---- 10. PROVENANCE -----------------------------------------------
        "provenance": _provenance_section(serving_conn, show),
    }

    # Derive risks and evidence status.
    view["risks"] = _derive_risks(view)
    view["evidence_status"] = _build_evidence_status(view, show)

    return view


# ---------------------------------------------------------------------------
# Section assemblers
# ---------------------------------------------------------------------------
def _venue_section(
    serving_conn, venue_key: str | None, configuration: str | None,
) -> dict[str, Any]:
    """Assemble venue/capacity evidence via the production capacity_prefill.

    Uses the exact same contract as the show-economics workbench:
    load CapacityClaim objects -> call assess_venue_claims.

    Programming errors (wrong function signature, schema mismatch) are exposed
    as ERROR. Source/data absence produces UNKNOWN.
    """
    if not venue_key:
        return {"status": "UNKNOWN", "reason": "no venue key provided"}

    try:
        from ..economics.show_economics_product import capacity_prefill
        return capacity_prefill(
            serving_conn,
            venue_key=venue_key,
            event_configuration=configuration,
        )
    except ImportError as e:
        return {"status": "ERROR", "reason": f"import failure: {e}"}
    except TypeError as e:
        # Wrong function signature — this is a programming error.
        return {"status": "ERROR", "reason": f"capacity_prefill signature mismatch: {e}"}
    except Exception as e:
        # Runtime errors from the capacity system.
        return {"status": "ERROR", "reason": f"capacity system error: {e}"}


def _calendar_section(
    serving_conn,
    show: dict[str, Any],
    coords: dict[str, Any],
    research_cutoff: str | None,
) -> dict[str, Any]:
    """Reuse competitive_calendar from PR #43 with full geography.

    Passes lat, lon, city, state_code, and venue_id so the calendar
    can compute same-venue / 5 / 10 / 25 / 50 mile distance buckets.
    """
    proposed_date = show.get("proposed_date")
    city = show.get("city")
    state_code = show.get("state_code")
    venue_name = show.get("venue_name")
    venue_id = show.get("venue_key")
    lat = coords.get("lat")
    lon = coords.get("lon")

    if not proposed_date:
        return {"status": "UNKNOWN", "reason": "no proposed date"}

    date_str = (
        str(proposed_date)[:10]
        if isinstance(proposed_date, date) else str(proposed_date)
    )
    try:
        return calendar_for_proposed_show(
            serving_conn,
            city=city or coords.get("city"),
            state_code=state_code or coords.get("state_code"),
            date=date_str,
            venue_name=venue_name,
            venue_id=venue_id,
            lat=lat,
            lon=lon,
            research_cutoff=research_cutoff,
        )
    except ImportError as e:
        return {"status": "ERROR", "reason": f"import failure: {e}"}
    except Exception as e:
        return {"status": "ERROR", "reason": f"calendar error: {e}"}


def _comparable_section(serving_conn, artist_name: str) -> dict[str, Any]:
    """Comparable-event context using the existing comparable engine."""
    if not artist_name:
        return {"status": "UNKNOWN", "reason": "no artist name"}
    try:
        card = artist_scorecard(serving_conn, artist_name=artist_name)
        comparables = card.get("comparables", {})
        market_history = card.get("market_history", {})
        return {
            "status": "OBSERVED",
            "gross": comparables.get("gross", {}),
            "attendance": comparables.get("attendance", {}),
            "market_history": market_history,
            "source": "boxoffice_research_corpus_v1 + artist_scorecard",
        }
    except ImportError as e:
        return {"status": "ERROR", "reason": f"import failure: {e}"}
    except Exception as e:
        return {"status": "ERROR", "reason": f"comparable engine error: {e}"}


def _artist_section(
    serving_conn, artist_key: str | None, artist_name: str,
) -> dict[str, Any]:
    """Artist identity + attention context using existing scorecard."""
    if not artist_name:
        return {"status": "UNKNOWN", "reason": "no artist name"}
    try:
        card = artist_scorecard(
            serving_conn, artist_key=artist_key, artist_name=artist_name,
        )
        return {
            "status": "OBSERVED",
            "identity": card.get("identity", {}),
            "attention": card.get("attention", {}),
            "live": card.get("live", {}),
            "festival": card.get("festival", {}),
            "coverage": card.get("coverage", {}),
        }
    except ImportError as e:
        return {"status": "ERROR", "reason": f"import failure: {e}"}
    except Exception as e:
        return {"status": "ERROR", "reason": f"artist scorecard error: {e}"}


def _economics_section(
    workspace_conn, proposed_show_key: str,
) -> dict[str, Any]:
    """Show-economics section: check for linked scenario, replay if possible."""
    try:
        rows = _rows(
            workspace_conn,
            "SELECT scenario_key FROM planning.show_economics_scenarios "
            "WHERE json_extract(identity_context, '$.proposed_show_key') = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            [proposed_show_key],
        )
        if rows:
            from ..economics.show_economics_repository import (
                load_show_economics_scenario,
            )
            scenario = load_show_economics_scenario(
                workspace_conn, rows[0]["scenario_key"],
            )
            return {
                "status": "LINKED",
                "scenario_key": rows[0]["scenario_key"],
                "derived_outputs": scenario.get("derived_outputs"),
                "input_ledger": _summarize_input_ledger(
                    scenario.get("inputs", {}),
                ),
            }
    except ImportError:
        return {"status": "ERROR", "reason": "economics module not importable"}
    except Exception as e:
        return {"status": "ERROR", "reason": f"economics lookup error: {e}"}
    return {"status": "NO_LINKED_SCENARIO"}


def _summarize_input_ledger(inputs: dict[str, Any]) -> dict[str, Any]:
    """Summarize TypedInput provenance into evidence counts."""
    summary: dict[str, list[str]] = {
        EVIDENCE_KNOWN: [], EVIDENCE_ASSUMED: [], EVIDENCE_UNKNOWN: [],
    }
    for field_name, typed in (inputs or {}).items():
        value = typed.get("value")
        provenance = typed.get("provenance", "UNKNOWN")
        status = _classify(value, provenance)
        summary[status].append(field_name)
    return summary


def _ticket_market_section(
    evidence_conn,
    show: dict[str, Any],
    proposed_show_key: str,
) -> dict[str, Any]:
    """TICKET MARKET section from the evidence estate (migration 039).

    Queries acquisition.ticket_market_snapshots for the proposed show's
    canonical event key (via artist+venue+date match against the watch
    universe), then reports per-source current state and, when 2+ real
    observations exist, absolute/percent change + elapsed time.

    Evidence semantics: listing_count / ticket_count are marketplace
    availability PROXIES, never tickets sold. No demand score is derived.
    """
    if evidence_conn is None:
        return {
            "status": "UNKNOWN",
            "reason": "evidence estate not connected",
            "sources": [],
            "history_coverage": {"observations": 0, "sources": [], "first_observed": None, "last_observed": None},
        }

    artist = show.get("artist_name") or ""
    venue = show.get("venue_name") or ""
    proposed_date = str(show.get("proposed_date") or "")[:10]
    if not artist or not proposed_date:
        return {
            "status": "UNKNOWN",
            "reason": "proposed show lacks artist or date for resolution",
            "sources": [],
            "history_coverage": {"observations": 0, "sources": [], "first_observed": None, "last_observed": None},
        }

    try:
        # 1. Resolve the proposed show to a watch-universe event key.
        ev_rows = _rows(
            evidence_conn,
            """
            SELECT event_key FROM acquisition.watch_universe
            WHERE lower(artist_name) = lower(?)
              AND lower(venue_name) = lower(?)
              AND CAST(event_date AS VARCHAR) = ?
            LIMIT 1
            """,
            [artist, venue, proposed_date],
        )
        event_key = ev_rows[0]["event_key"] if ev_rows else None
        if not event_key:
            return {
                "status": "UNKNOWN",
                "reason": "proposed show not in frozen watch universe",
                "sources": [],
                "history_coverage": {"observations": 0, "sources": [], "first_observed": None, "last_observed": None},
            }

        # 2. Latest snapshot per source for this event.
        latest = _rows(
            evidence_conn,
            """
            SELECT * FROM (
                SELECT source_platform, actor_or_endpoint, source_record_id,
                       wave_label, observed_at, currency,
                       resale_min_price, resale_median_price, resale_avg_price,
                       resale_max_price, listing_count, ticket_count,
                       sold_out_flag, availability_flag, face_value,
                       identity_match_status, identity_match_confidence,
                       source_url,
                       ROW_NUMBER() OVER (
                           PARTITION BY source_platform ORDER BY observed_at DESC
                       ) AS rn
                FROM acquisition.ticket_market_snapshots
                WHERE event_key = ? AND identity_match_status = 'MATCHED'
            ) WHERE rn = 1
            """,
            [event_key],
        )

        # 3. Per-event history depth across all sources.
        hist = _rows(
            evidence_conn,
            """
            SELECT COUNT(*) AS observation_count,
                   COUNT(DISTINCT source_platform) AS source_count,
                   MIN(observed_at) AS first_observed,
                   MAX(observed_at) AS last_observed
            FROM acquisition.ticket_market_snapshots
            WHERE event_key = ? AND identity_match_status = 'MATCHED'
            """,
            [event_key],
        )

        sources = []
        for row in latest:
            src = {
                "source_platform": row["source_platform"],
                "actor_or_endpoint": row.get("actor_or_endpoint"),
                "currency": row.get("currency"),
                "current": {
                    "min_price": row.get("resale_min_price"),
                    "median_price": row.get("resale_median_price"),
                    "avg_price": row.get("resale_avg_price"),
                    "max_price": row.get("resale_max_price"),
                    "listing_count": row.get("listing_count"),
                    "ticket_count": row.get("ticket_count"),
                    "sold_out": row.get("sold_out_flag"),
                    "availability": row.get("availability_flag"),
                    "face_value": row.get("face_value"),
                },
                "last_observed": str(row.get("observed_at"))[:19],
                "match_confidence": row.get("identity_match_confidence"),
                "source_url": row.get("source_url"),
            }
            # 4. Change vs first observation (when 2+ real observations).
            changes = _rows(
                evidence_conn,
                """
                SELECT MIN(observed_at) AS first_obs, MAX(observed_at) AS last_obs,
                       COUNT(*) AS n
                FROM acquisition.ticket_market_snapshots
                WHERE event_key = ? AND source_platform = ?
                  AND identity_match_status = 'MATCHED'
                """,
                [event_key, row["source_platform"]],
            )
            if changes and changes[0]["n"] >= 2:
                first_row = _rows(
                    evidence_conn,
                    """
                    SELECT resale_min_price, resale_median_price, listing_count
                    FROM acquisition.ticket_market_snapshots
                    WHERE event_key = ? AND source_platform = ?
                      AND identity_match_status = 'MATCHED'
                    ORDER BY observed_at ASC LIMIT 1
                    """,
                    [event_key, row["source_platform"]],
                )
                c0 = first_row[0] if first_row else {}
                c1 = changes[0]
                src["change"] = _market_change(c0, row, c1)
            sources.append(src)

        h0 = hist[0] if hist else {}
        section = {
            "status": "OBSERVED" if sources else "NO_MATCHED_SNAPSHOTS",
            "event_key": event_key,
            "sources": sources,
            "history_coverage": {
                "observations": int(h0.get("observation_count") or 0),
                "source_count": int(h0.get("source_count") or 0),
                "sources": sorted({s["source_platform"] for s in sources}),
                "first_observed": str(h0["first_observed"])[:19] if h0.get("first_observed") else None,
                "last_observed": str(h0["last_observed"])[:19] if h0.get("last_observed") else None,
            },
        }

        # ---- TICKET_MARKET_DATA_MOAT_V2 additions (graceful when absent) ----
        # 5. NOW / 1D / 7D history columns per source.
        section["market_history"] = _market_history_columns(evidence_conn, event_key, sources)
        # 6. Cross-market summary (all-in normalized where available).
        section["cross_market"] = _cross_market_summary(sources)
        # 7. Security master drill-down (event identifiers per marketplace).
        section["event_identifiers"] = _event_identifiers(evidence_conn, event_key)
        # 8. Source health by acquisition method.
        section["source_health"] = _source_health_by_method(evidence_conn, event_key)
        return section
    except Exception as e:
        return {
            "status": "ERROR",
            "reason": f"ticket market section error: {e}",
            "sources": [],
            "history_coverage": {"observations": 0, "sources": [], "first_observed": None, "last_observed": None},
        }


def _market_history_columns(
    evidence_conn, event_key: str, sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Per-source NOW / 1D / 7D price and listing columns.

    Factual deltas only — absolute + percent change over the window.
    Windows use real timestamps; short histories simply report fewer columns.
    """
    out: dict[str, Any] = {}
    try:
        rows = _rows(
            evidence_conn,
            """
            SELECT source_platform, observed_at,
                   resale_min_price, resale_median_price, listing_count
            FROM acquisition.ticket_market_snapshots
            WHERE event_key = ? AND identity_match_status = 'MATCHED'
            ORDER BY source_platform, observed_at
            """,
            [event_key],
        )
    except Exception:
        return out

    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source_platform"], []).append(r)

    from datetime import datetime as _dt

    for platform, obs in by_source.items():
        current = obs[-1]
        col: dict[str, Any] = {
            "now": _price_col(current),
            "observations": len(obs),
        }
        now_ts = _parse_ts(current.get("observed_at"))
        if now_ts is None:
            out[platform] = col
            continue
        for label, hours in (("1d", 24), ("7d", 168)):
            cutoff = now_ts - timedelta(hours=hours)
            past = [o for o in obs if _parse_ts(o.get("observed_at")) is not None
                    and _parse_ts(o.get("observed_at")) <= cutoff]
            if not past:
                col[label] = {"available": False, "reason": "no observation in window"}
                continue
            # The window baseline is the LATEST observation at-or-before the
            # cutoff (MAX(observed_at) <= cutoff) — never the oldest ever.
            baseline = past[-1]
            col[label] = {
                "available": True,
                "previous": _price_col(baseline),
                "delta": _window_delta(baseline, current),
                "baseline_observed_at": (
                    str(baseline.get("observed_at"))[:19]
                    if baseline.get("observed_at") else None
                ),
                "elapsed_hours": round(
                    (now_ts - _parse_ts(baseline.get("observed_at"))).total_seconds() / 3600, 1
                ),
            }
        out[platform] = col
    return out


def _price_col(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "min_price": row.get("resale_min_price"),
        "median_price": row.get("resale_median_price"),
        "listing_count": row.get("listing_count"),
        "observed_at": str(row.get("observed_at"))[:19] if row.get("observed_at") else None,
    }


def _window_delta(first: dict[str, Any], latest: dict[str, Any]) -> dict[str, Any]:
    """Absolute + percent delta between two observation rows (price, listings)."""
    out: dict[str, Any] = {}

    def _d(key: str, label: str) -> None:
        a = latest.get(key)
        b = first.get(key)
        if a is None or b is None:
            return
        diff = float(a) - float(b)
        pct = (diff / float(b) * 100.0) if float(b) else None
        out[label] = {
            "previous": float(b),
            "current": float(a),
            "absolute_change": round(diff, 2),
            "percent_change": round(pct, 2) if pct is not None else None,
        }

    _d("resale_min_price", "min_price")
    _d("resale_median_price", "median_price")
    _d("listing_count", "listing_count")
    return out


def _parse_ts(value: Any):
    from datetime import datetime as _dt
    if value is None:
        return None
    try:
        return _dt.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _cross_market_summary(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-market spread across mapped marketplaces.

    Uses min_price (all-in where available) per source. No arbitrage claim:
    fee conventions and inventory populations differ by marketplace.
    """
    prices = []
    per_source: dict[str, float | None] = {}
    for s in sources:
        p = (s.get("current") or {}).get("min_price")
        per_source[s["source_platform"]] = p
        if p is not None:
            prices.append(float(p))
    if len(prices) < 2:
        return {
            "status": "INSUFFICIENT",
            "detail": "cross-market spread requires 2+ marketplaces with prices",
            "per_source": per_source,
        }
    lo, hi = min(prices), max(prices)
    return {
        "status": "OBSERVED",
        "lowest_observed_price": lo,
        "highest_observed_price": hi,
        "absolute_spread": round(hi - lo, 2),
        "percent_spread": round((hi - lo) / lo * 100.0, 2) if lo else None,
        "per_source": per_source,
        "note": "all-in basis where available; fee conventions differ across marketplaces — not an arbitrage signal",
    }


def _event_identifiers(evidence_conn, event_key: str) -> dict[str, Any]:
    """Security master drill-down: FI event_key → provider IDs (migration 041)."""
    try:
        rows = _rows(
            evidence_conn,
            """
            SELECT marketplace, marketplace_event_id, marketplace_event_url,
                   mapping_status, mapping_method, confidence, first_resolved_at,
                   last_verified_at
            FROM acquisition.event_identifiers
            WHERE event_key = ?
            ORDER BY marketplace
            """,
            [event_key],
        )
    except Exception:
        return {"status": "UNKNOWN", "reason": "event_identifiers table not available"}
    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {
        "status": "OBSERVED" if rows else "NO_IDENTIFIERS",
        "event_key": event_key,
        "identifiers": rows,
    }


def _source_health_by_method(evidence_conn, event_key: str) -> dict[str, Any]:
    """Source health by acquisition method (migration 041), scoped to the
    marketplaces that actually matter for THIS event.

    The health rows shown on an event page are filtered to the marketplaces
    present in the event's identifiers / snapshots, so a buyer looking at
    Event A never sees unrelated source health from other events.
    """
    try:
        # Marketplaces relevant to this event (identifiers + observed snapshots).
        mps = [r["marketplace"] for r in _rows(
            evidence_conn,
            "SELECT DISTINCT marketplace FROM acquisition.event_identifiers WHERE event_key = ?",
            [event_key],
        )]
        mps += [r["source_platform"] for r in _rows(
            evidence_conn,
            "SELECT DISTINCT source_platform FROM acquisition.ticket_market_snapshots WHERE event_key = ?",
            [event_key],
        )]
        mps = sorted({m for m in mps if m})
        if not mps:
            return {"status": "NO_RUNS", "scope": "EVENT_MARKETPLACES", "runs": []}
        placeholders = ",".join(["?"] * len(mps))
        rows = _rows(
            evidence_conn,
            f"""
            SELECT method, marketplace, status, error_category, events_requested,
                   events_resolved, observations_ingested, latency_ms, cost_usd,
                   schema_version, started_at, finished_at
            FROM acquisition.source_health_by_method
            WHERE marketplace IN ({placeholders})
            ORDER BY started_at DESC
            LIMIT 50
            """,
            mps,
        )
    except Exception:
        return {"status": "UNKNOWN", "reason": "source_health_by_method table not available"}
    # Coerce datetimes so the view stays JSON-serializable.
    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {
        "status": "OBSERVED" if rows else "NO_RUNS",
        "scope": "EVENT_MARKETPLACES",
        "marketplaces": mps,
        "runs": rows,
    }


def _market_change(first: dict[str, Any], latest: dict[str, Any], timing: dict[str, Any]) -> dict[str, Any]:
    """Compute factual delta between first and latest observation.

    Only availability-proxy semantics: price and listing deltas, NOT sales.
    Returns absolute + percent change and elapsed time.
    """
    out: dict[str, Any] = {
        "first_observed": str(timing.get("first_obs") or "")[:19] or None,
        "last_observed": str(timing.get("last_obs") or "")[:19] or None,
        "observation_count": int(timing.get("n") or 0),
    }

    def _delta(key: str, label: str) -> None:
        a = latest.get(key)
        b = first.get(key)
        if a is None or b is None:
            return
        diff = float(a) - float(b)
        pct = (diff / float(b) * 100.0) if float(b) else None
        out[label] = {
            "previous": float(b),
            "current": float(a),
            "absolute_change": round(diff, 2),
            "percent_change": round(pct, 2) if pct is not None else None,
        }

    _delta("resale_min_price", "min_price")
    _delta("resale_median_price", "median_price")
    _delta("listing_count", "listing_count")
    return out


def _provenance_section(
    serving_conn, show: dict[str, Any],
) -> dict[str, Any]:
    """Provenance: what sources inform each section."""
    return {
        "competitive_calendar": "events.provider_event_snapshots (Ticketmaster Discovery API)",
        "venue_capacity": "economics.capacity.assess_venue_claims (Wikidata/Wikipedia)",
        "comparable_events": "boxoffice_research_corpus_v1 (Pollstar/Billboard Boxscore)",
        "artist_context": "core.artists + metrics.artist_attention_observations + events.provider_event_snapshots",
        "show_economics": "planning.show_economics_scenarios (deterministic engine)",
        "ticket_market": "acquisition.ticket_market_snapshots + acquisition.watch_universe (migration 039 evidence rail)",
        "source_count": 6,
        "has_external_augmentation": False,
    }


def _derive_risks(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive explicit risks/warnings from evidence gaps, never opinion."""
    risks: list[dict[str, Any]] = []

    # Capacity conflicts.
    vc = view.get("venue_capacity", {})
    assessment = vc.get("assessment", {})
    review_pairs = assessment.get("review_required_pairs", [])
    if review_pairs:
        risks.append({
            "severity": "WARNING",
            "type": "CAPACITY_CONFLICT",
            "detail": f"{len(review_pairs)} configuration(s) require review for capacity conflicts",
        })
    contradictions = assessment.get("cross_kind_contradictions", [])
    if contradictions:
        risks.append({
            "severity": "WARNING",
            "type": "CROSS_KIND_CAPACITY_CONTRADICTION",
            "detail": f"{len(contradictions)} cross-kind contradictions (e.g. concert cap > max)",
        })

    # Calendar: check PIT status.
    cc = view.get("competitive_calendar", {})
    if cc.get("status") == "UNKNOWN":
        risks.append({
            "severity": "WARNING",
            "type": "MISSING_COMPETITIVE_CALENDAR",
            "detail": "No competitive calendar data available",
        })
    if cc.get("pit_mode") == "NON_PIT":
        risks.append({
            "severity": "INFO",
            "type": "NON_PIT_CALENDAR",
            "detail": "Competitive calendar is non-PIT (no research cutoff); counts may include post-decision events",
        })
    unknown_count = len(cc.get("unknown_knowledge_time", []))
    if unknown_count > 0:
        risks.append({
            "severity": "INFO",
            "type": "INCOMPLETE_KNOWLEDGE_TIME",
            "detail": f"{unknown_count} competing events have unknown knowledge time",
        })

    # Ticket market: check evidence availability.
    tm = view.get("ticket_market", {})
    if tm.get("status") == "UNKNOWN":
        risks.append({
            "severity": "INFO",
            "type": "NO_TICKET_MARKET_EVIDENCE",
            "detail": "No ticket-market evidence for this show (evidence estate not connected or show not in watch universe)",
        })
    elif tm.get("status") == "ERROR":
        risks.append({
            "severity": "WARNING",
            "type": "TICKET_MARKET_INTEGRATION_ERROR",
            "detail": f"Ticket market section error: {tm.get('reason')}",
        })

    # Economics: check for missing scenario.
    econ = view.get("show_economics", {})
    if econ.get("status") == "NO_LINKED_SCENARIO":
        risks.append({
            "severity": "WARNING",
            "type": "MISSING_ECONOMICS",
            "detail": "No show-economics scenario linked to this proposed show",
        })
    if econ.get("status") == "ERROR":
        risks.append({
            "severity": "ERROR",
            "type": "ECONOMICS_INTEGRATION_ERROR",
            "detail": f"Economics lookup error: {econ.get('reason')}",
        })

    # Comparable: check for unavailable data.
    comp = view.get("comparable_events", {})
    gross_status = comp.get("gross", {}).get("status")
    if gross_status in ("UNKNOWN", None):
        risks.append({
            "severity": "INFO",
            "type": "NO_GROSS_COMPARABLES",
            "detail": "No comparable gross data available for this artist",
        })

    # Artist: check identity resolution.
    artist = view.get("artist_context", {})
    if not artist.get("identity", {}).get("matched"):
        risks.append({
            "severity": "WARNING",
            "type": "ARTIST_NOT_RESOLVED",
            "detail": f"Artist '{view.get('header', {}).get('artist_name', '')}' not resolved in canonical identity",
        })

    # Evidence: count assumptions vs known.
    evidence = view.get("evidence_status", {})
    assumed_count = len(evidence.get(EVIDENCE_ASSUMED, []))
    unknown_count_ev = len(evidence.get(EVIDENCE_UNKNOWN, []))
    known_count = len(evidence.get(EVIDENCE_KNOWN, []))
    if assumed_count > 3:
        risks.append({
            "severity": "WARNING",
            "type": "ASSUMPTION_HEAVY",
            "detail": f"{assumed_count} fields are user assumptions — review before committing",
        })
    if unknown_count_ev > 3:
        risks.append({
            "severity": "INFO",
            "type": "LARGE_UNKNOWN_SURFACE",
            "detail": f"{unknown_count_ev} fields are unknown (vs {known_count} known)",
        })
    if known_count == 0:
        risks.append({
            "severity": "WARNING",
            "type": "NO_KNOWN_EVIDENCE",
            "detail": "No externally sourced evidence — all fields are assumptions or unknown",
        })

    # Deal assumptions check.
    header = view.get("header", {})
    guarantee_prov = header.get("guarantee_provenance", "UNKNOWN")
    if header.get("artist_guarantee") and guarantee_prov == "USER_ASSUMPTION":
        risks.append({
            "severity": "INFO",
            "type": "GUARANTEE_IS_ASSUMPTION",
            "detail": f"${header['artist_guarantee']:,.0f} guarantee is a user assumption — not externally sourced",
        })

    return risks


def _build_evidence_status(
    view: dict[str, Any], show: dict[str, Any],
) -> dict[str, Any]:
    """Classify all observable evidence dimensions.

    Deal terms are classified by their provenance, not mere presence.
    """
    status: dict[str, list[str]] = {
        EVIDENCE_KNOWN: [],
        EVIDENCE_ASSUMED: [],
        EVIDENCE_UNKNOWN: [],
        EVIDENCE_CONFLICTING: [],
    }

    header = view.get("header", {})

    # Identity fields: presence = known.
    for field in ["artist_name", "artist_key", "market", "venue_name", "proposed_date"]:
        if header.get(field):
            status[EVIDENCE_KNOWN].append(f"header.{field}")
        else:
            status[EVIDENCE_UNKNOWN].append(f"header.{field}")

    # Deal fields: provenance matters.
    guarantee = header.get("artist_guarantee")
    guarantee_prov = header.get("guarantee_provenance", "UNKNOWN")
    if guarantee is not None and guarantee_prov not in ("UNKNOWN", None, ""):
        g_status = _classify(guarantee, guarantee_prov)
        status[g_status].append("header.artist_guarantee")
    else:
        status[EVIDENCE_UNKNOWN].append("header.artist_guarantee")

    deal_type = header.get("deal_type")
    deal_prov = header.get("deal_provenance", "UNKNOWN")
    if deal_type and deal_prov not in ("UNKNOWN", None, ""):
        d_status = _classify(deal_type, deal_prov)
        status[d_status].append("header.deal_type")
    elif deal_type:
        status[EVIDENCE_ASSUMED].append("header.deal_type")
    else:
        status[EVIDENCE_UNKNOWN].append("header.deal_type")

    for field in ["decision_cutoff", "research_cutoff"]:
        if header.get(field):
            status[EVIDENCE_KNOWN].append(f"header.{field}")
        else:
            status[EVIDENCE_UNKNOWN].append(f"header.{field}")

    # Backend terms.
    if show.get("backend_percentage") is not None:
        backend_prov = show.get("backend_provenance", "UNKNOWN")
        if backend_prov in ("OBSERVED_PUBLIC", "OBSERVED_PRIVATE", "DERIVED"):
            status[EVIDENCE_KNOWN].append("header.backend_percentage")
        else:
            status[EVIDENCE_ASSUMED].append("header.backend_percentage")
    else:
        status[EVIDENCE_UNKNOWN].append("header.backend_percentage")

    # Venue capacity.
    vc = view.get("venue_capacity", {})
    vc_status = vc.get("status", "UNKNOWN")
    if vc_status == "UNKNOWN":
        status[EVIDENCE_UNKNOWN].append("venue_capacity")
    elif vc_status == "ERROR":
        status[EVIDENCE_UNKNOWN].append("venue_capacity")
        status[EVIDENCE_CONFLICTING].append("venue_capacity_error")
    elif vc.get("assessment", {}).get("review_required_pairs"):
        status[EVIDENCE_CONFLICTING].append("venue_capacity")
    else:
        status[EVIDENCE_KNOWN].append("venue_capacity")

    # Competitive calendar.
    cc = view.get("competitive_calendar", {})
    if cc.get("status") == "OBSERVED":
        status[EVIDENCE_KNOWN].append("competitive_calendar")
    elif cc.get("status") == "ERROR":
        status[EVIDENCE_UNKNOWN].append("competitive_calendar")
    else:
        status[EVIDENCE_UNKNOWN].append("competitive_calendar")

    # Comparable events.
    comp = view.get("comparable_events", {})
    if comp.get("status") == "OBSERVED":
        status[EVIDENCE_KNOWN].append("comparable_events")
    elif comp.get("status") == "ERROR":
        status[EVIDENCE_UNKNOWN].append("comparable_events")
    else:
        status[EVIDENCE_UNKNOWN].append("comparable_events")

    # Artist identity.
    artist = view.get("artist_context", {})
    if artist.get("identity", {}).get("matched"):
        status[EVIDENCE_KNOWN].append("artist_identity")
    elif artist.get("status") == "ERROR":
        status[EVIDENCE_UNKNOWN].append("artist_identity")
    else:
        status[EVIDENCE_UNKNOWN].append("artist_identity")

    # Show economics.
    econ = view.get("show_economics", {})
    if econ.get("status") == "LINKED":
        status[EVIDENCE_KNOWN].append("show_economics")
    elif econ.get("status") == "ERROR":
        status[EVIDENCE_UNKNOWN].append("show_economics")
    else:
        status[EVIDENCE_UNKNOWN].append("show_economics")

    # Ticket market.
    tm = view.get("ticket_market", {})
    if tm.get("status") == "OBSERVED":
        status[EVIDENCE_KNOWN].append("ticket_market")
    elif tm.get("status") == "ERROR":
        status[EVIDENCE_UNKNOWN].append("ticket_market")
        status[EVIDENCE_CONFLICTING].append("ticket_market_error")
    else:
        status[EVIDENCE_UNKNOWN].append("ticket_market")

    return status


# ---------------------------------------------------------------------------
# Scenario comparison (Phase 3)
# ---------------------------------------------------------------------------
def compare_proposals(
    serving_conn,
    workspace_conn,
    *,
    proposed_show_keys: list[str],
    project_key: str | None = None,
    scenario_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Side-by-side comparison of 2+ proposed shows or historical revisions.

    Each show gets the full buyer decision view. The comparison highlight
    table marks which dimensions differ between scenarios.

    If scenario_keys are provided, compares historical revision snapshots
    instead of current state.
    """
    if scenario_keys:
        # Historical revision comparison.
        snapshots = []
        for sk in scenario_keys:
            rev = get_revision(workspace_conn, sk)
            if rev:
                snapshots.append(rev)
        if len(snapshots) < 2:
            return {"status": "INSUFFICIENT_SHOWS", "detail": "Need at least 2 valid revisions to compare"}
        return _compare_snapshots(snapshots, project_key)

    if len(proposed_show_keys) < 2:
        return {"status": "INSUFFICIENT_SHOWS", "detail": "Need at least 2 proposed shows to compare"}

    views = [
        buyer_decision_view(serving_conn, workspace_conn, proposed_show_key=key)
        for key in proposed_show_keys
    ]
    return _compare_views(views, proposed_show_keys, project_key)


def _compare_views(
    views: list[dict[str, Any]],
    proposed_show_keys: list[str],
    project_key: str | None,
) -> dict[str, Any]:
    """Build comparison from live buyer decision views."""
    headers = [v.get("header", {}) for v in views]

    comparison: dict[str, Any] = {
        "status": "OBSERVED",
        "project_key": project_key,
        "scenario_count": len(views),
        "scenarios": [],
        "differences": _diff_scenarios(views),
        "comparison_table": _build_comparison_table(headers, views),
    }

    for i, view in enumerate(views):
        comparison["scenarios"].append({
            "proposed_show_key": proposed_show_keys[i],
            "header": view.get("header", {}),
            "venue_capacity": _diffable_venue(view.get("venue_capacity", {})),
            "competitive_summary": _diffable_calendar(view.get("competitive_calendar", {})),
            "comparable_summary": _diffable_comparable(view.get("comparable_events", {})),
            "economics_summary": _diffable_economics(view.get("show_economics", {})),
            "evidence_status": view.get("evidence_status", {}),
            "risk_count": len(view.get("risks", [])),
            "risks": view.get("risks", []),
        })

    return comparison


def _compare_snapshots(
    snapshots: list[dict[str, Any]],
    project_key: str | None,
) -> dict[str, Any]:
    """Compare historical revision snapshots."""
    comparison: dict[str, Any] = {
        "status": "OBSERVED",
        "mode": "HISTORICAL_REVISION_COMPARISON",
        "project_key": project_key,
        "revisions": [],
        "differences": [],
        "comparison_table": [],
    }

    for snap in snapshots:
        comparison["revisions"].append({
            "scenario_key": snap.get("scenario_key"),
            "revision_number": snap.get("revision_number"),
            "revision_created_at": snap.get("revision_created_at"),
            "show": snap,
        })

    # Build diff table across revisions.
    rev_headers = [
        {
            "artist_name": s.get("artist_name"),
            "market": s.get("market"),
            "venue_name": s.get("venue_name"),
            "venue_configuration": s.get("venue_configuration"),
            "proposed_date": s.get("proposed_date"),
            "deal_type": s.get("deal_type"),
            "artist_guarantee": s.get("artist_guarantee"),
        }
        for s in snapshots
    ]

    dims = [
        ("date", "proposed_date"),
        ("venue", "venue_name"),
        ("configuration", "venue_configuration"),
        ("market", "market"),
        ("deal_type", "deal_type"),
        ("guarantee", "artist_guarantee"),
    ]
    for label, key in dims:
        values = [h.get(key) for h in rev_headers]
        if len(set(str(v) for v in values)) > 1:
            comparison["differences"].append({
                "dimension": label,
                "differs": True,
                "revision_values": [str(v) for v in values],
            })

    comparison["comparison_table"] = [
        {"dimension": label, "values": [str(h.get(key, "")) for h in rev_headers], "differs": len(set(str(h.get(key, "")) for h in rev_headers)) > 1}
        for label, key in dims
    ]

    return comparison


def _diff_scenarios(views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify dimensions that differ across scenarios."""
    if len(views) < 2:
        return []
    diffs: list[dict[str, Any]] = []
    headers = [v.get("header", {}) for v in views]

    dims = [
        ("date", "proposed_date"),
        ("venue", "venue_name"),
        ("configuration", "venue_configuration"),
        ("market", "market"),
        ("deal_type", "deal_type"),
        ("guarantee", "artist_guarantee"),
        ("cutoff", "decision_cutoff"),
    ]
    for label, key in dims:
        values = [h.get(key) for h in headers]
        if len(set(str(v) for v in values)) > 1:
            diffs.append({
                "dimension": label,
                "differs": True,
                "scenario_values": [str(v) for v in values],
            })

    # Capacity differences.
    venue_claims = [
        len(v.get("venue_capacity", {}).get("claims", []))
        for v in views
    ]
    if len(set(venue_claims)) > 1:
        diffs.append({"dimension": "venue_capacity_claims", "differs": True, "scenario_values": venue_claims})

    # Calendar differences.
    calendar_counts = [
        len(v.get("competitive_calendar", {}).get("rows", []))
        for v in views
    ]
    if len(set(calendar_counts)) > 1:
        diffs.append({"dimension": "competing_event_count", "differs": True, "scenario_values": calendar_counts})

    return diffs


def _build_comparison_table(
    headers: list[dict[str, Any]], views: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Row-oriented comparison table for tabular display."""
    rows: list[dict[str, Any]] = []
    specs = [
        ("Artist", lambda h: h.get("artist_name", "")),
        ("Market", lambda h: h.get("market", "")),
        ("Date", lambda h: str(h.get("proposed_date", ""))[:10]),
        ("Venue", lambda h: h.get("venue_name", "")),
        ("Configuration", lambda h: h.get("venue_configuration", "")),
        ("Deal Type", lambda h: h.get("deal_type", "—")),
        ("Guarantee", lambda h: f"${h.get('artist_guarantee'):,.0f}" if h.get("artist_guarantee") else "—"),
        ("Decision Cutoff", lambda h: str(h.get("decision_cutoff", ""))[:10] if h.get("decision_cutoff") else "—"),
    ]
    for label, fn in specs:
        vals = [fn(h) for h in headers]
        differs = len(set(vals)) > 1
        rows.append({"dimension": label, "values": vals, "differs": differs})

    # Evidence-level comparisons.
    row_labels = [
        ("Competing Events (total)", lambda v: len(v.get("competitive_calendar", {}).get("rows", []))),
        ("Venue Capacity Claims", lambda v: len(v.get("venue_capacity", {}).get("claims", []))),
        ("Evidence Risks", lambda v: len(v.get("risks", []))),
        ("Known Fields", lambda v: len(v.get("evidence_status", {}).get(EVIDENCE_KNOWN, []))),
        ("Assumed Fields", lambda v: len(v.get("evidence_status", {}).get(EVIDENCE_ASSUMED, []))),
        ("Unknown Fields", lambda v: len(v.get("evidence_status", {}).get(EVIDENCE_UNKNOWN, []))),
    ]
    for label, fn in row_labels:
        vals = [fn(v) for v in views]
        differs = len(set(str(x) for x in vals)) > 1
        rows.append({"dimension": label, "values": [str(x) for x in vals], "differs": differs})

    return rows


# ---------------------------------------------------------------------------
# Diffable extractors for side-by-side comparison.
# ---------------------------------------------------------------------------
def _diffable_venue(sec: dict[str, Any]) -> dict[str, Any]:
    safe_pairs = sec.get("assessment", {}).get("safe_pairs", [])
    return {
        "status": sec.get("status"),
        "safe_pairs": safe_pairs,
        "safe_count": len(safe_pairs),
        "review_required_count": len(
            sec.get("assessment", {}).get("review_required_pairs", []),
        ),
    }


def _diffable_calendar(sec: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": sec.get("status"),
        "pit_mode": sec.get("pit_mode"),
        "total_competing": len(sec.get("rows", [])),
        "known_at_cutoff": len(sec.get("known_at_cutoff", [])),
        "observed_post_cutoff": len(sec.get("observed_after_cutoff", [])),
        "unknown_knowledge_time": len(sec.get("unknown_knowledge_time", [])),
    }


def _diffable_comparable(sec: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": sec.get("status"),
        "gross_median": sec.get("gross", {}).get("weighted_median"),
        "gross_status": sec.get("gross", {}).get("status"),
    }


def _diffable_economics(sec: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": sec.get("status"),
        "scenario_key": sec.get("scenario_key"),
        "error": sec.get("reason"),
    }