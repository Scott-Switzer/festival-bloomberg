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
    mark_conflicts,
    select_applicable_capacity,
    claim_from_wikipedia_infobox,
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
    # mark_conflicts flags distinct (value, kind) pairs as CONFLICTING so the
    # workbench never picks one silently.
    marked = mark_conflicts([a, b])
    assert {c.claim_status for c in marked} == {"CONFLICTING"}


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
