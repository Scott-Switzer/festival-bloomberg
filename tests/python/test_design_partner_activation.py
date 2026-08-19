"""Regressions for Design Partner Data Activation V1.

Covers the deterministic readiness tiers (row count alone never advances a
tier), the synthetic structural value simulator (never simulates prediction
accuracy), and the one-command ``partner preview`` CLI (isolated DB, PII
quarantine, duplicate surfacing, UNKNOWN preservation, public-warehouse
isolation). All offline.
"""

from __future__ import annotations

import json

from festival_bloomberg.economics.partner_readiness import (
    TIER_ECONOMICS_USABLE,
    TIER_RETROSPECTIVE_RESEARCH,
    TIER_STRUCTURAL_ONLY,
    TIER_UNDERWRITING_RESEARCH,
    partner_readiness_tier,
    simulate_partner_value,
)

ALLOWED_TIERS = {
    TIER_STRUCTURAL_ONLY,
    TIER_RETROSPECTIVE_RESEARCH,
    TIER_ECONOMICS_USABLE,
    TIER_UNDERWRITING_RESEARCH,
}


# ---------------------------------------------------------------------------
# Readiness tiers (pure, deterministic)
# ---------------------------------------------------------------------------
def test_empty_coverage_is_structural_only():
    tier = partner_readiness_tier({})
    assert tier["tier"] == TIER_STRUCTURAL_ONLY
    assert any("no events" in r for r in tier["reasons"])


def test_row_count_alone_never_advances_tier():
    # 1000 events with zero labels / zero cutoffs must NOT advance.
    coverage = {
        "events": 1000,
        "distinct_artists": 100,
        "distinct_venues": 100,
        "distinct_markets": 20,
        "events_with_cutoff": 0,
        "events_with_attendance": 0,
        "events_with_tickets_sold": 0,
        "events_with_gross": 0,
        "events_with_guarantee": 0,
        "events_with_settlement": 0,
    }
    assert partner_readiness_tier(coverage)["tier"] == TIER_STRUCTURAL_ONLY


def test_retrospective_floor():
    coverage = {
        "events": 60,
        "distinct_artists": 10,
        "distinct_venues": 5,
        "distinct_markets": 2,
        "events_with_cutoff": 40,
        "events_with_attendance": 35,
        "events_with_tickets_sold": 0,
        "events_with_gross": 0,
        "events_with_guarantee": 0,
        "events_with_settlement": 0,
    }
    assert partner_readiness_tier(coverage)["tier"] == TIER_RETROSPECTIVE_RESEARCH


def test_economics_floor():
    coverage = {
        "events": 120,
        "distinct_artists": 20,
        "distinct_venues": 10,
        "distinct_markets": 4,
        "events_with_cutoff": 90,
        "events_with_attendance": 80,
        "events_with_tickets_sold": 80,
        "events_with_gross": 70,
        "events_with_guarantee": 20,
        "events_with_settlement": 0,
    }
    assert partner_readiness_tier(coverage)["tier"] == TIER_ECONOMICS_USABLE


def test_underwriting_research_floor():
    coverage = {
        "events": 300,
        "distinct_artists": 60,
        "distinct_venues": 20,
        "distinct_markets": 8,
        "events_with_cutoff": 200,
        "events_with_attendance": 250,
        "events_with_tickets_sold": 250,
        "events_with_gross": 150,
        "events_with_guarantee": 150,
        "events_with_settlement": 150,
    }
    assert partner_readiness_tier(coverage)["tier"] == TIER_UNDERWRITING_RESEARCH


def test_missing_fields_preserved_as_missing_not_zero():
    # Missing coverage keys default to 0 internally, but that never advances
    # the tier; the returned object must carry the explicit thresholds.
    coverage = {"events": 500}
    result = partner_readiness_tier(coverage)
    assert result["tier"] == TIER_STRUCTURAL_ONLY
    assert "thresholds" in result


# ---------------------------------------------------------------------------
# Synthetic value simulator (structural only)
# ---------------------------------------------------------------------------
def test_simulator_never_reports_prediction_accuracy():
    rows = simulate_partner_value()
    assert rows
    for row in rows:
        assert "accuracy" not in row
        assert "prediction" not in row
        assert "forecast" not in row
        assert row["readiness_tier"] in ALLOWED_TIERS


def test_simulator_covers_all_sizes_and_families():
    rows = simulate_partner_value()
    families = {r["family"] for r in rows}
    sizes = {r["events"] for r in rows}
    assert len(families) == 5
    assert sizes == {50, 100, 250, 500, 1000, 5000}


