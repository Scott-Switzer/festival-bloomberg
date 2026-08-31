from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from festival_bloomberg.identity.graph_v2 import (
    build_graph, normalize_provider_id, read_estate_json, read_wikidata_parquets, rows_from_connection,
    write_graph_tables,
)


def artist(key: str, name: str, mbid: str, **extra):
    return {"artist_key": key, "name": name, "musicbrainz_id": mbid, **extra}


def edge(result, artist_key: str, provider: str):
    return next(e for e in result["edges"] if e["artist_key"] == artist_key and e["provider"] == provider)


def estate(*keys):
    return [{"artist_key": key, "tier": "COVERAGE_25000"} for key in keys]


def test_provider_shapes_fail_closed():
    assert normalize_provider_id("WIKIDATA", "Q42") == "Q42"
    assert normalize_provider_id("WIKIDATA", "42") is None
    assert normalize_provider_id("YOUTUBE", "UC" + "a" * 22) == "UC" + "a" * 22
    assert normalize_provider_id("YOUTUBE", "not-a-channel") is None
    assert normalize_provider_id("OFFICIAL_WEBSITE", "https://example.com/") == "https://example.com"
    assert normalize_provider_id("OFFICIAL_WEBSITE", "example.com") is None


def test_exact_supported_missing_and_candidate_statuses_are_deterministic():
    mbid_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    mbid_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    artists = [artist("a", "A", mbid_a), artist("b", "B", mbid_b)]
    external = [
        {"entity_type": "artist", "entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "evidence_url": "https://source/spotify", "source_system": "musicbrainz", "resolution_status": "VERIFIED", "knowledge_time": "2024-01-01T00:00:00+00:00"},
        {"entity_type": "artist", "entity_key": "a", "id_type": "youtube", "id_value": "UC" + "a" * 22, "evidence_url": "https://source/youtube", "source_system": "musicbrainz"},
    ]
    linkages = [
        {"artist_key": "a", "provider": "SPOTIFY", "provider_id": "A" * 22, "resolution_status": "VERIFIED", "evidence_ref": "linkage-1", "source_system": "spotify", "knowledge_time": "2024-01-01T00:00:00+00:00"},
        {"artist_key": "a", "provider": "YOUTUBE", "provider_id": "UC" + "a" * 22, "resolution_status": "CANDIDATE", "evidence_ref": "candidate-1", "source_system": "youtube"},
    ]
    result = build_graph(artists=artists, external_ids=external, linkages=linkages, estate_rows=estate("a", "b"), canonical_limit=2, as_of="2025-01-01T00:00:00+00:00", created_at="2025-02-01T00:00:00+00:00")
    assert edge(result, "a", "SPOTIFY")["resolution_status"] == "SUPPORTED_MULTI_SOURCE"
    assert edge(result, "a", "YOUTUBE")["resolution_status"] == "CANDIDATE"
    assert not any(e["artist_key"] == "a" and e["provider"] == "DISCOGS" for e in result["edges"])
    assert result["run"]["canonical_count"] == 2
    assert result == build_graph(artists=artists, external_ids=external, linkages=linkages, estate_rows=estate("a", "b"), canonical_limit=2, as_of="2025-01-01T00:00:00+00:00", created_at="2025-02-01T00:00:00+00:00")
    assert result["run"]["run_key"] == build_graph(
        artists=list(reversed(artists)), external_ids=list(reversed(external)),
        linkages=list(reversed(linkages)), estate_rows=estate("a", "b"), canonical_limit=2, as_of="2025-01-01T00:00:00+00:00", created_at="2025-02-01T00:00:00+00:00",
    )["run"]["run_key"]


def test_shared_ids_and_multiple_ids_fail_closed_and_preserve_conflicts():
    artists = [
        artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        artist("b", "B", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    ]
    same = "1" * 10
    result = build_graph(
        artists=artists,
        external_ids=[
            {"entity_type": "artist", "entity_key": "a", "id_type": "discogs", "id_value": same, "evidence_url": "u1"},
            {"entity_type": "artist", "entity_key": "b", "id_type": "discogs", "id_value": same, "evidence_url": "u2"},
            {"entity_type": "artist", "entity_key": "a", "id_type": "viaf", "id_value": "123", "evidence_url": "u3"},
            {"entity_type": "artist", "entity_key": "a", "id_type": "viaf", "id_value": "456", "evidence_url": "u4"},
        ],
        estate_rows=estate("a", "b"), canonical_limit=2,
    )
    assert edge(result, "a", "DISCOGS")["resolution_status"] == "CONFLICT"
    assert edge(result, "b", "DISCOGS")["resolution_status"] == "CONFLICT"
    viaf_edge = edge(result, "a", "VIAF")
    assert viaf_edge["resolution_status"] == "AMBIGUOUS"
    assert viaf_edge["evidence_count"] == 2
    assert viaf_edge["source_refs"] == ["u3", "u4"]
    assert {c["conflict_type"] for c in result["conflicts"]} >= {"SHARED_PROVIDER_ID", "MULTIPLE_PROVIDER_IDS"}
    reversed_result = build_graph(
        artists=list(reversed(artists)), external_ids=list(reversed([
            {"entity_type": "artist", "entity_key": "a", "id_type": "discogs", "id_value": same, "evidence_url": "u1"},
            {"entity_type": "artist", "entity_key": "b", "id_type": "discogs", "id_value": same, "evidence_url": "u2"},
            {"entity_type": "artist", "entity_key": "a", "id_type": "viaf", "id_value": "123", "evidence_url": "u3"},
            {"entity_type": "artist", "entity_key": "a", "id_type": "viaf", "id_value": "456", "evidence_url": "u4"},
        ])), estate_rows=estate("a", "b"), canonical_limit=2,
    )
    assert [c["conflict_key"] for c in result["conflicts"]] == [c["conflict_key"] for c in reversed_result["conflicts"]]


def test_wikidata_p434_joins_to_mbid_and_broad_scope_is_cheap():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result = build_graph(
        artists=[artist("a", "A", mbid), artist("z", "Z", "zzzzzzzz-zzzz-4zzz-8zzz-zzzzzzzzzzzz")],
        estate_rows=estate("a"),
        wikidata_rows=[{"qid": "Q123", "P434": mbid, "source_ref": "wd-row-1"}],
        canonical_limit=1,
    )
    assert edge(result, "a", "WIKIDATA")["resolution_status"] == "CANDIDATE"
    assert not any(e["artist_key"] == "z" and e["provider"] == "WIKIDATA" for e in result["edges"])
    assert next(s for s in result["scorecard"] if s["scope"] == "BROADER_CANONICAL" and s["provider"] == "WIKIDATA")["missing_count"] == 1
    assert {node["scope"] for node in result["nodes"]} == {"CANONICAL_25K", "BROADER_CANONICAL"}


def test_wikidata_artist_external_ids_parquet_shape_joins_through_p434():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result = build_graph(
        artists=[artist("a", "A", mbid)],
        estate_rows=estate("a"),
        wikidata_rows=[{
            "qid": "Q123", "external_id_property": "P434",
            "external_id_value": mbid, "source_ref": "artist-ids-row",
        }],
        canonical_limit=1,
    )
    assert edge(result, "a", "WIKIDATA")["resolution_status"] == "CANDIDATE"
    assert edge(result, "a", "MUSICBRAINZ")["resolution_status"] == "VERIFIED_EXACT"


def test_wikidata_property_rows_share_qid_join_from_separate_p434_row():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result = build_graph(
        artists=[artist("a", "A", mbid)],
        estate_rows=estate("a"),
        wikidata_rows=[
            {"qid": "Q123", "external_id_property": "P434", "external_id_value": mbid, "source_ref": "p434"},
            {"qid": "Q123", "external_id_property": "P214", "external_id_value": "123", "source_ref": "viaf"},
        ],
        canonical_limit=1,
    )
    assert edge(result, "a", "VIAF")["resolution_status"] == "CANDIDATE"


def test_future_wikidata_p434_cannot_authorize_historical_property_row():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result = build_graph(
        artists=[artist("a", "A", mbid)],
        estate_rows=estate("a"),
        wikidata_rows=[
            {
                "qid": "Q123", "external_id_property": "P434",
                "external_id_value": mbid,
                "knowledge_time": "2026-01-01T00:00:00Z",
            },
            {
                "qid": "Q123", "external_id_property": "P214",
                "external_id_value": "123",
                "knowledge_time": "2024-01-01T00:00:00Z",
            },
        ],
        canonical_limit=1, as_of="2025-01-01T00:00:00Z",
    )
    assert not any(row["provider"] == "VIAF" for row in result["edges"])
    assert any(
        row["reason"] == "FUTURE_KNOWLEDGE_TIME"
        for row in result["discarded_claims"]
    )


def test_null_time_wikidata_p434_keeps_verified_property_candidate():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result = build_graph(
        artists=[artist("a", "A", mbid)],
        estate_rows=estate("a"),
        wikidata_rows=[
            {
                "qid": "Q123", "external_id_property": "P434",
                "external_id_value": mbid,
            },
            {
                "qid": "Q123", "external_id_property": "P214",
                "external_id_value": "123", "resolution_status": "VERIFIED",
                "knowledge_time": "2024-01-01T00:00:00Z",
            },
        ],
        canonical_limit=1, as_of="2025-01-01T00:00:00Z",
    )
    assert edge(result, "a", "VIAF")["resolution_status"] == "CANDIDATE"


def test_governed_estate_controls_scope_even_when_keys_sort_late():
    artists = [
        artist("a-ungoverned", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        artist("z-governed", "Z", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    ]
    result = build_graph(
        artists=artists, estate_rows=[{"artist_key": "z-governed", "tier": "GOVERNED_TIER"}], canonical_limit=1,
    )
    nodes = {row["artist_key"]: row for row in result["nodes"]}
    assert nodes["z-governed"]["scope"] == "CANONICAL_25K"
    assert nodes["z-governed"]["estate_tier"] == "GOVERNED_TIER"
    assert nodes["a-ungoverned"]["scope"] == "BROADER_CANONICAL"


def test_estate_size_is_a_validation_expectation_not_a_selector():
    with pytest.raises(ValueError, match="canonical_limit=2"):
        build_graph(
            artists=[artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
            estate_rows=estate("a"), canonical_limit=2,
        )


def test_resolution_claims_fail_closed_and_claimed_status_is_preserved():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    artists = [artist("a", "A", mbid, official_homepage="https://example.com")]
    result = build_graph(
        artists=artists,
        external_ids=[
            {"entity_type": "artist", "entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "UNKNOWN"},
            {"entity_type": "artist", "entity_key": "a", "id_type": "youtube", "id_value": "UC" + "a" * 22, "resolution_status": "VERIFIED", "source_system": "crowd-curated"},
            {"entity_type": "artist", "entity_key": "a", "id_type": "discogs", "id_value": "123", "resolution_status": "AMBIGUOUS"},
            {"entity_type": "artist", "entity_key": "a", "id_type": "isni", "id_value": "1234567890123456", "resolution_status": "CONFLICT"},
            {"entity_type": "artist", "entity_key": "a", "id_type": "viaf", "id_value": "123", "resolution_status": "FAILED"},
        ],
        estate_rows=estate("a"), canonical_limit=1,
    )
    assert edge(result, "a", "OFFICIAL_WEBSITE")["resolution_status"] == "CANDIDATE"
    assert any(row["provider_id"] == "https://example.com" for row in result["evidence"] if row["provider"] == "OFFICIAL_WEBSITE")
    assert edge(result, "a", "SPOTIFY")["resolution_status"] == "CANDIDATE"
    assert edge(result, "a", "YOUTUBE")["resolution_status"] == "CANDIDATE"
    assert edge(result, "a", "DISCOGS")["resolution_status"] == "AMBIGUOUS"
    assert edge(result, "a", "ISNI")["resolution_status"] == "CONFLICT"
    assert not any(row["artist_key"] == "a" and row["provider"] == "VIAF" for row in result["edges"])
    node = next(row for row in result["nodes"] if row["artist_key"] == "a")
    assert node["provider_status_json"]["VIAF"] == "MISSING"
    youtube = next(row for row in result["evidence"] if row["provider"] == "YOUTUBE")
    assert youtube["claimed_status"] == "VERIFIED"


def test_supported_multi_source_requires_distinct_source_systems_and_latest_knowledge():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    base = {"entity_type": "artist", "entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "VERIFIED"}
    result = build_graph(
        artists=[artist("a", "A", mbid)],
        external_ids=[base | {"source_system": "one", "knowledge_time": "2024-01-01T00:00:00+00:00"}, base | {"source_system": "two", "knowledge_time": "2025-01-01T00:00:00+00:00"}],
        estate_rows=estate("a"), canonical_limit=1, as_of="2030-01-01T00:00:00+00:00",
    )
    assert edge(result, "a", "SPOTIFY")["resolution_status"] == "SUPPORTED_MULTI_SOURCE"
    assert edge(result, "a", "SPOTIFY")["knowledge_time"] == "2025-01-01T00:00:00+00:00"
    assert not any(e["artist_key"] == "a" and e["provider"] == "DISCOGS" for e in result["edges"])
    assert all(row.get("created_at") == result["run"]["created_at"] for key in ("nodes", "evidence", "edges", "conflicts", "scorecard", "discarded_claims") for row in result[key])
    assert result["run"]["created_at"] != result["run"]["as_of"]


def test_unknown_claims_and_invalid_p434_are_discarded_with_audit_rows():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result = build_graph(
        artists=[artist("a", "A", mbid)],
        external_ids=[{"entity_type": "artist", "entity_key": "missing", "id_type": "spotify", "id_value": "A" * 22}, {"entity_type": "artist", "entity_key": "a", "id_type": "future_provider", "id_value": "x"}],
        wikidata_rows=[{"qid": "Q123", "external_id_property": "P434", "external_id_value": "not-an-mbid"}],
        estate_rows=estate("a"), canonical_limit=1,
    )
    assert any(row["provider"] == "MUSICBRAINZ" for row in result["evidence"])
    assert len(result["discarded_claims"]) == 3
    assert {row["explanation"] for row in result["conflicts"]} >= {"UNKNOWN_ARTIST", "UNKNOWN_PROVIDER", "NO_UNIQUE_P434_MBID_JOIN"}


def test_optional_linkage_table_is_absent_safe_and_source_set_is_actual():
    import duckdb
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA core")
        conn.execute("CREATE TABLE core.artists (artist_key VARCHAR, name VARCHAR, musicbrainz_id VARCHAR)")
        conn.execute("INSERT INTO core.artists VALUES ('a', 'A', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')")
        artists, external, linkages, sources, available = rows_from_connection(conn, ["a"])
        empty_artists, _, _, _, available_without_keys = rows_from_connection(conn)
    finally:
        conn.close()
    assert artists and external == [] and linkages == []
    assert sources == ["core.artists"]
    assert available == 0
    assert empty_artists == [] and available_without_keys == 1


def test_estate_json_and_report_collision_guards(tmp_path: Path):
    manifest = tmp_path / "estate.json"
    manifest.write_text(json.dumps({"artists": [{"key": "z", "tier": "T1"}]}), encoding="utf-8")
    assert read_estate_json(str(manifest)) == [{"key": "z", "tier": "T1", "artist_key": "z"}]
    spec = importlib.util.spec_from_file_location("build_identity_graph_v2", "scripts/build_identity_graph_v2.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    with pytest.raises(ValueError, match="collides"):
        cli.validate_report_path(manifest, [manifest])


def test_graph_writer_inserts_exact_columns_transactionally():
    import duckdb
    result = build_graph(
        artists=[artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
        estate_rows=estate("a"), canonical_limit=1, as_of="2025-01-01T00:00:00+00:00",
    )
    conn = duckdb.connect(":memory:")
    try:
        counts = write_graph_tables(conn, result)
        assert counts["runs"] == 1
        assert conn.execute("SELECT COUNT(*) FROM identity.graph_v2_nodes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM identity.graph_v2_edges").fetchone()[0] == 1
        assert conn.execute("SELECT estate_tier FROM identity.graph_v2_nodes").fetchone() == ("COVERAGE_25000",)
    finally:
        conn.close()


def test_broad_read_requires_explicit_cap():
    import duckdb
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA core")
        conn.execute("CREATE TABLE core.artists (artist_key VARCHAR, name VARCHAR, musicbrainz_id VARCHAR)")
        conn.execute("INSERT INTO core.artists VALUES ('a', 'A', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'), ('b', 'B', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')")
        with pytest.raises(ValueError, match="max_artists"):
            rows_from_connection(conn, ["a", "b"], include_broad=True, max_artists=1)
        artists, *_ = rows_from_connection(conn, ["a"], include_broad=True, max_artists=2)
        assert {row["artist_key"] for row in artists} == {"a", "b"}
    finally:
        conn.close()


def test_restrictive_rights_win_over_unknown_values():
    result = build_graph(
        artists=[artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
        external_ids=[
            {"entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "VERIFIED", "rights_status": "RIGHTS_BLOCKED", "commercial_use_status": "DISALLOWED"},
            {"entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "VERIFIED", "rights_status": "UNKNOWN", "commercial_use_status": "UNKNOWN"},
        ],
        estate_rows=estate("a"), canonical_limit=1,
    )
    row = edge(result, "a", "SPOTIFY")
    assert row["rights_status"] == "RIGHTS_BLOCKED"
    assert row["commercial_use_status"] == "DISALLOWED"


def test_wikidata_reader_projects_and_enforces_bounds(tmp_path: Path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    path = tmp_path / "wd.parquet"
    pq.write_table(pa.table({"qid": ["Q1", "Q2"], "external_id_property": ["P434", "P999"], "external_id_value": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "x"], "ignored": ["x", "y"]}), path)
    rows = read_wikidata_parquets([path], max_rows=1, max_bytes=path.stat().st_size)
    assert len(rows) == 1 and "ignored" not in rows[0]
    with pytest.raises(ValueError, match="bytes"):
        read_wikidata_parquets([path], max_bytes=1)


def test_as_of_rejects_naive_and_future_or_invalid_knowledge_without_promotion():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with pytest.raises(ValueError, match="RFC3339 UTC"):
        build_graph(artists=[artist("a", "A", mbid)], estate_rows=estate("a"), canonical_limit=1, as_of="2025-01-01")
    future = build_graph(
        artists=[artist("a", "A", mbid)],
        external_ids=[{"entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "API_VERIFIED", "knowledge_time": "2026-01-01T00:00:00Z"}],
        estate_rows=estate("a"), canonical_limit=1, as_of="2025-01-01T00:00:00Z",
    )
    assert not any(row["provider"] == "SPOTIFY" for row in future["edges"])
    assert any(row["reason"] == "FUTURE_KNOWLEDGE_TIME" for row in future["discarded_claims"])
    invalid = build_graph(
        artists=[artist("a", "A", mbid)],
        external_ids=[{"entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "API_VERIFIED", "knowledge_time": "not-a-time"}],
        estate_rows=estate("a"), canonical_limit=1, as_of="2025-01-01T00:00:00Z",
    )
    assert not any(row["provider"] == "SPOTIFY" for row in invalid["edges"])
    assert any(row["reason"] == "INVALID_KNOWLEDGE_TIME" for row in invalid["discarded_claims"])
    null_time = build_graph(
        artists=[artist("a", "A", mbid)],
        external_ids=[{"entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "VERIFIED"}],
        estate_rows=estate("a"), canonical_limit=1, as_of="2025-01-01T00:00:00Z",
    )
    assert edge(null_time, "a", "SPOTIFY")["resolution_status"] == "CANDIDATE"


def test_trust_classes_never_promote_mb_or_wikidata_to_api():
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    result = build_graph(
        artists=[artist("a", "A", mbid)],
        external_ids=[
            {"entity_key": "a", "id_type": "musicbrainz", "id_value": mbid},
            {"entity_key": "a", "id_type": "youtube", "id_value": "UC" + "a" * 22, "resolution_status": "API_VERIFIED", "knowledge_time": "2024-01-01T00:00:00Z"},
        ],
        linkages=[{"artist_key": "a", "provider": "spotify", "provider_id": "A" * 22, "resolution_status": "VERIFIED", "source_system": "linker", "knowledge_time": "2024-01-01T00:00:00Z"}],
        wikidata_rows=[{"qid": "Q123", "P434": mbid}],
        estate_rows=estate("a"), canonical_limit=1, as_of="2025-01-01T00:00:00Z",
    )
    by_provider = {}
    for row in result["evidence"]:
        by_provider.setdefault(row["provider"], set()).add(row["trust_class"])
    assert "CANONICAL_NATIVE" in by_provider["MUSICBRAINZ"]
    assert "MB_OFFICIAL_LINK" in by_provider["MUSICBRAINZ"]
    assert "WIKIDATA_LINK" in by_provider["WIKIDATA"]
    assert by_provider["YOUTUBE"] == {"API_VERIFIED"}
    assert by_provider["SPOTIFY"] == {"VERIFIED_LINKAGE"}
    assert "API_VERIFIED" not in by_provider["MUSICBRAINZ"] | by_provider["WIKIDATA"]


def test_evidence_preflight_happens_before_collection():
    with pytest.raises(ValueError, match="evidence preflight"):
        build_graph(
            artists=[artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", youtube_channel_id="UC" + "a" * 22)],
            estate_rows=estate("a"), canonical_limit=1, max_evidence=1,
        )
    with pytest.raises(ValueError, match="edge preflight"):
        build_graph(
            artists=[artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", youtube_channel_id="UC" + "a" * 22)],
            estate_rows=estate("a"), canonical_limit=1, max_edges=1,
        )


def test_blocked_resolution_claim_wins_over_trusted_claim():
    result = build_graph(
        artists=[artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
        external_ids=[
            {"entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "VERIFIED", "knowledge_time": "2024-01-01T00:00:00Z"},
            {"entity_key": "a", "id_type": "spotify", "id_value": "A" * 22, "resolution_status": "FAILED", "knowledge_time": "2024-01-01T00:00:00Z"},
        ],
        estate_rows=estate("a"), canonical_limit=1, as_of="2025-01-01T00:00:00Z",
    )
    assert not any(row["artist_key"] == "a" and row["provider"] == "SPOTIFY" for row in result["edges"])
    assert next(row for row in result["nodes"] if row["artist_key"] == "a")["provider_status_json"]["SPOTIFY"] == "MISSING"


def test_wikidata_direct_p434_allowlist_and_no_truncation(tmp_path: Path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    mbid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    path = tmp_path / "direct-p434.parquet"
    pq.write_table(pa.table({
        "qid": ["Q1", "Q1"], "P434": [mbid, None],
        "property": [None, "P214"], "external_id_value": [None, "123"],
    }), path)
    rows = read_wikidata_parquets([path], allowed_mbids=[mbid], max_rows=2, max_bytes=path.stat().st_size)
    assert len(rows) == 2
    with pytest.raises(ValueError, match="refusing truncation"):
        read_wikidata_parquets([path], allowed_mbids=[mbid], max_rows=1, max_bytes=path.stat().st_size)


def test_writer_rolls_back_ddl_and_rejects_extra_columns():
    import duckdb
    result = build_graph(
        artists=[artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
        estate_rows=estate("a"), canonical_limit=1, as_of="2025-01-01T00:00:00Z",
    )
    bad = dict(result)
    bad["nodes"] = [dict(result["nodes"][0], unexpected="must-fail")]
    conn = duckdb.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="columns mismatch"):
            write_graph_tables(conn, bad)
        assert conn.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='identity' AND table_name LIKE 'graph_v2_%'").fetchone() == (0,)
    finally:
        conn.close()


def test_cli_guards_and_canonical_resource_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location("build_identity_graph_v2", "scripts/build_identity_graph_v2.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.validate_as_of("2025-01-01T00:00:00Z")
    assert not cli.validate_as_of("2025-01-01")
    assert not cli.validate_as_of("2025-01-01T00:00:00+05:00")
    monkeypatch.setattr("sys.argv", ["build_identity_graph_v2.py", "--db", "source.duckdb", "--estate-json", "estate.json", "--as-of", "2025-01-01T00:00:00Z", "--dry-run", "--output-db", "out.duckdb"])
    with pytest.raises(SystemExit):
        cli.main()
    output = tmp_path / "out.duckdb"
    marker_as_input = cli.completion_path(output)
    monkeypatch.setattr("sys.argv", ["build_identity_graph_v2.py", "--db", str(marker_as_input), "--estate-json", "estate.json", "--as-of", "2025-01-01T00:00:00Z", "--output-db", str(output)])
    with pytest.raises(SystemExit):
        cli.main()
    monkeypatch.setattr("sys.argv", ["build_identity_graph_v2.py", "--db", "source.duckdb", "--estate-json", "estate.json", "--as-of", "2025-01-01T00:00:00Z", "--replace-output"])
    with pytest.raises(SystemExit):
        cli.main()
    result = build_graph(artists=[artist("a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")], estate_rows=estate("a"), canonical_limit=1)
    assert result["run"]["resource_warning"].startswith("CANONICAL_ONLY_DEFAULT")
    assert cli.output_space_requirement(result) >= 1_000_000_000


def test_compact_report_writer_replaces_atomically(tmp_path: Path):
    spec = importlib.util.spec_from_file_location("build_identity_graph_v2", "scripts/build_identity_graph_v2.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    report = tmp_path / "report.json"
    cli.write_report_atomic(report, '{"build_status":"DRY_RUN_READY"}\n')
    assert report.read_text(encoding="utf-8") == '{"build_status":"DRY_RUN_READY"}\n'
    assert not list(tmp_path.glob(".*.tmp"))


def test_cli_persists_materialized_status_in_database_and_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import duckdb

    source = tmp_path / "source.duckdb"
    conn = duckdb.connect(str(source))
    try:
        conn.execute("CREATE SCHEMA core")
        conn.execute("CREATE TABLE core.artists (artist_key VARCHAR, name VARCHAR, musicbrainz_id VARCHAR)")
        conn.execute(
            "INSERT INTO core.artists VALUES (?, ?, ?)",
            ["a", "A", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        )
    finally:
        conn.close()
    estate_path = tmp_path / "estate.json"
    estate_path.write_text(
        json.dumps({"artists": [{"artist_key": "a", "tier": "COVERAGE_25000"}]}),
        encoding="utf-8",
    )
    output = tmp_path / "identity_graph_v2.duckdb"
    report = tmp_path / "identity.json"
    spec = importlib.util.spec_from_file_location("build_identity_graph_v2_materialized", "scripts/build_identity_graph_v2.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_identity_graph_v2.py", "--db", str(source),
            "--estate-json", str(estate_path), "--canonical-limit", "1",
            "--as-of", "2025-01-01T00:00:00Z", "--output-db", str(output),
            "--report", str(report),
        ],
    )
    assert cli.main() == 0
    output_conn = duckdb.connect(str(output), read_only=True)
    try:
        assert output_conn.execute(
            "SELECT build_status FROM identity.graph_v2_runs"
        ).fetchone() == ("MATERIALIZED",)
    finally:
        output_conn.close()
    assert json.loads(report.read_text(encoding="utf-8"))["run"]["build_status"] == "MATERIALIZED"
    complete = cli.verify_completion_manifest(cli.completion_path(output))
    assert complete["status"] == "COMPLETE"
    assert complete["run_key"] == json.loads(report.read_text(encoding="utf-8"))["run"]["run_key"]
    assert complete["output_db"]["sha256"] == cli.artifact(output, "check")["sha256"]
    assert complete["report"]["sha256"] == cli.artifact(report, "check")["sha256"]
    assert not list(tmp_path.glob(".*.tmp"))
