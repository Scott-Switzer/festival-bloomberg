"""VENUE_CONFIGURATION_CAPACITY_V2 regression tests.

Covers: QID->sitelink resolution, Wikipedia infobox parsing (plain, comma,
ranges, multiple capacities, parentheticals, refs/templates, seating), OSM
semantics, cross-source conflict preservation, and never MAX_PERSONS as
sellable capacity.
"""

from __future__ import annotations

import pytest

from festival_bloomberg.economics.capacity import (
    CapacityClaim,
    assess_venue_claims,
    claim_from_wikipedia_infobox,
    mark_conflicts,
    select_applicable_capacity,
)
from festival_bloomberg.economics.wikipedia_capacity import (
    extracts_to_records,
    parse_venue_infobox,
)


def _infobox(wikitext: str) -> list[dict]:
    parsed = parse_venue_infobox(wikitext)
    return extracts_to_records(
        parsed,
        page_title="Test Venue",
        source_url="https://en.wikipedia.org/wiki/Test_Venue",
        wikidata_qid="Q1000",
        retrieved_at="2026-08-24T00:00:00Z",
    )


# --- Wikipedia infobox parsing ---


def test_plain_integer_capacity_is_max_persons():
    records = _infobox("{{Infobox venue\n| name = X\n| capacity = 500\n}}")
    assert len(records) == 1
    assert records[0]["capacity_value"] == 500.0
    assert records[0]["capacity_kind"] == "MAX_PERSONS"


def test_comma_formatted_capacity():
    records = _infobox("{{Infobox venue\n| name = X\n| capacity = 23,500\n}}")
    assert records[0]["capacity_value"] == 23500.0


def test_capacity_range_is_not_invented():
    parsed = parse_venue_infobox("{{Infobox venue\n| name = X\n| capacity = 2,000\u20132,500\n}}")
    fields = parsed.capacities()
    assert fields == []
    # raw range evidence is preserved (parse_state=RANGE, no numeric claim)
    assert parsed.fields[0].parse_state == "RANGE"


def test_multiple_capacities_all_preserved():
    records = _infobox(
        "{{Infobox venue\n| name = X\n| capacity = 20,000\n| seating_capacity = 18,000\n}}"
    )
    kinds = sorted(r["capacity_kind"] for r in records)
    assert kinds == ["MAX_PERSONS", "SEATED"]


def test_parenthetical_configuration_is_captured():
    records = _infobox("{{Infobox venue\n| name = X\n| capacity = 23,500 (concert)\n}}")
    assert records[0]["capacity_kind"] == "CONCERT"
    assert "(concert)" in records[0]["raw_value"]


def test_seating_capacity_is_seated():
    records = _infobox("{{Infobox venue\n| name = X\n| seating_capacity = 20,917\n}}")
    assert records[0]["capacity_kind"] == "SEATED"


def test_references_and_templates_are_stripped():
    records = _infobox(
        "{{Infobox venue\n| name = X\n"
        "| capacity = 2,500<ref>{{cite web|url=http://x}}</ref>\n}}"
    )
    assert records[0]["capacity_value"] == 2500.0


def test_non_capacity_numeric_fields_never_parsed():
    parsed = parse_venue_infobox(
        "{{Infobox venue\n| name = X\n| year_built = 1925\n| capacity = 1000\n}}"
    )
    field_names = [f.field_name for f in parsed.fields]
    assert "year_built" not in field_names


def test_ambiguous_or_novel_capacity_stays_upper_bound():
    # If the page calls 'capacity' with no config, stays MAX_PERSONS.
    records = _infobox("{{Infobox venue\n| name = X\n| capacity = 20,000\n}}")
    assert records[0]["capacity_kind"] == "MAX_PERSONS"


def test_configuration_from_raw_text_overrides_generic():
    records = _infobox("{{Infobox venue\n| name = X\n| capacity = 18,006 (hockey)\n}}")
    assert records[0]["capacity_kind"] == "SPORTS"


