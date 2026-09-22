"""TypeSafe Jev evaluation client (fail-closed).

Jev is TypeSafe's System One decision model: one HTTPS call evaluates a map
of typed questions (``choice`` / ``noul`` / ``score``) against a ``state``
and returns structured answers with calibrated confidence. It generates no
prose, selects no IDs, and persists nothing.

Contract (mirrors ``NimClient`` conventions):

- Without ``TYPESAFE_API_KEY`` the client reports ``NOT_CONFIGURED`` and
  makes ZERO network calls.
- Timeouts are bounded; retries happen ONLY on documented transient
  conditions (HTTP 429 / 529, per https://docs.typesafe.ai/api) with
  exponential backoff, at most 2 retries.
- Every response is structurally validated against the question map the
  caller supplied. Anything malformed degrades to a closed failure dict —
  never a fabricated answer.
- Model id, latency, and token usage are always recorded on success.
- No secret is ever logged, placed in an error string, or persisted.

Official endpoint: ``POST https://api.typesafe.ai/v1/systemone``
(see ``docs/research/jev-calibration-v1.md`` for measured vs vendor claims).
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..localenv import load_local_env

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1"
SYSTEMONE_PATH = "/systemone"
DEFAULT_MODEL = "jev-latest"

#: HTTP statuses the official docs mark transient (retry with backoff).
TRANSIENT_STATUSES = frozenset({429, 529})
MAX_RETRIES = 2
BACKOFF_BASE_SECONDS = 1.0

VALID_QUESTION_TYPES = frozenset({"choice", "noul", "score"})


class JevClient:
    """Minimal typed HTTP client for the TypeSafe systemone endpoint."""

    name = "jev"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        transport: Any = None,
    ) -> None:
        load_local_env()
        import os
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.transport = transport

    # -- configuration ------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _transport(self) -> Any:
        if self.transport is not None:
            return self.transport
        from ..acquisition.transport import UrllibTransport
        return UrllibTransport()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -- API ----------------------------------------------------------------
    def evaluate(
        self,
        state: Any,
        questions: dict[str, dict[str, Any]],
        *,
        model: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        """Evaluate typed questions against a state. Fail-closed.

        Returns ``{"ok": True, "answers": {...}, "model": ..., "usage": {...},
        "latency_ms": ...}`` on success, else ``{"ok": False, "status": ...}``
        where status is one of NOT_CONFIGURED / INVALID_REQUEST / NETWORK_ERROR
        / HTTP_<code> / MALFORMED_RESPONSE. No exception escapes except on
        caller-side programming errors (non-serializable payload).
        """
        if not self.is_configured:
            return {"ok": False, "status": "NOT_CONFIGURED", "answers": {}}
        if not isinstance(questions, dict) or not questions:
            return {"ok": False, "status": "INVALID_REQUEST", "answers": {}}
        for qid, q in questions.items():
            if not isinstance(q, dict) or q.get("type") not in VALID_QUESTION_TYPES:
                return {"ok": False, "status": "INVALID_REQUEST", "answers": {}}
        payload = {
            "state": state,
            "model": model or self.model,
            "questions": questions,
        }
        try:
            body = json.dumps(payload, default=str)
        except (ValueError, TypeError):
            return {"ok": False, "status": "INVALID_REQUEST", "answers": {}}

        url = f"{self.base_url}{SYSTEMONE_PATH}"
        started = time.monotonic()
        resp = None
        attempts = 0
        while True:
            attempts += 1
            try:
                resp = self._transport().request(
                    "POST", url, headers=self._headers(),
                    body=body.encode("utf-8"), timeout_seconds=timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001 — network degrades gracefully
                return {"ok": False, "status": "NETWORK_ERROR", "answers": {},
                        "detail": f"{type(exc).__name__}", "attempts": attempts}
            if resp.status not in TRANSIENT_STATUSES or attempts > MAX_RETRIES:
                break
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)))
        latency_ms = int((time.monotonic() - started) * 1000)

        if resp.status != 200:
            return {"ok": False, "status": f"HTTP_{resp.status}", "answers": {},
                    "attempts": attempts, "latency_ms": latency_ms}
        try:
            decoded = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"ok": False, "status": "MALFORMED_RESPONSE", "answers": {},
                    "attempts": attempts, "latency_ms": latency_ms}
        answers, error = _validate_answers(decoded.get("answers"), questions)
        if error is not None:
            return {"ok": False, "status": "MALFORMED_RESPONSE", "answers": {},
                    "detail": error, "attempts": attempts, "latency_ms": latency_ms}
        usage = decoded.get("usage") if isinstance(decoded.get("usage"), dict) else {}
        return {
            "ok": True,
            "status": "OK",
            "answers": answers,
            "model": decoded.get("model") or (model or self.model),
            "usage": {
                "input_tokens": _safe_int(usage.get("input_tokens")),
                "output_tokens": _safe_int(usage.get("output_tokens")),
            },
            "latency_ms": latency_ms,
            "attempts": attempts,
        }


def _safe_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _validate_answers(
    answers: Any, questions: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], str | None]:
    """Validate the answers map against the requested questions.

    Returns (answers, None) on success, ({}, reason) on any mismatch. Choice
    answers must select a declared option; noul must be a 0..1 number; score
    must be numeric. Confidence, when present, must be 0..1.
    """
    if not isinstance(answers, dict):
        return {}, "answers_not_object"
    for qid, q in questions.items():
        if qid not in answers or not isinstance(answers[qid], dict):
            return {}, f"missing_answer:{qid}"
        ans = answers[qid]
        qtype = q["type"]
        if ans.get("type") != qtype:
            return {}, f"type_mismatch:{qid}"
        conf = ans.get("confidence")
        if conf is not None and not (isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0):
            return {}, f"bad_confidence:{qid}"
        if qtype == "choice":
            options = q.get("criteria") or q.get("options") or {}
            if isinstance(options, dict):
                valid = set(options.keys())
            elif isinstance(options, (list, tuple)):
                valid = {o.get("key", o) if isinstance(o, dict) else o for o in options}
            else:
                valid = set()
            if ans.get("choice") not in valid:
                return {}, f"undeclared_choice:{qid}"
            probs = ans.get("probabilities")
            if probs is not None:
                if not isinstance(probs, dict) or set(probs) - valid:
                    return {}, f"bad_probabilities:{qid}"
                for pv in probs.values():
                    if not isinstance(pv, (int, float)) or not 0.0 <= pv <= 1.0:
                        return {}, f"bad_probabilities:{qid}"
        elif qtype == "noul":
            n = ans.get("noul")
            if not isinstance(n, (int, float)) or not 0.0 <= n <= 1.0:
                return {}, f"bad_noul:{qid}"
        elif qtype == "score":
            s = ans.get("score")
            if not isinstance(s, (int, float)):
                return {}, f"bad_score:{qid}"
    return answers, None
