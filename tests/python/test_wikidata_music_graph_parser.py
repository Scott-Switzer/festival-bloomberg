from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_wikidata_music_graph.py"
SPEC = importlib.util.spec_from_file_location("build_wikidata_music_graph", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _old_retained_triple(line: bytes):
    match = MODULE.NT_LINE_B.match(line)
    if match is None:
        return None
    subject, predicate, value = match.groups()
    if not predicate.startswith(MODULE.PROP_NS_B):
        return None
    prop = predicate[len(MODULE.PROP_NS_B) :]
    if prop not in MODULE.KEEP_PROPS_B:
        return None
    return subject, prop, value


def _fast_retained_triple(line: bytes):
    match = MODULE.KEEP_NT_LINE_B.match(line)
    return match.groups() if match is not None else None


def test_fast_matcher_preserves_retained_triples() -> None:
    lines = [
        b'<http://www.wikidata.org/entity/Q1> <http://www.wikidata.org/prop/direct/P434> "mbid" .',
        b'  <http://www.wikidata.org/entity/Q2> <http://www.wikidata.org/prop/direct/P31> <http://www.wikidata.org/entity/Q215380> .  ',
        b'<http://www.wikidata.org/entity/Q3> <http://www.wikidata.org/prop/direct/P625> "Point(-118.2 34.0)"^^<http://www.opengis.net/ont/geosparql#wktLiteral> .',
        b'<http://www.wikidata.org/entity/Q4> <http://www.wikidata.org/prop/direct/P999999> "irrelevant" .',
        b'<http://www.wikidata.org/entity/Q5> <http://example.com/P434> "wrong namespace" .',
        b'# comment',
        b'malformed',
        b'',
    ]

    assert [_fast_retained_triple(line) for line in lines] == [
        _old_retained_triple(line) for line in lines
    ]


def test_fast_matcher_covers_every_allowed_property() -> None:
    for prop in MODULE.KEEP_PROPS_B:
        line = (
            b'<http://www.wikidata.org/entity/Q42> <'
            + MODULE.PROP_NS_B
            + prop
            + b'> "value" .'
        )
        assert _fast_retained_triple(line) == _old_retained_triple(line)


def test_p31_and_enrichment_only_subjects_are_not_identity_anchors() -> None:
    props = {
        "P31": [MODULE.ENTITY_NS_B + b"Q5"],
        "P625": [b"Point(-118.2 34.0)"],
        "P856": [b"https://example.test"],
    }

    assert MODULE.should_keep_subject(props, {b"Q5"}) is False


def test_music_identity_or_typed_generic_id_anchors_subject() -> None:
    assert MODULE.should_keep_subject(
        {"P434": [b"12345678-1234-1234-1234-123456789abc"],
         "P31": [MODULE.ENTITY_NS_B + b"Q5"]},
        {b"Q5"},
    ) is True
    assert MODULE.should_keep_subject(
        {"P214": [b"123456"], "P31": [MODULE.ENTITY_NS_B + b"Q639669"]},
        {b"Q639669"},
    ) is True
    assert MODULE.should_keep_subject(
        {"P214": [b"123456"], "P31": [MODULE.ENTITY_NS_B + b"Q5"]},
        {b"Q5"},
    ) is False
    assert MODULE.should_keep_subject(
        {"P1566": [b"98765"], "P31": [MODULE.ENTITY_NS_B + b"Q811430"]},
        {b"Q811430"},
    ) is True


def test_emit_normalizes_qids_and_separates_types_and_enrichment(monkeypatch):
    # Avoid constructing a real R2 client while retaining the production emit
    # path by initializing only the reducer fields used by emit().
    builder = MODULE.SubgraphBuilder.__new__(MODULE.SubgraphBuilder)
    builder.stats = MODULE.Counter()
    builder.run_id = "fixture"
    builder.now = "2026-08-30T00:00:00Z"
    builder._pending = 0
    names = ("music_entities", "entity_types", "entity_ids", "artist_ids",
             "venue_ids", "place_ids", "coordinates", "locations", "websites",
             "inceptions", "genres", "relationships")
    for name in names:
        setattr(builder, name, [])
    builder.spill_paths = {name: [] for name in names}
    monkeypatch.setattr(MODULE, "SPILL_EVERY", 10**9)
    qid = MODULE.ENTITY_NS_B
    builder.emit(
        qid + b"Q123",
        {
            "P31": [qid + b"Q639669"],
            "P434": [b"12345678-1234-1234-1234-123456789ABC"],
            "P17": [qid + b"Q30"],
            "P136": [qid + b"Q188451"],
            "P175": [qid + b"Q456"],
        },
        {b"Q639669"},
    )
    assert builder.music_entities[0]["qid"] == "Q123"
    assert builder.entity_types[0]["type_qid"] == "Q639669"
    assert [r["external_id_property"] for r in builder.artist_ids] == ["P434"]
    assert not any(r["external_id_property"] == "P31" for r in builder.entity_ids)
    assert builder.locations[0]["location_qid"] == "Q30"
    assert builder.genres[0]["genre_qid"] == "Q188451"
    assert builder.relationships[0]["object_qid"] == "Q456"
    for name in names:
        for row in getattr(builder, name):
            assert row["source_system"] == MODULE.SOURCE_SYSTEM
            assert row["knowledge_time"] == builder.now
            assert row["ingested_at"] == builder.now


def test_classification_does_not_promote_generic_place_to_live_venue():
    assert MODULE.SubgraphBuilder.classify(object.__new__(MODULE.SubgraphBuilder), {b"Q41176"}) == "PLACE"
    assert MODULE.SubgraphBuilder.classify(object.__new__(MODULE.SubgraphBuilder), {b"Q811430"}) == "LIVE_MUSIC_VENUE"


def test_restart_from_zero_fails_closed_instead_of_replaying_compressed_block(monkeypatch):
    class Fake:
        def get_object(self, **kwargs):
            raise AssertionError("R2 must not be touched for an unsafe resume")

    monkeypatch.setattr(MODULE, "r2_client", lambda: Fake())
    with pytest.raises(RuntimeError, match="restart the reducer from byte zero"):
        MODULE.stream_nt_lines("bucket", "key", MODULE.Queue(), start_consumed=1)


def test_spills_are_run_scoped_and_do_not_delete_existing_keys(tmp_path, monkeypatch):
    class FakeR2:
        def __init__(self):
            self.puts = []
            self.deletes = []
            self.objects = {}

        def upload_fileobj(self, handle, bucket, key, ExtraArgs=None):
            payload = handle.read()
            self.puts.append(key)
            self.objects[key] = {
                "payload": payload,
                "metadata": (ExtraArgs or {}).get("Metadata", {}),
            }

        def head_object(self, **kwargs):
            obj = self.objects[kwargs["Key"]]
            return {
                "ContentLength": len(obj["payload"]),
                "Metadata": obj["metadata"],
            }

        def delete_object(self, **kwargs):
            self.deletes.append(kwargs["Key"])

    fake = FakeR2()
    monkeypatch.setattr(MODULE, "r2_client", lambda: fake)
    monkeypatch.setattr(MODULE, "SPILL_DIR", str(tmp_path / "spill"))
    monkeypatch.setattr(MODULE, "SPILL_EVERY", 1)
    builder = MODULE.SubgraphBuilder(run_id="fixture-run")
    builder.emit(
        MODULE.ENTITY_NS_B + b"Q1",
        {"P31": [MODULE.ENTITY_NS_B + b"Q639669"],
         "P434": [b"12345678-1234-1234-1234-123456789abc"]},
        {b"Q639669"},
    )
    assert fake.puts
    assert all("/_spill/fixture-run/" in key for key in fake.puts)
    assert fake.deletes == []


def test_source_identity_is_exact_and_fails_closed():
    class FakeR2:
        def __init__(self, size, etag):
            self.size = size
            self.etag = etag

        def head_object(self, **_kwargs):
            return {"ContentLength": self.size, "ETag": f'"{self.etag}"'}

    assert MODULE.verified_source_identity(
        FakeR2(MODULE.RAW_BYTES, MODULE.RAW_ETAG)
    ) == (MODULE.RAW_BYTES, MODULE.RAW_ETAG)
    with pytest.raises(RuntimeError, match="source identity changed"):
        MODULE.verified_source_identity(FakeR2(MODULE.RAW_BYTES - 1, MODULE.RAW_ETAG))
    with pytest.raises(RuntimeError, match="source identity changed"):
        MODULE.verified_source_identity(FakeR2(MODULE.RAW_BYTES, "different"))


def test_external_id_shapes_are_normalized_and_invalid_anchors_are_rejected():
    assert MODULE.normalize_external_id(
        "P434", "12345678-1234-1234-1234-123456789ABC"
    ) == "12345678-1234-1234-1234-123456789abc"
    assert MODULE.normalize_external_id("P2397", "UCabcdefghijklmnopqrstuv") == (
        "UCabcdefghijklmnopqrstuv"
    )
    assert MODULE.normalize_external_id("P2397", "channel-name") is None
    assert MODULE.normalize_external_id("P1902", "too-short") is None
    assert MODULE.should_keep_subject(
        {"P434": [b"malformed"]}, {b"Q639669"}
    ) is False


def test_bounded_limit_cannot_publish(monkeypatch):
    monkeypatch.setattr("sys.argv", ["build_wikidata_music_graph.py", "--limit", "1"])
    with pytest.raises(SystemExit) as exc:
        MODULE.main()
    assert exc.value.code == 2
