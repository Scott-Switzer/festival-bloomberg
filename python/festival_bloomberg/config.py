"""Safe credential/config status for the Festival Intelligence estate.

This module answers "which providers are configured?" WITHOUT ever exposing a
secret value. It reports, per environment-variable name:

    - ``present``  — the name exists in the environment or the local ``.env``
    - ``nonempty`` — a non-blank value was found
    - ``source``   — ``env`` (already exported) or ``dotenv`` (loaded from the
                     local repo ``.env``) or ``missing``

Rules (enforced here and tested):

- Values are NEVER returned, logged, or printed. Only booleans + the name.
- OS environment always wins over the local ``.env`` (see ``localenv.py``).
- The loader is only invoked once per process (idempotent); the local ``.env``
  is gitignored and never loaded in hermetic CI (which sets
  ``FESTIVAL_BLOOMBERG_SKIP_ENV_FILE=1``).
"""

from __future__ import annotations

import os
from typing import Any

from .localenv import load_local_env

#: Environment-variable names the project recognizes. This is the registry of
#: NAMES only — values live in the environment / local .env, never here.
KNOWN_KEYS: tuple[str, ...] = (
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "TICKETMASTER_API_KEY",
    "TICKETMASTER_CONSUMER_KEY",
    "YOUTUBE_API_KEY",
    "MONID_API_KEY",
    "APIFY_TOKEN",
    "SETLISTFM_API_KEY",
    "SEATGEEK_CLIENT_ID",
    "SEATGEEK_CLIENT_SECRET",
    "CENSUS_API_KEY",
    "BLS_API_KEY",
    "NOAA_API_TOKEN",
    "NVIDIA_API_KEY",
    "DEEPSEEK_API_KEY",
    "MUSICBRAINZ_USER_AGENT",
    "MUSICBRAINZ_API_KEY",
    "JAMBASE_API_KEY",
)

#: Single-key auth: any one of these being set makes the provider "configured".
PROVIDER_KEYS: dict[str, tuple[str, ...]] = {
    "ticketmaster": ("TICKETMASTER_API_KEY", "TICKETMASTER_CONSUMER_KEY"),
    "youtube": ("YOUTUBE_API_KEY",),
    "monid": ("MONID_API_KEY",),
    "apify": ("APIFY_TOKEN",),
    "setlistfm": ("SETLISTFM_API_KEY",),
    "seatgeek": ("SEATGEEK_CLIENT_ID",),
    "spotify": ("SPOTIFY_CLIENT_ID",),
    "census": ("CENSUS_API_KEY",),
    "bls": ("BLS_API_KEY",),
    "nws": ("NOAA_API_TOKEN",),
    "nvidia": ("NVIDIA_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "musicbrainz": ("MUSICBRAINZ_USER_AGENT", "MUSICBRAINZ_API_KEY"),
    "jambase": ("JAMBASE_API_KEY",),
}

#: Providers that require NO credential at all (public APIs). Their status must
#: never depend on an imaginary key.
PUBLIC_NO_AUTH_PROVIDERS: frozenset[str] = frozenset(
    {"listenbrainz", "gdelt", "nws", "wikimedia", "wikipedia", "wikidata",
     "commoncrawl", "openstreetmap"}
)


def load_config() -> int:
    """Load the local ``.env`` (process env wins). Returns count loaded."""
    return load_local_env()


def credential_status(name: str) -> dict[str, Any]:
    """Presence only for one environment-variable name. NEVER the value."""
    before = set(os.environ)
    load_local_env()
    value = os.environ.get(name)
    if name in os.environ:
        source = "env" if name in before else "dotenv"
    else:
        source = "missing"
    return {
        "name": name,
        "present": name in os.environ,
        "nonempty": bool(value and value.strip()),
        "source": source,
    }


def provider_credential_status() -> dict[str, dict[str, Any]]:
    """Per-provider presence-only credential status (no secret values)."""
    load_local_env()
    out: dict[str, dict[str, Any]] = {}
    for provider, keys in PROVIDER_KEYS.items():
        statuses = [credential_status(k) for k in keys]
        out[provider] = {
            "provider": provider,
            "keys": [s["name"] for s in statuses],
            "present_any": any(s["present"] for s in statuses),
            "nonempty_any": any(s["nonempty"] for s in statuses),
        }
    return out


def all_credential_status() -> dict[str, dict[str, Any]]:
    load_local_env()
    return {name: credential_status(name) for name in KNOWN_KEYS}
