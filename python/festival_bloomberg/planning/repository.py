"""Planning workspace repository: projects, stages, candidates, shortlists,
constraints, scenarios.

Pure CRUD over the ``planning.*`` schema (migration 033). Read models in
``candidates.py`` / ``scenario.py`` build on top. No booking advice, no
optimization, no fabricated data: talent_budget stays NULL when unknown.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SOFTWARE_VERSION = "talent_buyer_workbench_v1"

SHORTLIST_STATUSES = (
    "DISCOVERED", "RESEARCHING", "INTEREST", "HOLD", "CONTACTED", "PASSED",
    "SHORTLIST", "UNKNOWN",
)
AVAILABILITY_STATUSES = (
    "CONFIRMED_CONFLICT", "POSSIBLE_CONFLICT", "NO_CONFLICT_OBSERVED", "UNKNOWN",
)


def _h(material: str, n: int = 24) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:n]


def _parse_json(v: Any) -> Any:
    """DuckDB JSON columns may arrive as str or as already-parsed objects."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return v
    return v


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


_JSON_COLUMNS = {
    "planning.festival_projects": {"genre_objectives"},
    "planning.festival_candidate_artists": {
        "inclusion_reasons", "availability_evidence", "scorecard_snapshot"},
    "planning.festival_scenarios": {"slots", "warnings", "summaries"},
}


def _j(rows: list[dict[str, Any]], table: str) -> list[dict[str, Any]]:
    """Decode JSON columns so API consumers receive dicts, not strings."""
    json_cols = _JSON_COLUMNS.get(table, set())
    if not json_cols:
        return rows
    out = []
    for r in rows:
        rec = dict(r)
        for col in json_cols:
            rec[col] = _parse_json(rec.get(col))
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def create_project(
    conn, *, name: str, city: str | None = None, market: str | None = None,
    venue_site: str | None = None, start_date: str | None = None,
    end_date: str | None = None, num_days: int | None = None,
    num_stages: int | None = None, talent_budget_usd: float | None = None,
    genre_objectives: list[str] | None = None, target_audience: str | None = None,
    min_billing_tier: str | None = None, max_billing_tier: str | None = None,
    notes: str | None = None, scenario_class: str = "SYNTHETIC_PLANNING_SCENARIO",
) -> dict[str, Any]:
    # Stable key per (name, city): re-saving a project updates the same row.
    key = _h(f"project::{name}::{city or ''}")
    conn.execute(
        """
        INSERT INTO planning.festival_projects
            (project_key, name, city, market, venue_site, start_date, end_date,
             num_days, num_stages, talent_budget_usd, genre_objectives,
             target_audience, min_billing_tier, max_billing_tier, notes,
             scenario_class, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (project_key) DO UPDATE SET
            city = excluded.city, market = excluded.market,
            venue_site = excluded.venue_site, start_date = excluded.start_date,
            end_date = excluded.end_date, num_days = excluded.num_days,
            num_stages = excluded.num_stages,
            talent_budget_usd = excluded.talent_budget_usd,
            genre_objectives = excluded.genre_objectives,
            target_audience = excluded.target_audience,
            min_billing_tier = excluded.min_billing_tier,
            max_billing_tier = excluded.max_billing_tier,
            notes = excluded.notes, scenario_class = excluded.scenario_class,
            updated_at = now()
        """,
        [key, name, city, market, venue_site, start_date, end_date, num_days,
         num_stages, talent_budget_usd,
         json.dumps(genre_objectives) if genre_objectives else None,
         target_audience, min_billing_tier, max_billing_tier, notes, scenario_class],
    )
    return get_project(conn, key)


def list_projects(conn) -> list[dict[str, Any]]:
    return _j(_rows(conn, "SELECT * FROM planning.festival_projects ORDER BY created_at"),
              "planning.festival_projects")


def get_project(conn, project_key: str) -> dict[str, Any] | None:
    rows = _j(_rows(
        conn, "SELECT * FROM planning.festival_projects WHERE project_key = ?",
        [project_key],
    ), "planning.festival_projects")
    if not rows:
        return None
    project = rows[0]
    project["stages"] = _rows(
        conn, "SELECT * FROM planning.festival_project_stages WHERE project_key = ? ORDER BY stage_name",
        [project_key],
    )
    project["constraints"] = _rows(
        conn, "SELECT * FROM planning.festival_constraints WHERE project_key = ? ORDER BY created_at",
        [project_key],
    )
    project["candidate_count"] = int(conn.execute(
        "SELECT COUNT(*) FROM planning.festival_candidate_artists WHERE project_key = ?",
        [project_key],
    ).fetchone()[0])
    return project