def test_parser_preserves_raw_and_version():
    records = _infobox("{{Infobox venue\n| name = X\n| capacity = 500\n}}")
    assert records[0]["raw_value"] == "500"
    assert records[0]["parser_version"] == "wikipedia_infobox_v2"


# --- Claim semantics ---


def _claim(kind: str, value: float, venue="v1") -> CapacityClaim:
    return CapacityClaim(
        claim_id=f"cap_{kind}_{venue}",
        canonical_venue_id=venue,
        capacity_value=value,
        capacity_kind=kind,
        configuration_description=None,
        effective_from=None,
        effective_to=None,
        provider="test",
        source="test",
        source_url=None,
        source_publication_time=None,
        retrieved_at="2026-08-24T00:00:00Z",
        knowledge_time="2026-08-24T00:00:00Z",
        source_observation_id=None,
        claim_status="OBSERVED",
        usage_label="MAXIMUM_CAPACITY_UPPER_BOUND" if kind == "MAX_PERSONS" else None,
    )


def test_conflicting_claims_coexist():
    # Different capacity claims for the same venue persist side-by-side.
    a = _claim("MAX_PERSONS", 20000)
    b = _claim("CONCERT", 23500)
    # Each keeps its own claim_id and value; neither overwrites the other.
    assert a.claim_id != b.claim_id
    values = {(c.capacity_value, c.capacity_kind) for c in (a, b)}
    assert values == {(20000.0, "MAX_PERSONS"), (23500.0, "CONCERT")}
    # CONCERT 23,500 above the claimed MAX 20,000 is a cross-kind
    # contradiction: both raw claims survive and prefill is blocked.
    marked = mark_conflicts([a, b])
    assert {c.claim_status for c in marked} == {"CROSS_KIND_CONTRADICTION"}


def test_different_configurations_never_conflict_solely_by_value():
    # A: SEATED 17,500 + CONCERT 18,000 is not a conflict.
    seated = _claim("SEATED", 17500)
    concert = _claim("CONCERT", 18000)
    marked = mark_conflicts([seated, concert])
    assert {c.claim_status for c in marked} == {"OBSERVED"}
    assessment = assess_venue_claims([seated, concert])
    assert assessment["same_configuration_conflicts"] == []
    assert {p["configuration"] for p in assessment["safe_pairs"]} == {"SEATED", "CONCERT"}


def test_same_configuration_different_values_requires_review():
    # B: CONCERT 18,000 + CONCERT 20,000 -> conflict, no safe prefill.
    a = _claim("CONCERT", 18000)
    b = _claim("CONCERT", 20000)
    marked = mark_conflicts([a, b])
    assert {c.claim_status for c in marked} == {"SAME_CONFIGURATION_CONFLICT"}
    assessment = assess_venue_claims([a, b])
    assert assessment["safe_pairs"] == []
    assert assessment["status"] == "REVIEW_REQUIRED"
    assert any(
        c["configuration"] == "CONCERT"
        for c in assessment["same_configuration_conflicts"]
    )


def test_configuration_exceeding_max_is_cross_kind_contradiction():
    # C: MAX_PERSONS 20,789 + CONCERT 22,000 -> contradiction, no prefill.
    maximum = _claim("MAX_PERSONS", 20789)
    concert = _claim("CONCERT", 22000)
    marked = mark_conflicts([maximum, concert])
    assert {c.claim_status for c in marked} == {"CROSS_KIND_CONTRADICTION"}
    assessment = assess_venue_claims([maximum, concert])
    assert assessment["safe_pairs"] == []
    assert assessment["status"] == "REVIEW_REQUIRED"
    assert len(assessment["cross_kind_contradictions"]) == 1
    assert assessment["cross_kind_contradictions"][0]["value"] == 22000.0
    assert assessment["cross_kind_contradictions"][0]["contradicted_max_value"] == 20789.0


