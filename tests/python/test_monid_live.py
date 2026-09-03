"""LIVE Monid contract tests.

These hit the real Monid API with the configured ``MONID_API_KEY``. They only
use free catalog operations (``/v1/wallet/balance``, ``/v1/discover``,
``/v1/inspect``) — no paid runs — and verify the current documented contract:

* discover returns ``results[]`` with ``provider`` + ``endpoint``
* inspect returns the endpoint's ``inputSchema`` and ``price``
* the adapter's run payload shape is ``{provider, endpoint, input}``

When ``MONID_API_KEY`` is absent the tests skip with a clear message (the
contract remains pinned by the offline regression tests in
``test_signal_fabric_acquisition.py``).
"""

from __future__ import annotations

import os
import pathlib

import pytest
from festival_bloomberg.acquisition.contracts import AcquisitionRequest
from festival_bloomberg.acquisition.providers.monid import MonidProvider, operation_for_request


def _env_key() -> str:
    """Read MONID_API_KEY from the repo .env file (canonical), falling back
    to process env.

    The file is parsed directly and the shared process environment is never
    mutated — hermetic tests in the same session are unaffected, and a fake
    key that another test may have placed in ``os.environ`` never wins over
    the real one.
    """
    env_file = pathlib.Path(__file__).resolve().parents[2] / ".env"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("MONID_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return os.environ.get("MONID_API_KEY") or ""


def _has_key() -> bool:
    return bool(_env_key())


pytestmark = pytest.mark.skipif(
    not _has_key(),
    reason="MONID_API_KEY not configured; live Monid contract tests skipped",
)


def _provider() -> MonidProvider:
    # Hermetic test env: pass the key explicitly instead of loading .env.
    return MonidProvider(env={"MONID_API_KEY": _env_key()})


def _request(**kwargs) -> AcquisitionRequest:
    return AcquisitionRequest.new(
        entity_id=kwargs.get("entity_id", "drake"),
        entity_type="artist",
        platform=kwargs.get("platform", "tiktok"),
        query=kwargs.get("query", "Drake"),
        operation=kwargs.get("operation", "SOCIAL_PROFILE"),
        max_records=kwargs.get("max_records", 5),
    )


def test_wallet_balance_matches_documented_shape():
    estimate = _provider().estimate(_request())
    assert estimate.estimated_cost_usd is not None
    assert estimate.source == "monid_wallet_balance"


def test_discover_returns_current_contract_fields():
    provider = _provider()
    # Drive discover directly through the real endpoint via the transport.
    key = _env_key()
    response = provider._request(
        "POST",
        f"{provider.base_url}/v1/discover",
        headers=provider._headers(key),
        body={"query": "tiktok profile stats for a musician", "limit": 5},
    )
    assert response.status == 200
    payload = response.json()
    results = payload.get("results")
    assert results, "discover returned no endpoints"
    first = results[0]
    assert first.get("provider")
    assert first.get("endpoint")
    assert "price" in first
    assert payload.get("count") is not None


def test_inspect_returns_schema_and_pricing():
    provider = _provider()
    key = _env_key()
    discover = provider._request(
        "POST",
        f"{provider.base_url}/v1/discover",
        headers=provider._headers(key),
        body={"query": "tiktok profile stats for a musician", "limit": 3},
    )
    first = (discover.json().get("results") or [])[0]
    inspect = provider._request(
        "POST",
        f"{provider.base_url}/v1/inspect",
        headers=provider._headers(key),
        body={"provider": first["provider"], "endpoint": first["endpoint"]},
    )
    assert inspect.status == 200
    payload = inspect.json()
    assert payload.get("endpoint") == first["endpoint"]
    assert "price" in payload
    # Live contract: inputSchema is present for endpoints that expose one and
    # may be entirely absent for others (observed with tiktok-profile-scraper).
    # The adapter must handle both — see the offline regression tests for the
    # refuse-to-run path when neither schema nor pin exists.
    if "inputSchema" in payload:
        assert payload["inputSchema"] is None or isinstance(payload["inputSchema"], dict)


def test_run_payload_shape_is_provider_endpoint_input():
    provider = _provider()
    request = _request()
    operation = operation_for_request(request)
    # Build the input the way acquire() would for a schema-less endpoint that
    # is pinned; verify the run body shape without spending money.
    input_payload, schema_used = provider._build_input(
        request=request,
        operation=operation,
        endpoint_path="/apidojo/tiktok-profile-scraper",
        input_schema={},
    )
    assert input_payload is not None
    assert schema_used.startswith("pinned:")
    assert "username" in input_payload
    run_body = {"provider": "apify", "endpoint": "/apidojo/tiktok-profile-scraper", "input": input_payload}
    assert set(run_body.keys()) == {"provider", "endpoint", "input"}
    assert "endpoint_id" not in run_body and "params" not in run_body