"""Behavioral tests for TALENT_BUYER_WORKBENCH_V1 (planning workspace).

Offline (in-memory/tmp DuckDB, no network): project CRUD, candidate universe
with deterministic inclusion reasons, scorecard UNKNOWN semantics, shortlist
statuses, and non-optimizing scenario validation.
"""

from __future__ import annotations

import json

from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.planning import candidates as planning_candidates
from festival_bloomberg.planning import repository as planning_repo
from festival_bloomberg.planning import scenario as planning_scenario
from festival_bloomberg.warehouse.repository import FestivalRepository


def _fresh(tmp_path, name: str) -> FestivalRepository:
    repo = FestivalRepository(str(tmp_path / name))
    EventRepository(repo.conn)  # apply pending migrations incl. 033
    return repo


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def test_create_and_get_project(tmp_path):
    repo = _fresh(tmp_path, "proj.duckdb")
    try:
        p = planning_repo.create_project(
            repo.conn, name="Chicago Multi-Day Festival 2027 (Synthetic)",
            city="Chicago", market="Chicago", start_date="2027-08-01",
            end_date="2027-08-04", num_days=4, num_stages=8,
            genre_objectives=["rock", "hip-hop", "electronic"],
            max_billing_tier="HEADLINE",
            scenario_class="SYNTHETIC_PLANNING_SCENARIO",
        )
        got = planning_repo.get_project(repo.conn, p["project_key"])
        assert got["name"].startswith("Chicago Multi-Day Festival 2027")
        assert got["scenario_class"] == "SYNTHETIC_PLANNING_SCENARIO"
        assert got["candidate_count"] == 0
        # idempotent upsert: same name/city -> same project_key
        again = planning_repo.create_project(
            repo.conn, name="Chicago Multi-Day Festival 2027 (Synthetic)",
            city="Chicago", market="Chicago")
        assert again["project_key"] == p["project_key"]
    finally:
        repo.close()


def test_unknown_budget_stays_null_not_zero(tmp_path):
    repo = _fresh(tmp_path, "budget.duckdb")
    try:
        p = planning_repo.create_project(repo.conn, name="No Budget Known")
        got = planning_repo.get_project(repo.conn, p["project_key"])
        assert got["talent_budget_usd"] is None  # UNKNOWN != 0
    finally:
        repo.close()


