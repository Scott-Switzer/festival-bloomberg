"""Evidence semantics: content role and resolution method taxonomies.

These taxonomies keep finance-grade provenance honest:

* ``content_role`` separates fan-generated discourse from encyclopedic,
  editorial and promotional text. Only ``FAN_GENERATED`` (and, by explicit
  policy, ``FORUM_DISCUSSION``) may feed fan sentiment / intent signals.
* ``resolution_method`` records *how* a canonical object was identified.
  Exact identities (explicit platform id / canonical URL / external id) carry
  no probabilistic confidence; fuzzy and manual resolutions carry evidence.

Both enums fail closed: ``UNKNOWN`` is never upgraded into a real signal.
"""

from __future__ import annotations

from enum import Enum


class ContentRole(str, Enum):
    FAN_GENERATED = "FAN_GENERATED"
    FORUM_DISCUSSION = "FORUM_DISCUSSION"
    REVIEW = "REVIEW"
    EDITORIAL = "EDITORIAL"
    ENCYCLOPEDIC = "ENCYCLOPEDIC"
    OFFICIAL_PROMOTIONAL = "OFFICIAL_PROMOTIONAL"
    EVENT_LISTING = "EVENT_LISTING"
    TICKET_LISTING = "TICKET_LISTING"
    TRANSCRIPT = "TRANSCRIPT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ResolutionMethod(str, Enum):
    EXACT_PLATFORM_ID = "EXACT_PLATFORM_ID"
    EXACT_CANONICAL_URL = "EXACT_CANONICAL_URL"
    EXACT_EXTERNAL_ID = "EXACT_EXTERNAL_ID"
    ALIAS_MATCH = "ALIAS_MATCH"
    FUZZY_MATCH = "FUZZY_MATCH"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


#: Roles eligible to enter fan sentiment / intent aggregation. Everything else
#: is explicitly excluded (fail closed).
FAN_SENTIMENT_ROLES = frozenset({ContentRole.FAN_GENERATED, ContentRole.FORUM_DISCUSSION})


def is_fan_role(role: str | None) -> bool:
    """True only for roles eligible for fan sentiment / intent signals."""
    if role is None:
        return False
    try:
        return ContentRole(role) in FAN_SENTIMENT_ROLES
    except ValueError:
        return False


def normalize_content_role(role: str | None) -> str | None:
    """Validate a role string; unknown values fail closed to UNKNOWN."""
    if role is None:
        return None
    try:
        return ContentRole(role).value
    except ValueError:
        return ContentRole.UNKNOWN.value


def normalize_resolution_method(method: str | None) -> str | None:
    if method is None:
        return None
    try:
        return ResolutionMethod(method).value
    except ValueError:
        return ResolutionMethod.UNKNOWN.value