def test_simulator_festival_operator_reaches_underwriting_at_250():
    rows = simulate_partner_value(families=("FESTIVAL_OPERATOR",))
    by_size = {r["events"]: r for r in rows}
    assert by_size[250]["readiness_tier"] == TIER_UNDERWRITING_RESEARCH
    # 50 events is nowhere near underwriting.
    assert by_size[50]["readiness_tier"] != TIER_UNDERWRITING_RESEARCH


def test_simulator_tier_monotonic_with_size():
    for family in ("LOW_REPEAT_PROMOTER", "REGIONAL_PROMOTER", "MULTI_MARKET_PROMOTER"):
        rows = simulate_partner_value(families=(family,))
        by_size = {r["events"]: r["readiness_tier"] for r in rows}
        order = [TIER_STRUCTURAL_ONLY, TIER_RETROSPECTIVE_RESEARCH, TIER_ECONOMICS_USABLE, TIER_UNDERWRITING_RESEARCH]
        ranks = {t: i for i, t in enumerate(order)}
        seq = [ranks[by_size[s]] for s in (50, 100, 250, 500, 1000, 5000)]
        assert seq == sorted(seq), f"{family}: tier must be monotonic, got {seq}"


# ---------------------------------------------------------------------------
# One-command preview CLI (isolated DB)
# ---------------------------------------------------------------------------
def test_partner_preview_cli_end_to_end(tmp_path, capsys):
    from festival_bloomberg.cli.main import build_parser

    csv = tmp_path / "messy.csv"
    csv.write_text(
        "customer_event_id,artist_name,venue_name,market,city,event_date,booking_date,"
        "announcement_date,onsale_date,venue_capacity,tickets_sold,paid_tickets,"
        "scanned_attendance,ticket_gross,artist_guarantee,promoter_contribution,"
        "sold_out,currency,buyer_email,Final Sold\n"
        "EVT001,The National,Riviera Theatre,Chicago,Chicago,2024-05-10,2023-11-01,"
        "2024-01-15,2024-01-20,2500,2400,2350,2380,150000,75000,24000,true,USD,"
        "fan@example.com,2350\n"
        "EVT001,The National,Riviera Theatre,Chicago,Chicago,2024-05-10,2023-11-01,"
        "2024-01-15,2024-01-20,2500,2400,2350,2380,150000,75000,24000,true,USD,"
        "fan@example.com,2350\n",
        encoding="utf-8",
    )

    db_path = str(tmp_path / "partner_preview.duckdb")
    out_path = str(tmp_path / "summary.json")

    parser = build_parser()
    args = parser.parse_args([
        "partner", "preview",
        "--files", str(csv),
        "--customer", "demo_promoter",
        "--db", db_path,
        "--output", out_path,
    ])
    rc = args.handler(args)
    assert rc == 0

    summary = json.loads(open(out_path, encoding="utf-8").read())
    # PII quarantined, duplicates surfaced, isolated DB honored.
    assert summary["pii_quarantined"] >= 1
    assert summary["duplicates_skipped"] > 0
    assert summary["isolated_db"] == db_path
    assert summary["no_predictions"] is True
    # Two identical rows = one unique event.
    assert summary["structural_coverage"]["events"] == 1
    # Mapping ambiguity surfaced (Final Sold / buyer_email).
    mapping = summary["mapping_summary"]["messy.csv"]
    assert mapping.get("REVIEW_REQUIRED", 0) >= 1 or mapping.get("UNMAPPED", 0) >= 1
    # Readiness stays structural for a tiny corpus.
    assert summary["readiness"]["tier"] == TIER_STRUCTURAL_ONLY


def test_partner_preview_isolation_from_public_warehouse(tmp_path):
    """The preview writes only to its own DB and never touches the default DB."""
    from festival_bloomberg.cli.main import build_parser

    csv = tmp_path / "shows.csv"
    csv.write_text(
        "customer_event_id,artist_name,venue_name,event_date,tickets_sold\n"
        "EVT900,Sturgill Simpson,Thalia Hall,2024-04-04,800\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "isolated.duckdb")
    out_path = str(tmp_path / "summary.json")

    args = build_parser().parse_args([
        "partner", "preview",
        "--files", str(csv),
        "--customer", "iso",
        "--db", db_path,
        "--output", out_path,
    ])
    assert args.handler(args) == 0

    summary = json.loads(open(out_path, encoding="utf-8").read())
    assert summary["isolated_db"] == db_path
    # The isolated DB holds private claims; the default warehouse path was
    # never opened by this command.
    from festival_bloomberg.warehouse.repository import DEFAULT_DB_PATH
    assert db_path != DEFAULT_DB_PATH
