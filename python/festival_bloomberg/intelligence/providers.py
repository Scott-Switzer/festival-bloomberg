"""Provider registry for the intelligence terminal.

This is a UNIFIED registry: it reconciles the terminal's provider scaffolds
with the canonical acquisition layer (``festival_bloomberg.acquisition``).
Both read the SAME local ``.env`` through ``localenv.load_local_env``, and both
report the same rights/commercial status from ``flywheel.source_registry``.

Provider state taxonomy (the bug fix from the data-estate milestone):

- ``PUBLIC_NO_AUTH``   — key-free public provider that is operational.
- ``AUTH_CONFIGURED``  — required key present (value never shown).
- ``AUTH_MISSING``     — required key absent.
- ``OPERATIONAL``      — implemented AND (public OR auth configured).
- ``NOT_IMPLEMENTED``  — scaffold only; no real acquisition wired.
- ``DISABLED_RIGHTS``  — terms/rights block automated ingestion.
- ``BLOCKED`` / ``RATE_LIMITED`` / ``DEGRADED`` — runtime states.

A no-key provider (e.g. ListenBrainz, GDELT, NWS) can NEVER be classified
``NOT_CONFIGURED``/``AUTH_MISSING``: that status is reserved for providers
whose required credential is absent. This was previously broken by an
``env_keys = ()`` provider whose ``any(())`` test always returned False.

Provider semantics are honored literally: ListenBrainz is an ATTENTION/
CONSUMPTION SAMPLE (CC0), never local demand; GDELT stores metadata only;
NWS forecasts are snapshotted at forecast-generation time; SeatGeek stays
DISABLED for automated ingestion (terms review).
"""

from __future__ import annotations

from typing import Any

from ..acquisition.automation import AutomationStatus, automation_status
from ..localenv import load_local_env

# -- status constants ---------------------------------------------------------
PUBLIC_NO_AUTH = "PUBLIC_NO_AUTH"
AUTH_CONFIGURED = "AUTH_CONFIGURED"
AUTH_MISSING = "AUTH_MISSING"
OPERATIONAL = "OPERATIONAL"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
DISABLED_RIGHTS = "DISABLED_RIGHTS"
BLOCKED = "BLOCKED"
RATE_LIMITED = "RATE_LIMITED"
DEGRADED = "DEGRADED"
NOT_CONFIGURED = "NOT_CONFIGURED"  # legacy alias for AUTH_MISSING