def add_stage(
    conn, *, project_key: str, stage_name: str, capacity_claim: float | None = None,
    capacity_evidence_class: str | None = None, indoor_outdoor: str | None = None,
) -> dict[str, Any]:
    key = _h(f"stage::{project_key}::{stage_name}")
    conn.execute(
        """
        INSERT INTO planning.festival_project_stages
            (stage_key, project_key, stage_name, capacity_claim,
             capacity_evidence_class, indoor_outdoor, created_at)
        VALUES (?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (stage_key) DO UPDATE SET
            capacity_claim = excluded.capacity_claim,
            capacity_evidence_class = excluded.capacity_evidence_class,
            indoor_outdoor = excluded.indoor_outdoor
        """,
        [key, project_key, stage_name, capacity_claim, capacity_evidence_class, indoor_outdoor],
    )
    return {"stage_key": key, "project_key": project_key, "stage_name": stage_name}


# ---------------------------------------------------------------------------
# Candidate universe
# ---------------------------------------------------------------------------
def add_candidate(
    conn, *, project_key: str, artist_key: str | None, artist_name: str,
    musicbrainz_id: str | None = None, inclusion_reasons: list[dict[str, Any]] | None = None,
    availability_status: str = "UNKNOWN",
    availability_evidence: list[dict[str, Any]] | None = None,
    scorecard_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Idempotently add a candidate; reasons merge (dedupe by reason name)."""
    if availability_status not in AVAILABILITY_STATUSES:
        raise ValueError(f"invalid availability_status {availability_status!r}")
    key = _h(f"cand::{project_key}::{artist_key or artist_name}", 32)
    existing = _rows(
        conn, "SELECT inclusion_reasons FROM planning.festival_candidate_artists WHERE candidate_key = ?",
        [key],
    )
    reasons = list(inclusion_reasons or [])
    if existing:
        prior = _parse_json(existing[0]["inclusion_reasons"]) or []
        names = {r.get("reason") for r in reasons}
        for r in prior:
            if r.get("reason") not in names:
                reasons.append(r)
    conn.execute(
        """
        INSERT INTO planning.festival_candidate_artists
            (candidate_key, project_key, artist_key, artist_name, musicbrainz_id,
             inclusion_reasons, availability_status, availability_evidence,
             scorecard_snapshot, added_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (candidate_key) DO UPDATE SET
            inclusion_reasons = excluded.inclusion_reasons,
            availability_status = excluded.availability_status,
            availability_evidence = excluded.availability_evidence,
            updated_at = now()
        """,
        [key, project_key, artist_key, artist_name, musicbrainz_id,
         json.dumps(reasons) if reasons else None, availability_status,
         json.dumps(availability_evidence) if availability_evidence else None,
         json.dumps(scorecard_snapshot, default=str) if scorecard_snapshot else None],
    )
    return {"candidate_key": key, "project_key": project_key, "artist_name": artist_name,
            "artist_key": artist_key, "inclusion_reasons": reasons}


def list_candidates(conn, project_key: str) -> list[dict[str, Any]]:
    return _j(_rows(
        conn, "SELECT * FROM planning.festival_candidate_artists WHERE project_key = ? "
              "ORDER BY artist_name",
        [project_key],
    ), "planning.festival_candidate_artists")


# ---------------------------------------------------------------------------
# Shortlists
# ---------------------------------------------------------------------------
def set_shortlist(
    conn, *, project_key: str, artist_key: str | None, artist_name: str,
    status: str = "DISCOVERED", candidate_day: int | None = None,
    candidate_stage: str | None = None, candidate_billing_tier: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    if status not in SHORTLIST_STATUSES:
        raise ValueError(f"invalid shortlist status {status!r}")
    key = _h(f"sl::{project_key}::{artist_key or artist_name}", 32)
    conn.execute(
        """
        INSERT INTO planning.festival_shortlists
            (shortlist_key, project_key, artist_key, artist_name, status,
             candidate_day, candidate_stage, candidate_billing_tier, notes,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (shortlist_key) DO UPDATE SET
            status = excluded.status, candidate_day = excluded.candidate_day,
            candidate_stage = excluded.candidate_stage,
            candidate_billing_tier = excluded.candidate_billing_tier,
            notes = excluded.notes, updated_at = now()
        """,
        [key, project_key, artist_key, artist_name, status, candidate_day,
         candidate_stage, candidate_billing_tier, notes],
    )
    return {"shortlist_key": key, "project_key": project_key, "artist_name": artist_name,
            "status": status}


def list_shortlists(conn, project_key: str) -> list[dict[str, Any]]:
    return _rows(
        conn, "SELECT * FROM planning.festival_shortlists WHERE project_key = ? ORDER BY status, artist_name",
        [project_key],
    )


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------
def add_constraint(
    conn, *, project_key: str, constraint_type: str, description: str,
    payload: dict[str, Any] | None = None, source: str | None = None,
) -> dict[str, Any]:
    key = _h(f"con::{project_key}::{constraint_type}::{description}")
    conn.execute(
        """
        INSERT INTO planning.festival_constraints
            (constraint_key, project_key, constraint_type, description, payload,
             source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (constraint_key) DO NOTHING
        """,
        [key, project_key, constraint_type, description,
         json.dumps(payload) if payload else None, source],
    )
    return {"constraint_key": key}


def list_constraints(conn, project_key: str) -> list[dict[str, Any]]:
    return _rows(
        conn, "SELECT * FROM planning.festival_constraints WHERE project_key = ? ORDER BY created_at",
        [project_key],
    )


# ---------------------------------------------------------------------------
# Scenarios (non-optimizing boards)
# ---------------------------------------------------------------------------
def save_scenario(
    conn, *, project_key: str, name: str, slots: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    summaries: dict[str, Any] | None = None, notes: str | None = None,
) -> dict[str, Any]:
    key = _h(f"scen::{project_key}::{name}")
    conn.execute(
        """
        INSERT INTO planning.festival_scenarios
            (scenario_key, project_key, name, notes, slots, warnings, summaries,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (scenario_key) DO UPDATE SET
            slots = excluded.slots, warnings = excluded.warnings,
            summaries = excluded.summaries, notes = excluded.notes,
            updated_at = now()
        """,
        [key, project_key, name, notes,
         json.dumps(slots, default=str), json.dumps(warnings, default=str),
         json.dumps(summaries, default=str)],
    )
    return {"scenario_key": key, "project_key": project_key, "name": name}


def list_scenarios(conn, project_key: str) -> list[dict[str, Any]]:
    return _j(_rows(
        conn, "SELECT * FROM planning.festival_scenarios WHERE project_key = ? ORDER BY created_at",
        [project_key],
    ), "planning.festival_scenarios")


# ---------------------------------------------------------------------------
# Synthetic example project (clearly marked; NOT official festival data)
# ---------------------------------------------------------------------------
def seed_synthetic_project(conn) -> dict[str, Any]:
    """Lollapalooza-STYLE 4-day Chicago festival — SYNTHETIC PLANNING SCENARIO.

    Never presented as official Lollapalooza data. Idempotent.
    """
    project = create_project(
        conn,
        name="Chicago Multi-Day Festival 2027 (Synthetic)",
        city="Chicago", market="Chicago",
        venue_site="Grant Park (synthetic assumption)",
        start_date="2027-08-01", end_date="2027-08-04",
        num_days=4, num_stages=8,
        genre_objectives=["rock", "hip-hop", "electronic", "pop"],
        target_audience="18-34 broad-market",
        min_billing_tier="EARLY", max_billing_tier="HEADLINE",
        notes="Synthetic planning scenario for acceptance testing. NOT official Lollapalooza data.",
        scenario_class="SYNTHETIC_PLANNING_SCENARIO",
    )
    for sname, cap, cls, io in [
        ("North Stage", 60000, "ESTIMATED", "OUTDOOR"),
        ("South Stage", 50000, "ESTIMATED", "OUTDOOR"),
        ("T-Mobile Stage", 25000, "ESTIMATED", "OUTDOOR"),
        ("Bud Light Seltzer Stage", 20000, "ESTIMATED", "OUTDOOR"),
        ("BMI Stage", 15000, "ESTIMATED", "OUTDOOR"),
        ("Perry's", 20000, "ESTIMATED", "OUTDOOR"),
        ("Grove Stage", 10000, "ESTIMATED", "OUTDOOR"),
        ("Kidzapalooza", 5000, "ESTIMATED", "OUTDOOR"),
    ]:
        add_stage(conn, project_key=project["project_key"], stage_name=sname,
                  capacity_claim=cap, capacity_evidence_class=cls, indoor_outdoor=io)
    add_constraint(
        conn, project_key=project["project_key"], constraint_type="BILLING_TIER",
        description="At most 1 headliner billing per stage per day",
        payload={"rule": "max_headliners_per_stage_day: 1"}, source="synthetic"),
    return project