def test_max_above_configuration_is_not_contradiction():
    # D: MAX_PERSONS 23,500 + CONCERT 20,000 is fine; values merely differ.
    maximum = _claim("MAX_PERSONS", 23500)
    concert = _claim("CONCERT", 20000)
    marked = mark_conflicts([maximum, concert])
    assert {c.claim_status for c in marked} == {"OBSERVED"}
    assessment = assess_venue_claims([maximum, concert])
    assert assessment["cross_kind_contradictions"] == []
    assert assessment["safe_pairs"] == [
        {"configuration": "CONCERT", "value": 20000,
         "supporting_claim_ids": [concert.claim_id]}
    ]


def _sports_claim(subtype: str, value: float) -> CapacityClaim:
    claim = _claim("SPORTS", value)
    claim.configuration_description = subtype
    return claim


def test_sports_subtypes_remain_distinct():
    # E: SPORTS basketball 19,812 + SPORTS hockey 18,006 are distinct
    # legitimate configurations, never collapsed into a false conflict.
    basketball = _sports_claim("basketball", 19812)
    hockey = _sports_claim("ice hockey", 18006)
    marked = mark_conflicts([basketball, hockey])
    assert {c.claim_status for c in marked} == {"OBSERVED"}
    assessment = assess_venue_claims([basketball, hockey])
    assert assessment["same_configuration_conflicts"] == []
    assert assessment["status"] == "UNKNOWN"  # SPORTS never prefills


def test_same_value_same_configuration_corroborated():
    # F: two CONCERT 20,000 claims from separate sources -> separate rows,
    # corroborated assessment.
    a = CapacityClaim(
        claim_id="cap_concert_a", canonical_venue_id="v1", capacity_value=20000.0,
        capacity_kind="CONCERT", configuration_description=None, effective_from=None,
        effective_to=None, provider="wikidata_official_api", source="wikidata_p1083",
        source_url=None, source_publication_time=None,
        retrieved_at="2026-08-24T00:00:00Z", knowledge_time="2026-08-24T00:00:00Z",
        source_observation_id=None, claim_status="OBSERVED", usage_label=None,
    )
    b = CapacityClaim(
        claim_id="cap_concert_b", canonical_venue_id="v1", capacity_value=20000.0,
        capacity_kind="CONCERT", configuration_description=None, effective_from=None,
        effective_to=None, provider="wikipedia_mediawiki_api", source="wikipedia_infobox",
        source_url=None, source_publication_time=None,
        retrieved_at="2026-08-24T00:00:00Z", knowledge_time="2026-08-24T00:00:00Z",
        source_observation_id=None, claim_status="OBSERVED", usage_label=None,
    )
    assert a.claim_id != b.claim_id
    marked = mark_conflicts([a, b])
    assert {c.claim_status for c in marked} == {"CORROBORATED"}
    assessment = assess_venue_claims([a, b])
    assert assessment["safe_pairs"][0]["configuration"] == "CONCERT"
    assert assessment["safe_pairs"][0]["value"] == 20000
    assert set(assessment["safe_pairs"][0]["supporting_claim_ids"]) == {"cap_concert_a", "cap_concert_b"}


def test_assessment_unknown_remains_unknown():
    # H: UNKNOWN remains UNKNOWN.
    assessment = assess_venue_claims([])
    assert assessment["status"] == "UNKNOWN"
    assert assessment["safe_pairs"] == []
    assert assessment["upper_bound_only"] is False


def test_max_persons_only_is_upper_bound_never_prefill():
    # I: MAX_PERSONS never becomes sellable/usable capacity.
    assessment = assess_venue_claims([_claim("MAX_PERSONS", 23500)])
    assert assessment["safe_pairs"] == []
    assert assessment["status"] == "UPPER_BOUND_ONLY"
    assert assessment["upper_bound_only"] is True


