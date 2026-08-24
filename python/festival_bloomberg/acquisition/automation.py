"""Canonical machine-enforced provider automation dispositions.

Automation status is intentionally separate from source rights, research use,
and commercial use.  A provider implementation may remain installed while
unattended acquisition is disabled.
"""

from __future__ import annotations

from enum import Enum


class AutomationStatus(str, Enum):
    ENABLED = "AUTOMATION_ENABLED"
    DISABLED = "AUTOMATION_DISABLED"


_DISPOSITIONS: dict[str, AutomationStatus] = {
    "seatgeek": AutomationStatus.DISABLED,
}


def automation_status(provider: str) -> AutomationStatus:
    """Return the canonical automation disposition for a known provider."""
    return _DISPOSITIONS.get(provider.strip().lower(), AutomationStatus.ENABLED)


def automation_allowed(provider: str) -> bool:
    return automation_status(provider) == AutomationStatus.ENABLED