class ProviderScaffold:
    """Base scaffold: name, auth requirement, rights, and fail-closed status."""

    name = "scaffold"
    env_keys: tuple[str, ...] = ()
    auth_required: bool = True       # False for key-free public providers
    implemented: bool = False        # True once a real _acquire exists
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    license = None
    quota_note = None

    def __init__(self, transport: Any = None) -> None:
        self.transport = transport

    @property
    def is_configured(self) -> bool:
        # Public providers never need a credential.
        if not self.auth_required:
            return True
        load_local_env()
        return any(_nonempty(k) for k in self.env_keys)

    def auth_status(self) -> str:
        if not self.auth_required:
            return PUBLIC_NO_AUTH
        return AUTH_CONFIGURED if self.is_configured else AUTH_MISSING

    def status(self) -> str:
        if automation_status(self.name) == AutomationStatus.DISABLED:
            return DISABLED_RIGHTS
        if self.rights_status in ("DISABLED", "RIGHTS_BLOCKED", "TERMS_BLOCKED"):
            return DISABLED_RIGHTS
        if not self.is_configured:
            return AUTH_MISSING
        return OPERATIONAL if self.implemented else NOT_IMPLEMENTED

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "auth_status": self.auth_status(),
            "operational_status": self.status(),
            "rights_status": self.rights_status,
            "commercial_use_status": self.commercial_use_status,
            "license": self.license,
            "quota_note": self.quota_note,
        }

    def run_bounded(self, conn, **kwargs: Any) -> dict[str, Any]:
        if not self.is_configured or self.transport is None:
            return {"provider": self.name, "status": self.status(), "records": 0}
        return self._acquire(conn, **kwargs)

    def _acquire(self, conn, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


def _nonempty(name: str) -> bool:
    import os
    value = os.environ.get(name)
    return bool(value and value.strip())


class ListenBrainzProvider(ProviderScaffold):
    name = "listenbrainz"
    auth_required = False
    implemented = True  # canonical acquisition.providers.listenbrainz.ListenBrainzProvider
    rights_status = "OPEN_COMMERCIAL_OK"  # CC0
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    license = "CC0"
    quota_note = ("artist listeners stats keyed by MBID; LISTENBRAINZ_LISTEN_COUNT / "
                  "LISTENBRAINZ_LISTENER_COUNT; attention/consumption sample, never demand")


class GdeltProvider(ProviderScaffold):
    name = "gdelt"
    auth_required = False
    implemented = True  # canonical acquisition.providers.gdelt.GdeltProvider
    rights_status = "OPEN_COMMERCIAL_OK"
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    license = None
    quota_note = ("DOC artlist (metadata only); provider asks >=1 req/5s; "
                  "recent-only window; no full article text")


class NwsProvider(ProviderScaffold):
    name = "nws"
    auth_required = False
    implemented = True  # canonical acquisition.providers.nws.NwsProvider
    rights_status = "OPEN_COMMERCIAL_OK"
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    license = None
    quota_note = "api.weather.gov; forecasts snapshot at generation time"


class CensusProvider(ProviderScaffold):
    name = "census"
    env_keys = ("CENSUS_API_KEY",)
    implemented = False
    rights_status = "OPEN_COMMERCIAL_OK"
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    quota_note = "ACS vintage + estimate + margin of error retained"


class JamBaseProvider(ProviderScaffold):
    name = "jambase"
    env_keys = ("JAMBASE_API_KEY",)
    implemented = False
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    quota_note = "OPTIONAL; strictly bounded benchmark (trial); not ground truth"


class TicketmasterProvider(ProviderScaffold):
    name = "ticketmaster-discovery"
    env_keys = ("TICKETMASTER_API_KEY", "TICKETMASTER_CONSUMER_KEY")
    implemented = False
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    quota_note = "5,000/day; treat 2 req/s as safe default; DMA-partitioned US music"


class SpotifyProvider(ProviderScaffold):
    name = "spotify"
    env_keys = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_API_KEY")
    implemented = True  # canonical acquisition.providers.spotify.SpotifyProvider
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "RESEARCH_ONLY"
    quota_note = ("Dev Mode 2026: no popularity/followers/genres/top-tracks; shared "
                  "dev-account quota. Classic client-credentials flow validated "
                  "2026-08-15 (AUTH_VALID). SPOTIFY_API_KEY (spak_) is the Soloist "
                  "API surface, not the standard Web API.")


class YouTubeProvider(ProviderScaffold):
    name = "youtube"
    env_keys = ("YOUTUBE_API_KEY",)
    implemented = False
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    quota_note = "video stats + comment collection; batchGetStats quota bucket"


class SetlistFmProvider(ProviderScaffold):
    name = "setlistfm"
    env_keys = ("SETLISTFM_API_KEY",)
    implemented = False
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "RESEARCH_ONLY"
    quota_note = "noncommercial unless separately arranged"


class ApifyProvider(ProviderScaffold):
    name = "apify"
    env_keys = ("APIFY_TOKEN",)
    implemented = False
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    quota_note = "last-resort high-value fallback; spend credits only on P0 gaps"


class MonidProvider(ProviderScaffold):
    name = "monid"
    env_keys = ("MONID_API_KEY",)
    implemented = False
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    quota_note = "configurable approved endpoint"


class MusicBrainzProvider(ProviderScaffold):
    name = "musicbrainz"
    env_keys = ("MUSICBRAINZ_USER_AGENT", "MUSICBRAINZ_API_KEY")
    implemented = False
    rights_status = "OPEN_COMMERCIAL_OK"  # core data CC0
    commercial_use_status = "RESEARCH_ONLY"  # public web service is noncommercial
    quota_note = "~1 req/sec; prefer local core dump for identity spine"


class WikimediaProvider(ProviderScaffold):
    name = "wikimedia"
    auth_required = False
    implemented = True  # canonical acquisition.providers.wikimedia.WikimediaProvider
    rights_status = "OPEN_COMMERCIAL_OK"
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    quota_note = "pageviews/edits attention series"


class CommonCrawlProvider(ProviderScaffold):
    name = "commoncrawl"
    auth_required = False
    implemented = True  # canonical acquisition.providers.commoncrawl.CommonCrawlProvider
    rights_status = "OPEN_COMMERCIAL_OK"
    commercial_use_status = "OPEN_COMMERCIAL_OK"
    quota_note = "URL Index (Parquet) for bulk discovery; CDX only for known URLs"


class NvidiaProvider(ProviderScaffold):
    name = "nvidia"
    env_keys = ("NVIDIA_API_KEY",)
    implemented = False
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "RESEARCH_ONLY"  # dev program is prototyping/research
    quota_note = "OpenAI-compatible NIM; production licensing separate"


class DeepSeekProvider(ProviderScaffold):
    name = "deepseek"
    env_keys = ("DEEPSEEK_API_KEY",)
    implemented = False
    rights_status = "TERMS_REVIEW_REQUIRED"
    commercial_use_status = "RESEARCH_ONLY"
    quota_note = "public/sanitized material only; no private settlement data"


class SoundchartsProvider(ProviderScaffold):
    name = "soundcharts"
    env_keys = ("SOUNDCHARTS_APP_ID", "SOUNDCHARTS_API_KEY")
    implemented = True  # canonical acquisition.providers.soundcharts.SoundchartsProvider
    rights_status = "LICENSED_PROVIDER_PENDING_ACCOUNT_REVIEW"
    commercial_use_status = "LICENSE_REQUIRED"
    quota_note = "Licensed historical audience/streaming rail; real backfill requires account authorization"


class ChartmetricProvider(ProviderScaffold):
    name = "chartmetric"
    env_keys = ("CHARTMETRIC_API_KEY",)
    implemented = False
    rights_status = "LICENSED_PROVIDER_PENDING_ACCOUNT_REVIEW"
    commercial_use_status = "LICENSE_REQUIRED"
    quota_note = "Alternate licensed historical audience/streaming source; contract pending"


class GoogleTrendsProvider(ProviderScaffold):
    name = "google_trends"
    env_keys = ("GOOGLE_TRENDS_API_KEY", "GOOGLE_TRENDS_ACCESS_TOKEN")
    implemented = True  # official alpha adapter; access remains waitlisted when absent
    rights_status = "OFFICIAL_API_PENDING_ACCESS_REVIEW"
    commercial_use_status = "LICENSE_REQUIRED"
    quota_note = "WAITLIST / AUTH_REQUIRED; official alpha only, never UI scraping"


class SeatGeekProvider(ProviderScaffold):
    name = "seatgeek"
    env_keys = ("SEATGEEK_CLIENT_ID",)
    implemented = False
    rights_status = "DISABLED"  # terms prohibit AI/ML ingestion without authorization
    commercial_use_status = "TERMS_REVIEW_REQUIRED"
    quota_note = "DISABLED for automated corpus/LLM ingestion (terms)"


ALL_PROVIDERS: list[type[ProviderScaffold]] = [
    ListenBrainzProvider,
    GdeltProvider,
    NwsProvider,
    CensusProvider,
    JamBaseProvider,
    TicketmasterProvider,
    SpotifyProvider,
    YouTubeProvider,
    SetlistFmProvider,
    ApifyProvider,
    MonidProvider,
    MusicBrainzProvider,
    WikimediaProvider,
    CommonCrawlProvider,
    SoundchartsProvider,
    ChartmetricProvider,
    GoogleTrendsProvider,
    NvidiaProvider,
    DeepSeekProvider,
    SeatGeekProvider,
]


def provider_statuses(transport: Any = None) -> list[dict[str, Any]]:
    """Unified, fail-closed operational status for every registered provider."""
    load_local_env()
    out: list[dict[str, Any]] = []
    for cls in ALL_PROVIDERS:
        inst = cls(transport=transport)
        desc = inst.describe()
        # Surface credential presence WITHOUT the value (names only).
        desc["credentials"] = {
            "configured": inst.is_configured,
            "keys": list(inst.env_keys),
        }
        out.append(desc)
    return out