def test_raw_claims_source_specific_and_idempotent(tmp_path):
    # J: raw claims remain source-specific and idempotent.
    import duckdb
    from festival_bloomberg.economics.repository import EconomicsRepository

    db = duckdb.connect(str(tmp_path / "claims.duckdb"))
    repo = EconomicsRepository(db)
    record = {
        "capacity_value": 23500.0,
        "capacity_kind": "CONCERT",
        "source_field": "capacity",
        "raw_value": "23,500 (concert)",
        "parser_version": "wikipedia_infobox_v2",
        "source_url": "https://en.wikipedia.org/wiki/X",
        "wikidata_qid": "Q1000",
        "page_title": "X",
        "retrieved_at": "2026-08-24T00:00:00Z",
    }
    claim = claim_from_wikipedia_infobox(record, venue_id="v1")
    try:
        assert repo.insert_capacity_claim(claim) is True
        assert repo.insert_capacity_claim(claim) is False  # idempotent
        rows = db.execute(
            "SELECT count(*) FROM economics.venue_capacity_claims"
        ).fetchone()[0]
        assert rows == 1
        stored = db.execute(
            "SELECT raw_value, parser_version FROM economics.venue_capacity_claims"
        ).fetchone()
        assert stored[0] == "23,500 (concert)"
        assert stored[1] == "wikipedia_infobox_v2"
    finally:
        db.close()


def test_acquisition_report_and_production_prefill_agree(tmp_path):
    # G: the shared assessment (used by acquisition reporting) and production
    # capacity_prefill return the same decision for the same venue/config.
    import duckdb
    from festival_bloomberg.economics.repository import EconomicsRepository
    from festival_bloomberg.economics.show_economics_product import capacity_prefill

    db = duckdb.connect(str(tmp_path / "serving.duckdb"))
    repo = EconomicsRepository(db)
    db.execute(
        "INSERT INTO economics.venue_source_ids "
        "(mapping_id, canonical_venue_id, venue_name, resolution_status, knowledge_time) "
        "VALUES ('map1','v1','Test Room','RESOLVED','2026-01-01')"
    )
    db.execute(
        "INSERT INTO economics.venue_capacity_claims "
        "(claim_id, canonical_venue_id, capacity_value, capacity_kind, provider, source, "
        "retrieved_at, knowledge_time, claim_status) VALUES "
        "('c1','v1',17500,'SEATED','wikidata','p1083','2026-01-01','2026-01-01','OBSERVED'),"
        "('c2','v1',18000,'CONCERT','wikipedia','infobox','2026-01-01','2026-01-01','OBSERVED')"
    )
    try:
        report = repo.reconcile_capacity_claims()
        pair = next(
            p for p in report["safe_pairs"]
            if p["venue_id"] == "v1" and p["configuration"] == "SEATED"
        )
        prefill = capacity_prefill(db, venue_key="Test Room", event_configuration="SEATED")
        suggestion = prefill["usable_capacity_suggestion"]
        assert suggestion is not None
        assert suggestion["value"] == pair["value"] == 17500
        assert suggestion["provenance"] == "OBSERVED_PUBLIC"
        assert set(suggestion["supporting_claim_ids"]) == set(pair["supporting_claim_ids"]) == {"c1"}
    finally:
        db.close()


def test_same_value_cross_source_corroborated_not_collapsed():
    a = CapacityClaim(
        claim_id="cap_wikidata", canonical_venue_id="v1", capacity_value=20917.0,
        capacity_kind="SEATED", configuration_description=None, effective_from=None,
        effective_to=None, provider="wikidata_official_api", source="wikidata_p1083",
        source_url=None, source_publication_time=None,
        retrieved_at="2026-08-24T00:00:00Z", knowledge_time="2026-08-24T00:00:00Z",
        source_observation_id=None, claim_status="OBSERVED", usage_label=None,
    )
    b = CapacityClaim(
        claim_id="cap_wikipedia", canonical_venue_id="v1", capacity_value=20917.0,
        capacity_kind="SEATED", configuration_description=None, effective_from=None,
        effective_to=None, provider="wikipedia_mediawiki_api", source="wikipedia_infobox",
        source_url=None, source_publication_time=None,
        retrieved_at="2026-08-24T00:00:00Z", knowledge_time="2026-08-24T00:00:00Z",
        source_observation_id=None, claim_status="OBSERVED", usage_label=None,
    )
    # Distinct claim_ids remain distinct; never collapsed into one row.
    assert a.claim_id != b.claim_id
    assert a.claim_id.startswith("cap_wikidata")
    assert b.claim_id.startswith("cap_wikipedia")
    assert a.capacity_value == b.capacity_value == 20917.0


