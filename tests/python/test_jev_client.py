"""Offline contract tests for the Jev evaluation client (no network)."""

from __future__ import annotations

import json

from festival_bloomberg.intelligence.jev import JevClient


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()


class _Transport:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def request(self, method, url, headers=None, body=None, timeout_seconds=None):
        self.calls += 1
        self.last_headers = dict(headers or {})
        self.last_body = body
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def _ok_payload(questions):
    answers = {}
    for qid, q in questions.items():
        if q["type"] == "choice":
            opts = list((q.get("criteria") or {}).keys())
            answers[qid] = {"type": "choice", "choice": opts[0], "confidence": 0.9,
                            "probabilities": {o: (0.9 if o == opts[0] else 0.05) for o in opts}}
        elif q["type"] == "noul":
            answers[qid] = {"type": "noul", "noul": 0.8}
        else:
            answers[qid] = {"type": "score", "score": 2.0, "confidence": 0.7}
    return {"model": "jev-1.13.0", "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 5}}


def test_not_configured_makes_zero_calls():
    t = _Transport([_Resp(200, {})])
    c = JevClient(api_key=None, transport=t)
    assert c.is_configured is False
    r = c.evaluate("hi", {"q": {"type": "noul", "instructions": "x?"}})
    assert r == {"ok": False, "status": "NOT_CONFIGURED", "answers": {}}
    assert t.calls == 0


def test_success_records_model_latency_usage():
    qs = {"route": {"type": "choice", "instructions": "pick",
                    "criteria": {"a": "first", "b": "second"}}}
    t = _Transport([_Resp(200, _ok_payload(qs))])
    c = JevClient(api_key="sk-test", transport=t)
    r = c.evaluate("state text", qs)
    assert r["ok"] is True
    assert r["answers"]["route"]["choice"] == "a"
    assert r["model"] == "jev-1.13.0"
    assert r["usage"] == {"input_tokens": 100, "output_tokens": 5}
    assert isinstance(r["latency_ms"], int) and r["latency_ms"] >= 0
    # Secret travels only in the Authorization header, never in errors.
    assert t.last_headers["Authorization"] == "Bearer sk-test"
    assert "sk-test" not in json.dumps({k: v for k, v in r.items() if k != "answers"})


def test_undeclared_choice_fails_closed():
    qs = {"route": {"type": "choice", "instructions": "pick",
                    "criteria": {"a": "first"}}}
    bad = {"model": "jev-x", "answers": {
        "route": {"type": "choice", "choice": "ZZZ", "confidence": 0.99}},
        "usage": {}}
    c = JevClient(api_key="k", transport=_Transport([_Resp(200, bad)]))
    r = c.evaluate("s", qs)
    assert r["ok"] is False and r["status"] == "MALFORMED_RESPONSE"


def test_transient_retries_then_succeeds():
    qs = {"u": {"type": "noul", "instructions": "unsafe?"}}
    t = _Transport([_Resp(429, {}), _Resp(200, _ok_payload(qs))])
    c = JevClient(api_key="k", transport=t)
    r = c.evaluate("s", qs)
    assert r["ok"] is True and t.calls == 2 and r["attempts"] == 2


def test_non_transient_does_not_retry():
    t = _Transport([_Resp(401, {"error": "bad key"})])
    c = JevClient(api_key="k", transport=t)
    r = c.evaluate("s", {"u": {"type": "noul", "instructions": "x"}})
    assert r == {"ok": False, "status": "HTTP_401", "answers": {},
                 "attempts": 1, "latency_ms": r["latency_ms"]}


def test_network_error_degrades_without_secret():
    t = _Transport([TimeoutError("boom")])
    c = JevClient(api_key="sk-secret-xyz", transport=t)
    r = c.evaluate("s", {"u": {"type": "noul", "instructions": "x"}})
    assert r["ok"] is False and r["status"] == "NETWORK_ERROR"
    assert "sk-secret-xyz" not in json.dumps(r)
