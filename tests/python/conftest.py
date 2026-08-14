"""Pytest path bootstrap + shared offline fixtures.

All tests run OFFLINE: every provider request goes through the scripted
:class:`FakeTransport`, never the network, and no test makes a paid call.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Tests are hermetic: never load the developer's local .env into os.environ
# during the suite. Env-file loading is tested explicitly in test_localenv.py.
os.environ.setdefault("FESTIVAL_BLOOMBERG_SKIP_ENV_FILE", "1")

ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from festival_bloomberg.acquisition.transport import HttpResponse  # noqa: E402


class FakeTransport:
    """Scripted transport: returns pre-arranged responses, records requests.

    Responses may be plain payloads (status 200) or ``(status, payload)``
    tuples. Exhausted scripts fall back to the ``default_status`` with an
    error payload so a test that forgets a response fails loudly.
    """

    def __init__(self, responses=None, default_status: int = 500) -> None:
        self._responses = list(responses or [])
        self.default_status = default_status
        self.requests: list[dict] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        params: dict | None = None,
        body=None,
        timeout_seconds: float = 30.0,
    ) -> HttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "body": body,
            }
        )
        if self._responses:
            item = self._responses.pop(0)
            if isinstance(item, tuple):
                status, payload = item
            else:
                status, payload = self.default_status, item
        else:
            status, payload = self.default_status, {"error": "no scripted response"}
        if isinstance(payload, bytes):
            body_bytes = payload
        elif payload is None:
            body_bytes = b""
        else:
            body_bytes = json.dumps(payload).encode("utf-8")
        return HttpResponse(status, body_bytes, {})


def make_request(
    *,
    entity_id: str = "radiohead",
    platform: str = "youtube",
    query: str = "Radiohead",
    market_id: str | None = None,
    cutoff=None,
    start_time=None,
    max_records: int = 10,
    max_cost_usd: float = 0.0,
    preferred_providers: tuple[str, ...] = (),
    commercial_context: str = "research",
):
    from festival_bloomberg.acquisition.contracts import AcquisitionRequest

    return AcquisitionRequest.new(
        entity_id=entity_id,
        entity_type="artist",
        platform=platform,
        query=query,
        market_id=market_id,
        knowledge_cutoff=cutoff,
        start_time=start_time,
        max_records=max_records,
        max_cost_usd=max_cost_usd,
        preferred_providers=preferred_providers,
        commercial_context=commercial_context,
    )
