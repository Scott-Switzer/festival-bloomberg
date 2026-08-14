"""Canonicalization and provider reconciliation.

The same platform object may be observed through multiple providers. We
create ONE canonical object and preserve EVERY provider observation:

1. platform object ID (strongest)
2. canonical URL
3. normalized content hash (secondary evidence)

Mutable metrics (views, likes, ...) are never collapsed to "the latest
provider's value": each provider observation keeps its own retrieved_at
and is stored as a separate timestamped engagement snapshot.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    cleaned = url.strip().rstrip("/")
    # strip common tracking fragments
    cleaned = re.sub(r"[?#].*$", "", cleaned)
    return cleaned


def canonical_key(
    platform: str,
    platform_object_id: str | None,
    source_url: str | None,
    content_hash: str | None,
) -> str | None:
    """Return the strongest stable identity key for an object, or None."""
    if platform_object_id:
        return f"{platform}::{platform_object_id}"
    url = normalize_url(source_url)
    if url:
        return f"url::{url}"
    if content_hash:
        return f"hash::{content_hash}"
    return None


def canonical_observation_id(platform: str, key: str) -> str:
    digest = hashlib.sha1(f"{platform}::{key}".encode("utf-8")).hexdigest()
    return f"canon_{digest[:24]}"


@dataclass(frozen=True)
class CanonicalResolution:
    canonical_id: str
    key: str
    is_new: bool


def resolve_canonical(
    platform: str,
    platform_object_id: str | None,
    source_url: str | None,
    content_hash: str | None,
    known_ids: set[str],
) -> CanonicalResolution | None:
    """Resolve a provider record to a canonical observation id.

    ``known_ids`` is the set of canonical ids already present for the
    platform (queried by the caller); returns ``None`` when no stable
    identity can be derived (record cannot be deduplicated).
    """
    key = canonical_key(platform, platform_object_id, source_url, content_hash)
    if key is None:
        return None
    canonical_id = canonical_observation_id(platform, key)
    return CanonicalResolution(
        canonical_id=canonical_id,
        key=key,
        is_new=canonical_id not in known_ids,
    )
