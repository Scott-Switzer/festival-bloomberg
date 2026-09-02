"""Hermetic acceptance contracts for TALENT_BUYER_TERMINAL_V1.

These tests exercise the bounded acceptance evaluator, not live acquisition.
The product implementation is intentionally imported only by the production
acceptance script, so this file remains runnable while a product snapshot is
being rebuilt.
"""

from __future__ import annotations

from scripts.acceptance_talent_buyer_terminal_v1 import (
    capability,
    contract_issues,
    honest_unknown,
    search_match,
    select_cohort,
    validate_compare,
    validate_peers,
    validate_shortlist,
)
from festival_bloomberg.terminal.artist_security import has_advertised_structured_range


def test_explicit_unknown_is_honest_and_not_zero():
    panel = {
        "status": "UNKNOWN",
        "value": None,
        "source": "ListenBrainz",
        "reason": "no eligible observation",
    }
    assert honest_unknown(panel)
    assert capability({"attention": panel}, "attention")["status"] == "PASS"
    # A missing fact is not silently transformed into an observed zero.
    assert 0 not in [v for k, v in panel.items() if k not in {"status", "source", "reason"}]


def test_opaque_intelligence_and_action_fields_fail_closed():
    issues = contract_issues({
        "score": 0.91,
        "winner": "a",
        "recommendation": "buy",
        "expected_gross": 100,
    })
    assert any("score" in issue for issue in issues)
    assert any("winner" in issue for issue in issues)
    assert any("recommendation" in issue for issue in issues)
    assert any("expected_gross" in issue for issue in issues)


def test_peer_panel_requires_pilot_lineage_and_shared_listener_evidence():
    good = {
        "status": "PILOT",
        "data_lineage": "ListenBrainz 1% pilot Gold",
        "edges": [{"artist_key": "b", "shared_listener_count": 17, "jaccard": 0.12}],
    }
    assert validate_peers(good) == []

    missing_lineage = {
        "status": "OBSERVED",
        "edges": [{"artist_key": "b", "shared_listener_count": 17}],
    }
    assert any("lineage" in issue for issue in validate_peers(missing_lineage))


def test_compare_has_differences_but_no_winner():
    assert validate_compare({
        "artist_a": {"artist_key": "a"},
        "artist_b": {"artist_key": "b"},
        "differences": [{"field": "markets", "a": 2, "b": None}],
    }) == []
    assert any("winner" in issue for issue in validate_compare({
        "artist_a": "a", "artist_b": "b", "winner": "a", "differences": [],
    }))


def test_shortlist_roundtrip_requires_one_reused_artist():
    rows = [{"artist_key": "mbid::a", "status": "INTEREST"}]
    assert validate_shortlist(rows, "mbid::a") == []
    assert any("omitted" in issue for issue in validate_shortlist([], "mbid::a"))
    assert any("duplicated" in issue for issue in validate_shortlist(rows * 2, "mbid::a"))


def test_search_finds_canonical_artist_by_key_or_name():
    results = [
        {"entity_type": "ARTIST", "entity_id": "mbid::a", "name": "The Example"},
        {"entity_type": "ARTIST", "entity_id": "mbid::b", "name": "Another Act"},
    ]
    assert search_match(results, "mbid::a", "The Example")
    assert search_match(results, "name::the-example", "Another Act")
    assert not search_match(results, "mbid::missing", "Missing Artist")


def test_cohort_selection_is_tiered_profile_diverse_and_deterministic():
    rows = []
    for tier in ("HOT_1000", "CORE_5000", "COVERAGE_25000"):
        for profile in ("sparse", "medium", "deep"):
            for i in range(4):
                rows.append({
                    "artist_key": f"{tier}:{profile}:{i}",
                    "artist_name": f"Real {tier} {profile} {i}",
                    "tier": tier,
                    "evidence_profile": profile,
                })
    first = select_cohort(rows)
    second = select_cohort(list(reversed(rows)))
    assert [r["artist_key"] for r in first] == [r["artist_key"] for r in second]
    assert len(first) == 25
    assert {r["tier"] for r in first} == {"HOT_1000", "CORE_5000", "COVERAGE_25000"}
    assert {r["evidence_profile"] for r in first} == {"sparse", "medium", "deep"}


def test_ticket_range_counter_requires_structured_provider_evidence():
    assert has_advertised_structured_range({
        "ticket_price_basis": "ADVERTISED_STRUCTURED_RANGE",
        "ticket_evidence_status": "ADVERTISED_RANGE",
        "ticket_price_min": 42.0,
        "ticket_price_max": 110.0,
    })
    assert not has_advertised_structured_range({
        "ticket_price_basis": None,
        "ticket_evidence_status": "NO_CURRENT_TICKET_EVIDENCE",
        "ticket_price_min": None,
        "ticket_price_max": None,
    })
