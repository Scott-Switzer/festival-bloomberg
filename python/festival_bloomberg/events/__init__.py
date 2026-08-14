"""Artist × market × event history package."""

from .features import build_artist_market_vector
from .identity import IdentityResolution, canonical_artist_id, merge_identity
from .repository import EventRepository

__all__ = [
    "EventRepository",
    "IdentityResolution",
    "build_artist_market_vector",
    "canonical_artist_id",
    "merge_identity",
]
