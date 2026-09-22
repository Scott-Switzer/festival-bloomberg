"""Structural contracts for the triage eval corpus (no network, no key)."""

from __future__ import annotations

from fixtures.triage_corpus import build


def _corpus():
    return build()


def test_corpus_meets_size_and_class_minimums():
    d = _corpus()
    assert len(d) >= 300
    by_class = {}
    for x in d:
        by_class.setdefault(x["dossier_id"].split("-")[0], []).append(x)
    assert len(by_class.get("A", [])) >= 75
    assert len(by_class.get("B", [])) >= 100
    assert len(by_class.get("C", [])) >= 100
    assert len(by_class.get("D", [])) >= 50


def test_split_is_leakage_safe_and_held_sufficient():
    d = _corpus()
    held = [x for x in d if x["split"] == "held"]
    assert len(held) / len(d) >= 0.30
    # Same anchor artist never crosses splits (spot-check identity groups).
    from collections import defaultdict
    by_artist = defaultdict(set)
    for x in d:
        by_artist[x["target"]["artist"]].add(x["split"])
    assert all(len(v) == 1 for v in by_artist.values())


def test_gold_label_invariants():
    d = _corpus()
    for x in d:
        assert x["verifier_admissible"] or not x["worth_full_extraction"] or x["class"] in ("B", "D"), x["dossier_id"]
    adm_worth = [x for x in d if x["verifier_admissible"] and x["worth_full_extraction"]]
    assert len(adm_worth) >= 200
    # Class C is never worth extraction; Class A always admissible.
    assert all(not x["worth_full_extraction"] for x in d if x["class"] == "C")
    assert all(x["verifier_admissible"] for x in d if x["class"] == "A")
