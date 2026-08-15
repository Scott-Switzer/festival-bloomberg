"""Provider scaffolds for the intelligence terminal's data expansion.

Each provider is fail-closed: without an authorized key/transport it reports
NOT_CONFIGURED and makes ZERO network calls. Rights/commercial status is kept
in ``flywheel.source_registry``; these classes only add the operational
status + a bounded acquisition entry point that a keyed run can use.

Provider semantics are honored literally:

- ListenBrainz is an ATTENTION/CONSUMPTION SAMPLE (CC0), NEVER local demand.
- GDELT stores metadata (URL/domain/title/timestamp), never full article text.
- NWS forecasts are snapshotted at their forecast-observation time; realized
  weather never leaks into an earlier forecast state.
- Census ACS retains vintage + estimate + margin of error; missing values are
  NULL, never zero.
- JamBase is OPTIONAL and strictly bounded (trial); it is not ground truth and
  is never a hard dependency of the terminal.
- SeatGeek remains DISABLED for automated ingestion (terms review).
"""

from __future__ import annotations

import os
from typing import Any

NOT_CONFIGURED = "NOT_CONFIGURED"
OPERATIONAL = "OPERATIONAL"
BLOCKED = "BLOCKED"
PARTIAL = "PARTIAL"


def _has_env(*names: str) -> bool:
    return any(os.environ.get(n) for n in names)


class ProviderScaffold:
    """Base class: name, env key, rights, and a fail-closed status."""

    name = "scaffold"
    env_keys: tuple[str, ...] = ()
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    license = None
    quota_note = None

    def __init__(self, transport: Any = None) -> None:
        self.transport = transport

    @property
    def is_configured(self) -> bool:
        return _has_env(*self.env_keys)

    def status(self) -> str:
        return OPERATIONAL if self.is_configured else NOT_CONFIGURED

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "operational_status": self.status(),
            "rights_status": self.rights_status,
            "commercial_use_status": self.commercial_use_status,
            "license": self.license,
            "quota_note": self.quota_note,
        }

    def run_bounded(self, conn, **kwargs: Any) -> dict[str, Any]:
        """Bounded acquisition entry point. NOT_CONFIGURED without a key/transport."""
        if not self.is_configured or self.transport is None:
            return {"provider": self.name, "status": NOT_CONFIGURED, "records": 0}
        return self._acquire(conn, **kwargs)

    def _acquire(self, conn, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


class ListenBrainzProvider(ProviderScaffold):
    name = "listenbrainz"
    env_keys: tuple[str, ...] = ()
    rights_status = "OPEN_COMMERCIAL_OK"  # CC0
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    license = "CC0"
    quota_note = "public dumps + API; attention/consumption sample, never demand"

    def _acquire(self, conn, **kwargs: Any) -> dict[str, Any]:
        # Keyed/full implementation lands when a transport is wired; this
        # milestone keeps the scaffold honest rather than half-ingesting.
        return {"provider": self.name, "status": PARTIAL, "records": 0}


class GdeltProvider(ProviderScaffold):
    name = "gdelt"
    env_keys: tuple[str, ...] = ()
    rights_status = "OPEN_COMMERCIAL_OK"
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    license = None
    quota_note = "recent-only DOC window; metadata only (no full article text)"


class NwsProvider(ProviderScaffold):
    name = "nws"
    env_keys: tuple[str, ...] = ()
    rights_status = "OPEN_COMMERCIAL_OK"
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    license = None
    quota_note = "api.weather.gov; forecasts snapshot at observation time"


class CensusProvider(ProviderScaffold):
    name = "census"
    env_keys: tuple[str, ...] = ("CENSUS_API_KEY",)
    rights_status = "OPEN_COMMERCIAL_OK"
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    license = None
    quota_note = "ACS vintage + estimate + margin of error retained"


class JamBaseProvider(ProviderScaffold):
    name = "jambase"
    env_keys: tuple[str, ...] = ("JAMBASE_API_KEY",)
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    license = None
    quota_note = "OPTIONAL; strictly bounded benchmark (trial); not ground truth"


class TicketmasterProvider(ProviderScaffold):
    name = "ticketmaster-discovery"
    env_keys: tuple[str, ...] = ("TICKETMASTER_API_KEY",)
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    license = None
    quota_note = "5,000/day; treat 2 req/s as safe default; DMA-partitioned US music"


ALL_PROVIDERS: list[type[ProviderScaffold]] = [
    ListenBrainzProvider,
    GdeltProvider,
    NwsProvider,
    CensusProvider,
    JamBaseProvider,
    TicketmasterProvider,
]


def provider_statuses(transport: Any = None) -> list[dict[str, Any]]:
    """Operational status for every new provider (fail-closed)."""
    return [cls(transport=transport).describe() for cls in ALL_PROVIDERS]
