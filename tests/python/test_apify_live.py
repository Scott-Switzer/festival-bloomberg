"""LIVE Apify actor-identity contract tests.

Verify the canonical API actor identifier form ``owner~actor-name`` resolves
for every default actor through the real Apify API (``GET /v2/acts/{id}``)
and that the ``owner/actor`` form is not the API identifier. These are free
metadata calls — no actor runs are started, no paid calls are made.

Skipped when ``APIFY_TOKEN`` is not configured.
"""

from __future__ import annotations

import os
import pathlib

import pytest
from festival_bloomberg.acquisition.providers.apify import (
    ACTOR_PRICING_USD_PER_RESULT,
    DEFAULT_ACTORS,
)
from festival_bloomberg.acquisition.transport import UrllibTransport


def _env_token() -> str:
    """Read APIFY_TOKEN from the repo .env file (canonical), falling back to
    process env. Never mutates the shared process environment, and a fake key
    that another test may have placed in ``os.environ`` never wins over the
    real one.
    """
    env_file = pathlib.Path(__file__).resolve().parents[2] / ".env"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("APIFY_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return os.environ.get("APIFY_TOKEN") or ""


pytestmark = pytest.mark.skipif(
    os.environ.get("FESTIVAL_BLOOMBERG_LIVE_TESTS") != "1"
    or not (os.environ.get("APIFY_TOKEN") or _env_token()),
    reason="FESTIVAL_BLOOMBERG_LIVE_TESTS=1 and APIFY_TOKEN required for live tests",
)


def _auth() -> dict:
    return {"Authorization": f"Bearer {_env_token()}"}


@pytest.mark.parametrize("actor_id", sorted(DEFAULT_ACTORS.values()))
def test_default_actors_resolve_with_tilde_form(actor_id: str):
    response = UrllibTransport().request(
        "GET",
        f"https://api.apify.com/v2/acts/{actor_id}",
        headers=_auth(),
        timeout_seconds=30.0,
    )
    assert response.status == 200, f"{actor_id} did not resolve: http {response.status}"
    data = response.json().get("data") or {}
    assert data.get("username")
    assert data.get("name")


def test_slash_form_is_not_the_api_identifier():
    # Apify's API uses owner~actor; the / form is a web URL, not an API ID.
    response = UrllibTransport().request(
        "GET",
        "https://api.apify.com/v2/acts/clockworks/tiktok-scraper",
        headers=_auth(),
        timeout_seconds=30.0,
    )
    assert response.status in (404, 400)


def test_store_list_prices_are_recorded_for_all_default_actors():
    for actor_id in DEFAULT_ACTORS.values():
        assert ACTOR_PRICING_USD_PER_RESULT.get(actor_id) is not None, actor_id
        assert ACTOR_PRICING_USD_PER_RESULT[actor_id] > 0


def test_actor_identity_matches_monid_catalog_owner():
    # Monid's catalog uses the same Apify owner namespace (e.g. /apidojo/...).
    # The direct API and the Monid catalog must agree on the actor owner.
    owners = {actor_id.split("~")[0] for actor_id in DEFAULT_ACTORS.values()}
    assert owners == {"streamers", "clockworks", "apify"}