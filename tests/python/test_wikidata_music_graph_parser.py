from __future__ import annotations

import importlib.util
from pathlib import Path


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
        {"P434": [b"musicbrainz-id"], "P31": [MODULE.ENTITY_NS_B + b"Q5"]},
        {b"Q5"},
    ) is True
    assert MODULE.should_keep_subject(
        {"P214": [b"viaf-id"], "P31": [MODULE.ENTITY_NS_B + b"Q639669"]},
        {b"Q639669"},
    ) is True
    assert MODULE.should_keep_subject(
        {"P214": [b"viaf-id"], "P31": [MODULE.ENTITY_NS_B + b"Q5"]},
        {b"Q5"},
    ) is False
    assert MODULE.should_keep_subject(
        {"P1566": [b"geonames-id"], "P31": [MODULE.ENTITY_NS_B + b"Q811430"]},
        {b"Q811430"},
    ) is True