def test_synthetic_seed_project(tmp_path):
    repo = _fresh(tmp_path, "seed.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        got = planning_repo.get_project(repo.conn, p["project_key"])
        assert len(got["stages"]) == 8
        assert any(s["stage_name"] == "North Stage" for s in got["stages"])
        assert got["stages"][0]["capacity_evidence_class"] in (
            "OBSERVED", "DERIVED", "ESTIMATED", "UNKNOWN")
        assert got["constraints"]
        # re-seed is idempotent
        again = planning_repo.seed_synthetic_project(repo.conn)
        assert again["project_key"] == p["project_key"]
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Candidate universe
# ---------------------------------------------------------------------------
def test_candidate_add_and_inclusion_reasons(tmp_path):
    repo = _fresh(tmp_path, "cand.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        res = planning_repo.add_candidate(
            repo.conn, project_key=p["project_key"], artist_key="mbid::billie",
            artist_name="Billie Eilish", inclusion_reasons=[
                {"reason": "RECENT_FESTIVAL_ARTIST", "evidence": "3 festival events",
                 "source": "musicbrainz_festival_graph"},
                {"reason": "ATTENTION_MOMENTUM", "evidence": "1 record",
                 "source": "metrics.artist_attention_observations"},
            ],
        )
        assert res["candidate_key"]
        rows = planning_repo.list_candidates(repo.conn, p["project_key"])
        assert len(rows) == 1
        reasons = rows[0]["inclusion_reasons"]  # already decoded by the repository
        assert {r["reason"] for r in reasons} == {"RECENT_FESTIVAL_ARTIST", "ATTENTION_MOMENTUM"}
    finally:
        repo.close()


def test_candidate_default_availability_is_unknown(tmp_path):
    repo = _fresh(tmp_path, "avail.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        planning_repo.add_candidate(
            repo.conn, project_key=p["project_key"], artist_key="mbid::x",
            artist_name="Artist X")
        rows = planning_repo.list_candidates(repo.conn, p["project_key"])
        assert rows[0]["availability_status"] == "UNKNOWN"  # never AVAILABLE
    finally:
        repo.close()


def test_build_candidate_universe_merges_reasons(tmp_path):
    repo = _fresh(tmp_path, "uni.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        # seed watchlist so WATCHLIST_TARGET is a real inclusion reason
        from festival_bloomberg.product.workflow import add_watchlist_item, create_watchlist
        wl = create_watchlist(repo.conn, name="Talent", entity_type="ARTIST")
        add_watchlist_item(repo.conn, watchlist_key_value=wl["watchlist_key"],
                           entity_type="ARTIST", entity_key_value="mbid::billie",
                           entity_name="Billie Eilish")
        out = planning_candidates.build_candidate_universe(
            repo.conn, project_key=p["project_key"], market="Chicago", limit=50)
        assert out["candidates_added"] >= 1
        assert out["reason_counts"]["WATCHLIST_TARGET"] >= 1
        billie = [c for c in planning_repo.list_candidates(repo.conn, p["project_key"])
                  if "Billie" in c["artist_name"]]
        assert billie
        reasons = billie[0]["inclusion_reasons"]  # already decoded by the repository
        assert any(r["reason"] == "WATCHLIST_TARGET" for r in reasons)
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------
def test_scorecard_unknowns_on_empty_db(tmp_path):
    repo = _fresh(tmp_path, "sc.duckdb")
    try:
        card = planning_candidates.artist_scorecard(
            repo.conn, artist_name="Nobody Here")
        assert card["comparables"]["gross"]["status"] == "UNKNOWN"
        assert card["coverage"]["coverage_score"] == 0.0
        assert card["festival"]["edition_count"] == 0
    finally:
        repo.close()


def test_scorecard_with_seeded_artist(tmp_path):
    repo = _fresh(tmp_path, "sc2.duckdb")
    try:
        repo.conn.execute(
            """
            INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name)
            VALUES ('mbid::billie', 'billie', 'Billie Eilish', 'billie eilish')
            """
        )
        card = planning_candidates.artist_scorecard(
            repo.conn, artist_key="mbid::billie")
        assert card["identity"]["external_ids"]["musicbrainz"] == "billie"
        assert card["coverage"]["identity"] == 1
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Shortlists
# ---------------------------------------------------------------------------
def test_shortlist_status_roundtrip(tmp_path):
    repo = _fresh(tmp_path, "sl.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        planning_repo.set_shortlist(
            repo.conn, project_key=p["project_key"], artist_key="mbid::billie",
            artist_name="Billie Eilish", status="SHORTLIST", candidate_day=1,
            candidate_stage="North Stage", candidate_billing_tier="HEADLINE")
        rows = planning_repo.list_shortlists(repo.conn, p["project_key"])
        assert len(rows) == 1
        assert rows[0]["status"] == "SHORTLIST"
        assert rows[0]["candidate_day"] == 1
        # move to PASSED
        planning_repo.set_shortlist(
            repo.conn, project_key=p["project_key"], artist_key="mbid::billie",
            artist_name="Billie Eilish", status="PASSED")
        rows = planning_repo.list_shortlists(repo.conn, p["project_key"])
        assert rows[0]["status"] == "PASSED"
    finally:
        repo.close()


def test_shortlist_invalid_status_rejected(tmp_path):
    repo = _fresh(tmp_path, "sli.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        try:
            planning_repo.set_shortlist(
                repo.conn, project_key=p["project_key"], artist_key="a",
                artist_name="A", status="MAYBE")
            raise AssertionError("invalid status must raise")
        except ValueError:
            pass
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Scenario validation (non-optimizing board)
# ---------------------------------------------------------------------------
def test_scenario_double_booking_conflict(tmp_path):
    repo = _fresh(tmp_path, "dbl.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        slots = [
            {"artist_key": "mbid::a", "artist_name": "Artist A", "day": 1,
             "stage": "North Stage", "slot_label": "HEADLINE"},
            {"artist_key": "mbid::a", "artist_name": "Artist A", "day": 1,
             "stage": "South Stage", "slot_label": "HEADLINE"},
        ]
        warnings = planning_scenario.validate_scenario(
            repo.conn, project_key=p["project_key"], slots=slots)
        dbl = [w for w in warnings if w["type"] == "ARTIST_DOUBLE_BOOKED"]
        assert dbl and dbl[0]["severity"] == "CONFIRMED"
    finally:
        repo.close()


def test_scenario_stage_slot_conflict(tmp_path):
    repo = _fresh(tmp_path, "stg.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        slots = [
            {"artist_key": "mbid::a", "artist_name": "Artist A", "day": 2,
             "stage": "North Stage", "slot_label": "18:00"},
            {"artist_key": "mbid::b", "artist_name": "Artist B", "day": 2,
             "stage": "North Stage", "slot_label": "18:00"},
        ]
        warnings = planning_scenario.validate_scenario(
            repo.conn, project_key=p["project_key"], slots=slots)
        assert any(w["type"] == "STAGE_SLOT_CONFLICT" and w["severity"] == "CONFIRMED"
                   for w in warnings)
    finally:
        repo.close()


def test_scenario_passed_artist_warning(tmp_path):
    repo = _fresh(tmp_path, "pas.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        planning_repo.set_shortlist(
            repo.conn, project_key=p["project_key"], artist_key="mbid::p",
            artist_name="Passed Artist", status="PASSED")
        slots = [
            {"artist_key": "mbid::p", "artist_name": "Passed Artist", "day": 1,
             "stage": "North Stage", "slot_label": "18:00"},
        ]
        warnings = planning_scenario.validate_scenario(
            repo.conn, project_key=p["project_key"], slots=slots)
        assert any(w["type"] == "SHORTLIST_PASSED" for w in warnings)
    finally:
        repo.close()


def test_scenario_persist_and_summarize(tmp_path):
    repo = _fresh(tmp_path, "scen.duckdb")
    try:
        p = planning_repo.seed_synthetic_project(repo.conn)
        planning_repo.set_shortlist(
            repo.conn, project_key=p["project_key"], artist_key="mbid::b",
            artist_name="Billie Eilish", status="SHORTLIST",
            candidate_billing_tier="HEADLINE")
        slots = [
            {"artist_key": "mbid::b", "artist_name": "Billie Eilish", "day": 1,
             "stage": "North Stage", "slot_label": "22:00", "billing_tier": "HEADLINE"},
            {"artist_key": "mbid::f", "artist_name": "Fred again..", "day": 1,
             "stage": "South Stage", "slot_label": "20:00", "billing_tier": "SUBHEADLINE"},
        ]
        res = planning_scenario.persist_scenario(
            repo.conn, project_key=p["project_key"], name="Day 1 v1", slots=slots)
        assert res["scenario_key"]
        stored = planning_repo.list_scenarios(repo.conn, p["project_key"])
        assert len(stored) == 1
        assert stored[0]["summaries"]["artist_count"] == 2
        assert stored[0]["summaries"]["billing_distribution"]["HEADLINE"] == 1
        # idempotent overwrite
        planning_scenario.persist_scenario(
            repo.conn, project_key=p["project_key"], name="Day 1 v1",
            slots=slots[:1])
        stored = planning_repo.list_scenarios(repo.conn, p["project_key"])
        assert len(stored) == 1
    finally:
        repo.close()
