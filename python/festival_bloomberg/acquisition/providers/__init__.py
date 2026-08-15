"""Provider registry for the Festival Signal Fabric.

Only providers that are actually exercised and tested are registered here.
Crawlee, Scrapy, Playwright, Crawl4AI and yt-dlp are documented in
``docs/signal-fabric/provider-landscape.md`` and can be plugged in behind
the same :class:`AcquisitionProvider` protocol when benchmarked — no fake
"implemented" adapters are shipped for them.
"""

from __future__ import annotations

from ..base import AcquisitionProvider
from .apify import ApifyProvider
from .commoncrawl import CommonCrawlProvider
from .eventbrite import EventbriteProvider
from .http import HttpProvider
from .monid import MonidProvider
from .nws import NwsProvider
from .openstreetmap import OpenStreetMapProvider
from .scrapling import ScraplingProvider
from .seatgeek import SeatGeekProvider
from .setlistfm import SetlistFmProvider
from .spotify import SpotifyProvider
from .ticketmaster import TicketmasterProvider
from .wikidata import WikidataProvider
from .wikimedia import WikimediaProvider
from .youtube import YouTubeProvider

__all__ = [
    "AcquisitionProvider",
    "ApifyProvider",
    "CommonCrawlProvider",
    "EventbriteProvider",
    "HttpProvider",
    "MonidProvider",
    "NwsProvider",
    "OpenStreetMapProvider",
    "ScraplingProvider",
    "SeatGeekProvider",
    "SetlistFmProvider",
    "SpotifyProvider",
    "TicketmasterProvider",
    "WikidataProvider",
    "WikimediaProvider",
    "YouTubeProvider",
    "default_providers",
]


def default_providers(**overrides) -> dict[str, AcquisitionProvider]:
    """Build the canonical provider set (all offline-safe, none paid by default)."""
    providers: dict[str, AcquisitionProvider] = {
        "http": HttpProvider(**overrides.pop("http", {})),
        "commoncrawl": CommonCrawlProvider(**overrides.pop("commoncrawl", {})),
        "eventbrite": EventbriteProvider(**overrides.pop("eventbrite", {})),
        "monid": MonidProvider(**overrides.pop("monid", {})),
        "nws": NwsProvider(**overrides.pop("nws", {})),
        "apify": ApifyProvider(**overrides.pop("apify", {})),
        "youtube": YouTubeProvider(**overrides.pop("youtube", {})),
        "scrapling": ScraplingProvider(**overrides.pop("scrapling", {})),
        "wikimedia": WikimediaProvider(**overrides.pop("wikimedia", {})),
        "wikidata": WikidataProvider(**overrides.pop("wikidata", {})),
        "openstreetmap": OpenStreetMapProvider(**overrides.pop("openstreetmap", {})),
        "ticketmaster": TicketmasterProvider(**overrides.pop("ticketmaster", {})),
        "spotify": SpotifyProvider(**overrides.pop("spotify", {})),
        "setlistfm": SetlistFmProvider(**overrides.pop("setlistfm", {})),
        "seatgeek": SeatGeekProvider(**overrides.pop("seatgeek", {})),
    }
    return providers
