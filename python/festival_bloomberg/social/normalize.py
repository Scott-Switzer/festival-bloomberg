"""Normalizers that map provider payloads to the canonical record shape.

The canonical record shape is intentionally sparse; missing fields are
``None`` (never zero-filled). Every record may carry an ``engagement`` dict
whose values are stored as timestamped snapshots at retrieval time.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


def _text_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_monid_record(item: dict) -> dict:
    """Map a generic Monid provider record to the canonical shape.

    Monid endpoints return provider-specific schemas; we extract the common
    social fields and preserve the rest in ``metadata``.
    """
    text = (
        _first(item, "text", "content", "caption", "body", "description")
        or ""
    )
    author = _first(item, "author_id", "author", "user_id", "user")
    if isinstance(author, dict):
        author = _first(author, "id", "username", "name")
    platform_object_id = _first(
        item, "id", "object_id", "post_id", "video_id", "tweet_id"
    )
    return {
        "platform": _first(item, "platform", "source") or "unknown",
        "object_type": _first(item, "object_type", "type") or "post",
        "platform_object_id": str(platform_object_id) if platform_object_id is not None else None,
        "parent_object_id": item.get("parent_id") or item.get("reply_to"),
        "author_public_id": str(author) if author is not None else None,
        "author_name": _first(item, "author_name", "username", "user_name"),
        "text": str(text),
        "language": _first(item, "language", "lang"),
        "published_at": _first(item, "published_at", "created_at", "timestamp"),
        "media_type": _first(item, "media_type", "media"),
        "canonical_url": _first(item, "url", "permalink", "link"),
        "hashtags": item.get("hashtags") or item.get("tags"),
        "mentions": item.get("mentions"),
        "market_id": item.get("market_id"),
        "geographic_confidence": item.get("geographic_confidence"),
        "content_hash": _text_hash(str(text)) if text else None,
        "engagement": {
            "likes": item.get("likes") or item.get("like_count"),
            "comments": item.get("comments") or item.get("comment_count"),
            "shares": item.get("shares") or item.get("share_count") or item.get("retweet_count"),
            "reposts": item.get("reposts") or item.get("repost_count"),
            "views": item.get("views") or item.get("view_count") or item.get("play_count"),
            "follower_count": item.get("follower_count") or item.get("followers"),
            "verified_author": item.get("verified"),
        },
    }


def normalize_actor_record(item: dict) -> dict:
    """Map an Apify Actor record to the canonical shape (best effort)."""
    return normalize_monid_record(item)


def normalize_youtube_item(item: dict) -> dict:
    """Alias for YouTube records already normalized by the provider."""
    return item


def normalize_web_page(record: dict) -> dict:
    """Validate/normalize a raw web-page record from http/scrapling providers."""
    text = record.get("text")
    return {
        "platform": record.get("platform") or "web",
        "object_type": record.get("object_type") or "web_page",
        "platform_object_id": None,
        "text": text,
        "canonical_url": record.get("source_url"),
        "raw_bytes": record.get("raw_bytes"),
        "content_hash": _text_hash(text) if text else None,
    }


def is_relevant_text(text: str | None) -> bool:
    """Cheap relevance guard: URLs-only or empty text is not evidence."""
    if not text or not text.strip():
        return False
    if re.fullmatch(r"\s*(https?://\S+)\s*", text):
        return False
    return True
