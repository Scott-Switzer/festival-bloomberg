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
from .http import HttpProvider
from .monid import MonidProvider
from .scrapling import ScraplingProvider
from .youtube import YouTubeProvider

__all__ = [
    "AcquisitionProvider",
    "ApifyProvider",
    "HttpProvider",
    "MonidProvider",
    "ScraplingProvider",
    "YouTubeProvider",
    "default_providers",
]


def default_providers(**overrides) -> dict[str, AcquisitionProvider]:
    """Build the canonical provider set (all offline-safe, none paid by default)."""
    providers: dict[str, AcquisitionProvider] = {
        "http": HttpProvider(**overrides.pop("http", {})),
        "monid": MonidProvider(**overrides.pop("monid", {})),
        "apify": ApifyProvider(**overrides.pop("apify", {})),
        "youtube": YouTubeProvider(**overrides.pop("youtube", {})),
        "scrapling": ScraplingProvider(**overrides.pop("scrapling", {})),
    }
    return providers
