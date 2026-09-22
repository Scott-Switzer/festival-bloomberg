"""Offline contracts for Jev triage (no network, no key)."""

from __future__ import annotations

from festival_bloomberg.intelligence import jev_triage
from festival_bloomberg.intelligence.jev import JevClient


class _FailingTransport:
    def request(self, *a, **k):
        raise TimeoutError("no network in tests")


def _dossier(text="Tickets go on sale March 3."):
    return {"target": {"artist": "A", "venue": "V", "city": None, "date": "2024-03-05"},
            "source": {"domain": "x.example", "published_at": None},
            "text": text, "html": f"<p>{text}</p>"}


def test_failure_falls_back_to_escalate():
    client = JevClient(api_key="sk-test", transport=_FailingTransport())
    pred = jev_triage.triage_dossier(client, _dossier())
    assert pred["status"] != "OK"
    assert jev_triage.decide_triage(pred) == "FALLBACK_ESCALATE"


def test_unconfigured_never_skips():
    client = JevClient(api_key=None, transport=_FailingTransport())
    pred = jev_triage.triage_dossier(client, _dossier())
    assert pred["status"] == "NOT_CONFIGURED"
    assert jev_triage.decide_triage(pred) == "FALLBACK_ESCALATE"


def test_skip_only_on_confidently_low_worth():
    assert jev_triage.decide_triage({"status": "OK", "worth_further_extraction": 0.1}) == "SKIP"
    assert jev_triage.decide_triage({"status": "OK", "worth_further_extraction": 0.16}) == "ESCALATE"
    assert jev_triage.decide_triage({"status": "OK"}) == "FALLBACK_ESCALATE"


def test_state_is_bounded_and_id_free():
    d = _dossier("x" * 20000)
    state = jev_triage.build_state(d, max_chars=6000)
    assert len(state["text"]) <= 6100
    assert set(state) == {"target_event", "source", "text", "policy"}
    assert "mbid::" not in str(state)


def test_question_map_is_atomic():
    qs = jev_triage.build_questions()
    types = {v["type"] for v in qs.values()}
    assert types == {"noul", "choice"}
    assert qs["date_semantics"]["type"] == "choice"
    assert set(qs["date_semantics"]["criteria"]) == {
        "EXACT_DATE", "UPPER_BOUND", "RELATIVE_WITH_ANCHOR",
        "RELATIVE_WITHOUT_ANCHOR", "NO_DATE_EVIDENCE"}
