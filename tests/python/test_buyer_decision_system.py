"""Regressions for the Competitive Buyer Decision System V1.

Self-contained: builds a tiny serving DuckDB with the real product table
shapes, exercises the underwriter, comparables, point-in-time reconstruction,
private import (PII quarantine), retrospective, model readiness, monitor
baselines, decision snapshots, and close-out. Private data never touches the
serving connection.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal

import duckdb
import pytest

sys.path.insert(0, "/Users/scottthomasswitzer/CascadeProjects/festival-bloomberg/.freebuff/worktrees/b7db2dd4-1f92-4848-a046-59282b7d25e8/python")

from festival_bloomberg.terminal import decision_system  # noqa: E402
from festival_bloomberg.terminal import mvp_server  # noqa: E402

_SERVING_SCHEMA = """
-- Column names mirror the real terminal.duckdb artifact exactly.
CREATE TABLE artists (
    artist_key VARCHAR PRIMARY KEY,
    name VARCHAR, normalized_name VARCHAR, musicbrainz_id VARCHAR,
    tier VARCHAR, historical_event_count INTEGER, festival_appearance_count INTEGER,
    market_count INTEGER, venues_played INTEGER, evidence_family_count INTEGER,
    source_system VARCHAR, source_scope VARCHAR, knowledge_time TIMESTAMP,
    status VARCHAR, rights_status VARCHAR
);
CREATE TABLE artist_search_terms (
    search_term_key VARCHAR PRIMARY KEY, artist_key VARCHAR, term VARCHAR,
    normalized_term VARCHAR, term_type VARCHAR, source_system VARCHAR,
    source_scope VARCHAR, knowledge_time TIMESTAMP, status VARCHAR
);
CREATE TABLE artist_markets (
    row_key VARCHAR PRIMARY KEY, artist_key VARCHAR, market_key VARCHAR,
    observed_shows INTEGER, first_play_date DATE, last_play_date DATE,
    future_events INTEGER, ticket_evidence_count INTEGER,
    venue_count INTEGER, source_system VARCHAR, source_scope VARCHAR,
    knowledge_time TIMESTAMP, status VARCHAR, explanation VARCHAR
);
CREATE TABLE event_history (
    event_key VARCHAR PRIMARY KEY, artist_key VARCHAR, event_name VARCHAR,
    event_date DATE, venue_name VARCHAR, city VARCHAR, state_code VARCHAR,
    source_system VARCHAR, knowledge_time TIMESTAMP
);
CREATE TABLE festival_appearances (
    appearance_key VARCHAR PRIMARY KEY, artist_key VARCHAR, event_key VARCHAR,
    festival_key VARCHAR, festival_name VARCHAR, edition_year VARCHAR,
    event_date DATE, performance_date DATE, billing_tier VARCHAR,
    edition_key VARCHAR, event_name VARCHAR, knowledge_time TIMESTAMP
);
CREATE TABLE future_events (
    future_event_key VARCHAR PRIMARY KEY, artist_key VARCHAR, event_date DATE,
    event_name VARCHAR, venue_name VARCHAR, market_name VARCHAR, city VARCHAR,
    state_code VARCHAR, event_status VARCHAR, ticket_price_min DOUBLE,
    ticket_price_max DOUBLE, ticket_price_currency VARCHAR, rights_status VARCHAR,
    promoter VARCHAR, source_system VARCHAR, retrieved_at TIMESTAMP
);
CREATE TABLE attention_observations (
    observation_key VARCHAR PRIMARY KEY, artist_key VARCHAR, source_system VARCHAR,
    metric_kind VARCHAR, period_start DATE, period_end DATE, value DOUBLE,
    value_sum DOUBLE, value_unit VARCHAR, status VARCHAR, source_url VARCHAR,
    retrieved_at TIMESTAMP, knowledge_time TIMESTAMP, source_scope VARCHAR,
    rights_status VARCHAR
);
CREATE TABLE artist_peers (
    edge_key VARCHAR PRIMARY KEY, subject_key VARCHAR, peer_key VARCHAR,
    peer_name VARCHAR, rank INTEGER, shared_listeners BIGINT, jaccard DOUBLE,
    cosine DOUBLE, source_system VARCHAR, source_scope VARCHAR,
    knowledge_time TIMESTAMP, status VARCHAR, explanation VARCHAR
);
CREATE TABLE artist_external_ids (
    external_id_key VARCHAR PRIMARY KEY, artist_key VARCHAR, id_type VARCHAR,
    id_value VARCHAR, url VARCHAR, source_system VARCHAR, source_scope VARCHAR,
    knowledge_time TIMESTAMP, status VARCHAR, resolution_method VARCHAR, confidence DOUBLE
);
CREATE TABLE demo_artists (
    artist_key VARCHAR PRIMARY KEY, name VARCHAR, tier VARCHAR, completeness INTEGER,
    market_count INTEGER, historical_event_count INTEGER, festival_appearance_count INTEGER,
    attention_source_count INTEGER, peer_count INTEGER, future_event_count INTEGER
);
CREATE TABLE product_meta (
    product_id VARCHAR, product_version VARCHAR, built_at TIMESTAMP,
    source_serving_snapshot VARCHAR, artist_count INTEGER, market_count INTEGER,
    peer_count INTEGER, event_count INTEGER, festival_count INTEGER,
    future_event_count INTEGER, validation_status VARCHAR, data_boundary VARCHAR
);
"""


@pytest.fixture()
def serving(tmp_path):
    db = str(tmp_path / "serving.duckdb")
    conn = duckdb.connect(db)
    conn.execute(_SERVING_SCHEMA)
    # Ed Sheeran: 3 prior live events, 2 markets, 1 festival, attention row
    rows = [
        ("mbid::sheeran", "Ed Sheeran", "ed sheeran", "mbid::sheeran", "HOT_1000", 12, 2, 3, 9, 5,
         "test", "TEST", "2026-09-01 00:00:00", "PRESENT", "TEST"),
        ("mbid::manny", "Barry Manilow", "barry manilow", "mbid::manny", "HOT_1000", 30, 2, 4, 15, 6,
         "test", "TEST", "2026-09-01 00:00:00", "PRESENT", "TEST"),
        ("mbid::tayl", "Taylor Swift", "taylor swift", "mbid::tayl", "HOT_1000", 40, 3, 5, 20, 6,
         "test", "TEST", "2026-09-01 00:00:00", "PRESENT", "TEST"),
    ]
    conn.executemany("INSERT INTO artists VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    # live history: 9 events dated in 2024, all with knowledge_time set to
    # their event date (the source knew about them then — leakage-safe for a
    # 2025-01-01 cutoff).
    hist = []
    for i in range(9):
        hist.append((f"h{i}", "mbid::sheeran", f"Show {i}", f"2024-{i % 12 + 1:02d}-15", "Venue", "Chicago", "IL", "test", f"2024-{i % 12 + 1:02d}-15 00:00:00"))
    # Barry: 4 pre-2025 events with knowledge_time at event date.
    for i in range(4):
        hist.append((f"hm{i}", "mbid::manny", f"Manny Show {i}", f"2023-{i % 12 + 1:02d}-10", "Beacon", "New York", "NY", "test", f"2023-{i % 12 + 1:02d}-10 00:00:00"))
    conn.executemany("INSERT INTO event_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", hist)
    conn.execute("INSERT INTO artist_markets (row_key, artist_key, market_key, observed_shows, first_play_date, last_play_date, future_events, ticket_evidence_count, knowledge_time) VALUES "
                 "('m1','mbid::sheeran','chicago-il',3,'2023-05-01','2024-08-20',1,1,'2024-08-20 00:00:00'),"
                 "('m2','mbid::sheeran','london-gb',2,'2023-05-01','2024-06-10',0,1,'2024-06-10 00:00:00'),"
                 "('m3','mbid::sheeran','madison-wi',1,'2024-04-01','2024-04-01',0,0,'2024-04-01 00:00:00'),"
                 "('t1','mbid::tayl','chicago-il',5,'2022-01-01','2024-09-01',0,2,'2024-09-01 00:00:00'),"
                 "('t2','mbid::tayl','london-gb',4,'2022-01-01','2024-09-01',0,2,'2024-09-01 00:00:00'),"
                 "('t3','mbid::tayl','nyc-ny',6,'2022-01-01','2024-09-01',1,2,'2024-09-01 00:00:00'),"
                 "('b1','mbid::manny','chicago-il',7,'2021-01-01','2024-07-01',2,3,'2024-07-01 00:00:00')")
    # f1 and f2 share the same event_key => real shared-festival-bill evidence
    # for comps; knowledge_time at listing time, before 2025.
    conn.execute("INSERT INTO festival_appearances (appearance_key, artist_key, event_key, festival_key, festival_name, edition_year, event_date, billing_tier, knowledge_time) VALUES "
                 "('f1','mbid::sheeran','ev-fest-glasto-24','fest-glasto','Glastonbury','2024','2024-06-25','HEADLINE','2024-01-10 00:00:00'),"
                 "('f2','mbid::tayl','ev-fest-glasto-24','fest-glasto','Glastonbury','2024','2024-06-26','HEADLINE','2024-01-11 00:00:00'),"
                 "('f3','mbid::manny','ev-fest-other-23','fest-other','Other Fest','2023','2023-07-01','SUB','2022-11-01 00:00:00')")
    conn.execute("INSERT INTO future_events (future_event_key, artist_key, event_date, event_name, venue_name, city, event_status, ticket_price_min, ticket_price_max, ticket_price_currency) VALUES "
                 "('fe1','mbid::sheeran','2026-12-01','Ed Sheeran','United','chicago','onsale',39.5,89.5,'USD'),"
                 "('fe2','mbid::tayl','2026-12-05','Taylor Swift','United','chicago','onsale',99,299,'USD')")
    conn.execute("INSERT INTO attention_observations (observation_key, artist_key, source_system, metric_kind, period_start, period_end, value, value_sum, value_unit, status, source_url, retrieved_at, knowledge_time, source_scope) VALUES "
                 "('a1','mbid::sheeran','listenbrainz','LISTENBRAINZ_TOTAL_LISTEN_COUNT',NULL,NULL,868600,868600,'listens','ok',NULL,'2026-08-15 20:10:04','2026-08-15 20:10:04','TEST')")
    conn.executemany(
        "INSERT INTO artist_peers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("e1", "mbid::sheeran", "mbid::tayl", "Taylor Swift", 1, 251, 0.012, 0.03, "listenbrainz", "TEST", "2026-08-15 20:10:04", "PRESENT", "test"),
            ("e2", "mbid::sheeran", "mbid::manny", "Barry Manilow", 2, 90, 0.005, 0.01, "listenbrainz", "TEST", "2026-08-15 20:10:04", "PRESENT", "test"),
        ],
    )
    conn.execute("INSERT INTO artist_search_terms VALUES "
                 "('s1','mbid::sheeran','Ed Sheeran','ed sheeran','canonical_name','test','TEST','2026-09-01 00:00:00','PRESENT'),"
                 "('s2','mbid::manny','Barry Manilow','barry manilow','canonical_name','test','TEST','2026-09-01 00:00:00','PRESENT'),"
                 "('s3','mbid::tayl','Taylor Swift','taylor swift','canonical_name','test','TEST','2026-09-01 00:00:00','PRESENT')")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture()
def workspace(tmp_path):
    ws = str(tmp_path / "workspace.duckdb")
    conn = mvp_server.open_workspace(ws)
    yield conn
    conn.close()


def _inputs():
    return {
        "usable_capacity": "1200", "sellable_capacity": "1100",
        "average_ticket_price": "45", "sell_through": "0.55",
        "guarantee": "15000", "backend_percentage": "0.15",
        "artist_expenses": "0",
        "cost_marketing": "8000", "cost_production": "12000",
        "cost_venue": "5000", "cost_labor": "2000",
        "cost_insurance": "0", "cost_other": "0",
        "ancillary_revenue": "0", "sponsorship": "0",
        "ticketing_deduction": "0", "tax_rate": "0",
        "deal_type": "GUARANTEE_VS_PERCENTAGE", "backend_basis": "ADJUSTED_GROSS",
        "sell_through_down": "0.35", "sell_through_up": "0.75",
        "event_date": "2026-12-01",
    }


def test_underwrite_brief_sections(serving, workspace):
    brief = decision_system.build_underwrite(serving, workspace, artist_key="mbid::sheeran", market_key="chicago-il", inputs=_inputs())
    assert brief["artist"]["name"] == "Ed Sheeran"
    assert brief["market"]["observed_shows"] == 3
    assert brief["generation"] is None or isinstance(brief["generation"], str)
    base = brief["economics"]["base"]["outputs"]
    assert base["gross_ticket_revenue"]["status"] == "KNOWN"
    assert base["promoter_contribution"]["status"] == "KNOWN"
    assert base["break_even_sell_through"]["status"] == "KNOWN"
    # Explicit components, never an opaque score.
    assert len(brief["comparables"]) >= 2
    for comp in brief["comparables"]:
        assert comp["components"], "comparable must explain WHY"
    assert any(f["flag"] == "competing_events" for f in brief["risk_flags"])


def test_scenario_labels_are_user_defined_not_probability(serving, workspace):
    brief = decision_system.build_underwrite(serving, workspace, artist_key="mbid::sheeran", market_key="chicago-il", inputs=_inputs())
    for label, sc in brief["economics"].items():
        assert sc["label"] == "USER-DEFINED SCENARIO"
    assert {"downside", "base", "upside"} == set(brief["economics"].keys())


def test_pit_features_use_only_pre_cutoff_evidence(serving, workspace):
    pit = decision_system.pit_features_at(serving, "mbid::sheeran", "2025-01-01")
    assert pit["status"] == "PIT_COMPLETE"
    assert pit["prior_live_events"] >= 3  # fixture events dated before 2025 with knowledge_time before cutoff
    # A cutoff with a missing value is PIT_INSUFFICIENT, never fake zeros-as-known.
    pit2 = decision_system.pit_features_at(serving, "mbid::sheeran", None)
    assert pit2["status"] == "PIT_INSUFFICIENT"


def test_pit_leakage_regression_event_before_cutoff_knowledge_after(serving, workspace):
    """Occurrence != knowability. An event that HAPPENED before the cutoff but
    only became KNOWABLE after it must be excluded from the reconstruction."""
    # Pre-cutoff baseline (leakage-safe): all fixture 2024 events are admissible.
    pit0 = decision_system.pit_features_at(serving, "mbid::sheeran", "2025-01-01")
    admissible_before = pit0["prior_live_events"]
    # Leak row: HAPPENED in 2023 (well before cutoff) but the source only knew
    # about it in 2026 (after cutoff).
    serving.execute(
        "INSERT INTO event_history (event_key, artist_key, event_name, event_date, venue_name, city, state_code, source_system, knowledge_time) "
        "VALUES ('leak1','mbid::sheeran','Leaked 2023 show','2023-03-01','Venue','Chicago','IL','test','2026-09-01 00:00:00')"
    )
    serving.commit()
    pit = decision_system.pit_features_at(serving, "mbid::sheeran", "2025-01-01")
    # The leaked row is NOT counted as admissible.
    assert pit["prior_live_events"] == admissible_before
    fam = next(f for f in pit["families"] if f["family"] == "live_history")
    assert fam["excluded_knowledge_after_cutoff"] >= 1
    # And a row with NO knowledge_time at all is also excluded, not admitted.
    serving.execute(
        "INSERT INTO event_history (event_key, artist_key, event_name, event_date, venue_name, city, state_code, source_system, knowledge_time) "
        "VALUES ('no_kt','mbid::sheeran','No knowledge time','2024-05-05','Venue','Chicago','IL','test',NULL)"
    )
    serving.commit()
    pit2 = decision_system.pit_features_at(serving, "mbid::sheeran", "2025-01-01")
    assert pit2["prior_live_events"] == admissible_before
    fam2 = next(f for f in pit2["families"] if f["family"] == "live_history")
    assert fam2["excluded_missing_knowledge_time"] >= 1


def test_economics_no_silent_defaults(serving, workspace):
    """BLANK = UNKNOWN. Empty inputs must not silently become zero/55%/g-vs-%."""
    sc = decision_system.build_scenario({})
    ledger = sc.input_ledger()
    for name, item in ledger.items():
        if name == "currency":  # USD is the money-unit product constant, not a deal number
            continue
        assert item.provenance.value == "UNKNOWN", f"{name} must be UNKNOWN when blank"
    # No implicit scenarios without explicit sell-through.
    scenarios, template_applied = decision_system.scenario_sets({})
    assert scenarios == {}
    assert template_applied == {}
    # Explicit "0" is an accepted USER_ASSUMPTION ZERO — not UNKNOWN.
    sc2 = decision_system.build_scenario({"tax_rate": "0", "cost_marketing": "0"})
    assert sc2.tax_rate_on_gross.provenance.value == "USER_ASSUMPTION"
    assert sc2.costs.marketing.provenance.value == "USER_ASSUMPTION"
    # Invalid deal type stays UNKNOWN; it is never coerced to a default.
    prov = decision_system.assumption_provenance({"deal_type": "BOGUS"})
    assert prov["deal_type"] == "UNKNOWN"


def test_economics_template_requires_acceptance_and_is_labeled(serving, workspace):
    scenarios, template_applied = decision_system.scenario_sets(
        {"template": "MODERATE", "accept_template": "accept"}
    )
    assert set(scenarios.keys()) == {"downside", "base", "upside"}
    assert template_applied["sell_through_base"] == "MODERATE"
    prov = decision_system.assumption_provenance(
        {"template": "MODERATE"}, template_applied=template_applied,
    )
    assert prov["sell_through"] == "SYSTEM_TEMPLATE_ASSUMPTION"
    # Without acceptance the template is ignored — no hidden fill.
    ignored, applied2 = decision_system.scenario_sets({"template": "MODERATE"})
    assert ignored == {}
    assert applied2 == {}


def test_identity_resolution_fails_closed(serving, workspace):
    headers = ["artist", "show_date", "venue", "market", "capacity", "guarantee", "tickets_sold", "ticket_gross", "onsale"]
    mapping = [
        {"header": h, "status": "AUTO_ACCEPTED", "canonical_field": f}
        for h, f in [("artist", "artist_name"), ("show_date", "event_date"), ("venue", "venue_name"),
                     ("market", "market"), ("capacity", "venue_capacity"), ("guarantee", "artist_guarantee"),
                     ("tickets_sold", "tickets_sold"), ("ticket_gross", "ticket_gross"), ("onsale", "onsale_date")]
    ]
    rows = [
        # VERIFIED_EXACT → linked
        ["Taylor Swift", "2025-06-14", "The Vic", "Chicago", "1200", "15000", "702", "31590", "2025-03-01"],
        # Punctuation must not block an exact match ("Ed, Sheeran" == "ed sheeran")
        ["Ed, Sheeran", "2025-06-14", "United Center", "Chicago", "20000", "150000", "18000", "810000", "2025-03-01"],
        # No exact match → FAIL CLOSED, never first-search-result attachment
        ["Some Unknown Combo Band X", "2025-06-14", "The Vic", "Chicago", "800", "8000", "300", "13500", "2025-03-01"],
    ]
    result = decision_system.import_private_shows(serving, workspace, file_name="x.csv", headers=headers, rows=rows, mapping=mapping)
    assert result["artists_resolved"] == 2  # Taylor + Ed (punctuation-tolerant)
    assert result["identity"]["VERIFIED_EXACT"] == 2
    assert result["identity"]["REVIEW_REQUIRED"] + result["identity"]["UNRESOLVED"] == 1
    linked = decision_system._one(workspace, "SELECT * FROM private_shows WHERE artist_name = 'Taylor Swift'")
    assert linked["artist_key"] == "mbid::tayl"
    assert linked["identity_status"] == "VERIFIED_EXACT"
    ed = decision_system._one(workspace, "SELECT * FROM private_shows WHERE artist_name = 'Ed, Sheeran'")
    assert ed["artist_key"] == "mbid::sheeran"
    assert ed["identity_status"] == "VERIFIED_EXACT"
    unlinked = decision_system._one(workspace, "SELECT * FROM private_shows WHERE artist_name = 'Some Unknown Combo Band X'")
    assert unlinked["artist_key"] is None
    assert unlinked["identity_status"] in ("REVIEW_REQUIRED", "UNRESOLVED")


def test_preview_redacts_pii_values(serving, workspace):
    csv_text = (
        "artist,show_date,venue,market,guarantee,tickets_sold,\"buyer_email\",\"full_name\"\n"
        "\"Ed, Sheeran\",2025-06-14,\"The, Vic\",Chicago,15000,702,scott@example.com,Scott Switzer\n"
    )
    p = decision_system.preview_private_file("quoted.csv", csv_text)
    assert p["row_count"] == 1  # quoted comma in artist name did not split rows
    assert p["prohibited_pii"] == ["buyer_email"] or "buyer_email" in (p["prohibited_pii"] + p["potential_pii"])
    for row in p["preview_rows"]:
        assert "scott@example.com" not in json.dumps(row)
        assert "Scott Switzer" not in json.dumps(row)
        for h, v in row.items():
            if "email" in h or "name" in h:
                assert v == "[REDACTED PII]"


def test_import_quarantines_pii_and_stores_private(serving, workspace):
    headers = ["artist", "show_date", "venue", "market", "capacity", "guarantee", "tickets_sold", "ticket_gross", "onsale", "buyer_email"]
    mapping = [
        {"header": "artist", "status": "AUTO_ACCEPTED", "canonical_field": "artist_name"},
        {"header": "show_date", "status": "AUTO_ACCEPTED", "canonical_field": "event_date"},
        {"header": "venue", "status": "AUTO_ACCEPTED", "canonical_field": "venue_name"},
        {"header": "market", "status": "AUTO_ACCEPTED", "canonical_field": "market"},
        {"header": "capacity", "status": "AUTO_ACCEPTED", "canonical_field": "venue_capacity"},
        {"header": "guarantee", "status": "AUTO_ACCEPTED", "canonical_field": "artist_guarantee"},
        {"header": "tickets_sold", "status": "AUTO_ACCEPTED", "canonical_field": "tickets_sold"},
        {"header": "ticket_gross", "status": "AUTO_ACCEPTED", "canonical_field": "ticket_gross"},
        {"header": "onsale", "status": "AUTO_ACCEPTED", "canonical_field": "onsale_date"},
        {"header": "buyer_email", "status": "UNMAPPED", "canonical_field": None},
    ]
    rows = [["Ed Sheeran", "2025-06-14", "The Vic", "Chicago", "1200", "15000", "702", "31590", "2025-03-01", "scott@example.com"]]
    result = decision_system.import_private_shows(serving, workspace, file_name="x.csv", headers=headers, rows=rows, mapping=mapping)
    assert result["rows_imported"] == 1
    assert result["artists_resolved"] == 1
    stored = decision_system._one(workspace, "SELECT * FROM private_shows")
    assert stored["artist_key"] == "mbid::sheeran"
    assert stored["provenance"] == "OBSERVED_PRIVATE"
    # The email never entered private_shows.
    cols = [c[0] for c in workspace.execute("DESCRIBE private_shows").fetchall()]
    assert "buyer_email" not in cols
    # PII ledger records the quarantine.
    imp = decision_system._one(workspace, "SELECT pii_quarantine_json FROM private_imports")
    assert "buyer_email" in (imp["pii_quarantine_json"] or "")


def test_retrospective_and_readiness(serving, workspace):
    headers = ["artist", "show_date", "venue", "market", "capacity", "guarantee", "tickets_sold", "ticket_gross", "onsale"]
    mapping = [
        {"header": h, "status": "AUTO_ACCEPTED", "canonical_field": f}
        for h, f in [("artist", "artist_name"), ("show_date", "event_date"), ("venue", "venue_name"),
                     ("market", "market"), ("capacity", "venue_capacity"), ("guarantee", "artist_guarantee"),
                     ("tickets_sold", "tickets_sold"), ("ticket_gross", "ticket_gross"), ("onsale", "onsale_date")]
    ]
    rows = [
        ["Ed Sheeran", "2025-06-14", "The Vic", "Chicago", "1200", "15000", "702", "31590", "2025-03-01"],
        ["Barry Manilow", "2025-09-02", "Beacon Theatre", "New York", "2800", "30000", "2150", "129000", "2025-06-15"],
    ]
    decision_system.import_private_shows(serving, workspace, file_name="x.csv", headers=headers, rows=rows, mapping=mapping)
    retro = decision_system.retrospective(workspace, serving)
    assert retro["status"] == "OBSERVED_PRIVATE"
    assert retro["total_shows"] == 2
    assert retro["distributions"]["sell_through"]["status"] == "OBSERVED_PRIVATE"
    assert retro["distributions"]["contribution"]["status"] == "UNKNOWN"  # no contribution column supplied

    ready = decision_system.model_readiness(workspace, serving)
    assert ready["private_settled_shows"] == 2
    assert ready["with_booking_cutoff"] == 2
    assert ready["with_tickets_sold"] == 2
    assert ready["eligible_oos_rows"] >= 2
    assert ready["verdict"] == "no model"


def test_pit_retrospective_side_by_side(serving, workspace):
    headers = ["artist", "show_date", "venue", "market", "capacity", "guarantee", "tickets_sold", "ticket_gross", "onsale"]
    mapping = [
        {"header": h, "status": "AUTO_ACCEPTED", "canonical_field": f}
        for h, f in [("artist", "artist_name"), ("show_date", "event_date"), ("venue", "venue_name"),
                     ("market", "market"), ("capacity", "venue_capacity"), ("guarantee", "artist_guarantee"),
                     ("tickets_sold", "tickets_sold"), ("ticket_gross", "ticket_gross"), ("onsale", "onsale_date")]
    ]
    rows = [["Ed Sheeran", "2025-06-14", "The Vic", "Chicago", "1200", "15000", "702", "31590", "2025-03-01"]]
    decision_system.import_private_shows(serving, workspace, file_name="x.csv", headers=headers, rows=rows, mapping=mapping)
    show_id = decision_system._one(workspace, "SELECT show_id FROM private_shows")["show_id"]
    view = decision_system.pit_retrospective(workspace, serving, show_id)
    assert view is not None
    assert view["decision_cutoff"] == "2025-03-01"
    assert view["pit"]["status"] == "PIT_COMPLETE"
    # Realized outcome carries OBSERVED_PRIVATE provenance.
    assert view["realized_outcome"]
    assert view["realized_outcome"][0]["provenance"] == "OBSERVED_PRIVATE"


def test_snapshot_status_and_closeout(serving, workspace):
    brief = decision_system.build_underwrite(serving, workspace, artist_key="mbid::sheeran", market_key="chicago-il", inputs=_inputs())
    snap = decision_system.save_decision_snapshot(
        workspace, artist_key="mbid::sheeran", artist_name="Ed Sheeran",
        market_key="chicago-il", venue=None, event_date="2026-12-01",
        inputs=_inputs(), brief=brief, status="INTEREST", notes="uat",
    )
    snapshots = decision_system.list_decision_snapshots(workspace)
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "INTEREST"
    decision_system.update_decision_status(workspace, snap["snapshot_id"], "HOLD")
    assert decision_system.get_decision_snapshot(workspace, snap["snapshot_id"])["status"] == "HOLD"
    with pytest.raises(ValueError):
        decision_system.update_decision_status(workspace, snap["snapshot_id"], "BOOK_NOW")

    close = decision_system.close_out_show(
        workspace, snap["snapshot_id"],
        {"paid_tickets": "702", "promoter_contribution": "-8400"},
    )
    vault = decision_system.outcome_vault_summary(workspace)
    assert vault["entries"] == 1
    assert vault["hidden"] == 1  # never revealed by default
    # Close-out also feeds the private retrospective.
    retro = decision_system.retrospective(workspace, serving)
    assert retro["distributions"]["contribution"]["status"] == "OBSERVED_PRIVATE"


def test_monitor_baselines_and_deltas(serving, workspace):
    first = decision_system.monitor_changes(serving, workspace, ["mbid::sheeran", "mbid::tayl"])
    assert first["watch_count"] == 2
    # Second look — no data changed → "no changes".
    second = decision_system.monitor_changes(serving, workspace, ["mbid::sheeran"])
    assert second["artists"][0]["changes"] == []
    # A changed count produces a before/after delta.
    serving.execute("INSERT INTO future_events (future_event_key, artist_key, event_date, event_name, venue_name, city, event_status, ticket_price_min, ticket_price_max, ticket_price_currency) VALUES "
                    "('fe3','mbid::sheeran','2026-12-10','Ed Sheeran','United','chicago','onsale',39.5,89.5,'USD')")
    serving.commit()
    third = decision_system.monitor_changes(serving, workspace, ["mbid::sheeran"])
    deltas = third["artists"][0]["changes"]
    assert any(d["metric"] == "future_events" and d["before"] == 1 and d["after"] == 2 for d in deltas)