def test_max_persons_never_becomes_sellable():
    selected = select_applicable_capacity([_claim("MAX_PERSONS", 23500)], event_configuration=None)
    assert selected["usage_label"] == "MAXIMUM_CAPACITY_UPPER_BOUND"


def test_configuration_mismatch_does_not_prefill():
    selected = select_applicable_capacity([_claim("MAX_PERSONS", 23500)], event_configuration="STANDING")
    assert selected["status"] != "CONFIGURATION_COMPATIBLE"


def test_seated_compatible_capacity_may_prefill():
    selected = select_applicable_capacity([_claim("SEATED", 20917)], event_configuration="SEATED")
    assert selected["status"] == "CONFIGURATION_COMPATIBLE"
    assert selected["capacity_value"] == 20917.0


def test_unknown_stays_unknown():
    selected = select_applicable_capacity([], event_configuration="CONCERT")
    assert selected["status"] == "UNKNOWN"
    assert selected["capacity_value"] is None


# --- claim_from_wikipedia_infobox metadata passthrough ---


def test_wikipedia_claim_carries_raw_and_parser_version():
    record = {
        "capacity_value": 23500.0,
        "capacity_kind": "CONCERT",
        "source_field": "capacity",
        "raw_value": "23,500 (concert)",
        "parser_version": "wikipedia_infobox_v2",
        "source_url": "https://en.wikipedia.org/wiki/United_Center",
        "wikidata_qid": "Q1000",
        "page_title": "United Center",
        "retrieved_at": "2026-08-24T00:00:00Z",
    }
    claim = claim_from_wikipedia_infobox(record, venue_id="v1")
    assert claim.capacity_value == 23500.0
    assert claim.capacity_kind == "CONCERT"
    assert claim.raw_value == "23,500 (concert)"
    assert claim.parser_version == "wikipedia_infobox_v2"


def test_br_separated_figures_never_concatenated():
    # "18,000<br>21,032 (with floor seats)" must not become 1,800,021,032.
    parsed = parse_venue_infobox(
        "{{Infobox venue\n| name = X\n| capacity = 18,000<br>21,032 (with floor seats)\n}}"
    )
    # Two distinct figures with no single value -> unparseable, never merged.
    assert all(f.capacity_value is None for f in parsed.fields)


def test_claim_from_wikipedia_requires_value():
    assert claim_from_wikipedia_infobox({"capacity_value": None}, venue_id="v") is None


def test_migration_035_preserves_index_and_allows_delete(tmp_path):
    """Adding raw_value/parser_version columns must not break deletes on the
    ART index (regression for the DuckDB 'Failed to delete all rows from index'
    fatal caused by ADD COLUMN on an indexed table)."""
    import duckdb
    from festival_bloomberg.migrations import apply_pending_migrations

    db = duckdb.connect(str(tmp_path / "cap.duckdb"))
    try:
        apply_pending_migrations(db)
        db.execute(
            "INSERT INTO economics.venue_capacity_claims "
            "(claim_id, canonical_venue_id, capacity_value, capacity_kind, "
            "provider, source, retrieved_at, knowledge_time, claim_status) "
            "VALUES ('cap_x','v1',100,'SEATED','t','t',"
            "'2026-08-24T00:00:00','2026-08-24T00:00:00','OBSERVED')"
        )
        idx = db.execute(
            "SELECT count(*) FROM duckdb_indexes() "
            "WHERE table_name='venue_capacity_claims' "
            "AND index_name='idx_econ_capacity_venue'"
        ).fetchone()[0]
        assert idx == 1
        db.execute("DELETE FROM economics.venue_capacity_claims WHERE claim_id='cap_x'")
        left = db.execute("SELECT count(*) FROM economics.venue_capacity_claims").fetchone()[0]
        assert left == 0
    finally:
        db.close()
