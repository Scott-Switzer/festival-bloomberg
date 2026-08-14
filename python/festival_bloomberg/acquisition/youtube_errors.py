"""Classify YouTube Data API errors without exposing secrets.

``CONFIGURED`` (key present in env) is not authentication. Live validation
must report one of the AUTH_* statuses below.
"""

from __future__ import annotations

from typing import Any

AUTH_VALID = "VALID"
AUTH_INVALID = "INVALID"
AUTH_API_DISABLED = "API_DISABLED"
AUTH_RESTRICTION_BLOCKED = "RESTRICTION_BLOCKED"
AUTH_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
AUTH_UNKNOWN = "UNKNOWN"
AUTH_NOT_CONFIGURED = "NOT_CONFIGURED"

CAT_API_KEY_INVALID = "API_KEY_INVALID"
CAT_API_DISABLED = "YOUTUBE_DATA_API_DISABLED"
CAT_API_RESTRICTION = "API_RESTRICTION_MISMATCH"
CAT_APP_RESTRICTION = "APPLICATION_RESTRICTION_MISMATCH"
CAT_PROJECT_RESTRICTION = "PROJECT_RESTRICTION"
CAT_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
CAT_REQUEST_INVALID = "REQUEST_INVALID"
CAT_COMMENTS_DISABLED = "COMMENTS_DISABLED"
CAT_NOT_FOUND = "NOT_FOUND"
CAT_RATE_LIMITED = "RATE_LIMITED"
CAT_UNKNOWN = "UNKNOWN"


def _reasons(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    error = payload.get("error") or {}
    reasons: list[str] = []
    status = error.get("status")
    if isinstance(status, str):
        reasons.append(status)
    for item in error.get("errors") or []:
        if isinstance(item, dict) and item.get("reason"):
            reasons.append(str(item["reason"]))
    message = error.get("message")
    if isinstance(message, str):
        reasons.append(message)
    return reasons


def _joined_reasons(payload: Any) -> str:
    return " ".join(_reasons(payload)).lower()


def classify_youtube_error(status: int, payload: Any) -> tuple[str, str]:
    """Return ``(auth_or_call_status, error_category)`` with no secrets.

    The first element is an AUTH_* token when the failure is credential /
    project / quota related, otherwise a call-level token such as
    ``COMMENTS_DISABLED``.
    """
    blob = _joined_reasons(payload)
    if status == 401 or "keyinvalid" in blob.replace("_", "").replace(" ", "") or "api_key_invalid" in blob:
        return AUTH_INVALID, CAT_API_KEY_INVALID
    if "keyinvalid" in blob or "invalid api key" in blob or "api key not valid" in blob:
        return AUTH_INVALID, CAT_API_KEY_INVALID
    if "accessnotconfigured" in blob or "has not been used" in blob or "youtube data api" in blob and "disabled" in blob:
        return AUTH_API_DISABLED, CAT_API_DISABLED
    if any(
        token in blob
        for token in (
            "iprefererblocked",
            "refererblocked",
            "ipblocked",
            "androidappblocked",
            "iosappblocked",
            "apirestriction",
        )
    ):
        return AUTH_RESTRICTION_BLOCKED, CAT_API_RESTRICTION
    if "applicationrestriction" in blob:
        return AUTH_RESTRICTION_BLOCKED, CAT_APP_RESTRICTION
    if "project" in blob and "not authorized" in blob:
        return AUTH_RESTRICTION_BLOCKED, CAT_PROJECT_RESTRICTION
    if any(
        token in blob
        for token in ("quotaexceeded", "dailylimitexceeded", "quota exceeded", "rateLimitExceeded".lower())
    ) or status == 429:
        return AUTH_QUOTA_EXCEEDED, CAT_QUOTA_EXCEEDED
    if "commentsdisabled" in blob:
        return "COMMENTS_DISABLED", CAT_COMMENTS_DISABLED
    if status == 404 or "videonotfound" in blob or "notfound" in blob:
        return "NOT_FOUND", CAT_NOT_FOUND
    if status == 400:
        return AUTH_UNKNOWN, CAT_REQUEST_INVALID
    if status == 403:
        # Bare 403 without a reason is not assumed to be quota.
        return AUTH_UNKNOWN, CAT_UNKNOWN
    return AUTH_UNKNOWN, CAT_UNKNOWN


def auth_from_http(status: int, payload: Any) -> str:
    """Map a videos.list validation response to an AUTH_* token."""
    if status == 200:
        return AUTH_VALID
    auth, _category = classify_youtube_error(status, payload)
    if auth in {
        AUTH_INVALID,
        AUTH_API_DISABLED,
        AUTH_RESTRICTION_BLOCKED,
        AUTH_QUOTA_EXCEEDED,
        AUTH_UNKNOWN,
    }:
        return auth
    return AUTH_UNKNOWN
