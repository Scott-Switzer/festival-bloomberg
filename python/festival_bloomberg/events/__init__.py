"""Artist × market × event history package."""

from .fan_link import event_linked_fan_status, link_video_to_events
from .features import build_artist_market_vector
from .identity import IdentityResolution, canonical_artist_id, merge_identity
from .reconcile import reconcile_events
from .repository import EventRepository

__all__ = [
    "EventRepository",
    "IdentityResolution",
    "build_artist_market_vector",
    "canonical_artist_id",
    "event_linked_fan_status",
    "link_video_to_events",
    "merge_identity",
    "reconcile_events",
]
