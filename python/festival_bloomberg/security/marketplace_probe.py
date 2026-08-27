"""MARKET_LIQUIDITY_TAPE_V1 — P11: probe other marketplaces (only after the
official structured rails are exhausted).

Vivid Seats, TickPick, Gametime, AXS are probed LAST, after Ticketmaster /
SeatGeek / StubHub official structured interfaces are either exhausted or
authoritatively unavailable. The milestone forbids purchasing a new provider and
prefers structured evidence. Each marketplace is recorded in the source-auth
scorecard as FOR_DEFERRAL unless an official API credential is configured; no
budget is spent on browser/Monid scraping blindly.

No novel paid provider. This is a passive, cheap, structured status probe.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..acquisition.automation import AutomationStatus, automation_status

DEFERRED_MARKETPLACES = (
    ("vividseats", "vividseats.com"),
    ("tickpick", "tickpick.com"),
    ("gametime", "gametime.co"),
    ("axs", "axs.com"),
)


def probe_other_marketplaces(conn, env_keys: dict[str, Any]) -> dict[str, Any]:
    """Record deferral/authorization state for the remaining marketplaces.

    Does NOT start scraping: the official structured rails are exhausted first.
    Any provider with a configured API credential would be AUTHORIZED_REQUIRES_TEST;
    otherwise FOR_DEFERRAL (Monid fallback permitted later, bounded).
    """
    out: dict[str, Any] = {"status": "COMPLETE", "providers": {}}
    for key, domain in DEFERRED_MARKETPLACES:
        styled = key.upper()
        has_key = bool(
            env_keys.get(f"{styled}_API_KEY") or env_keys.get(f"{styled}_CLIENT_ID")
        )
        automation = automation_status(key)
        auto_blocked = automation == AutomationStatus.DISABLED
        auth_state = (
            "AUTHORIZED_REQUIRES_TEST" if has_key and not auto_blocked
            else ("AUTOMATION_DISABLED" if auto_blocked else "NOT_AUTHORIZED")
        )
        credential_state = "CONFIGURED" if has_key else ("DISABLED_BY_POLICY" if auto_blocked else "ABSENT")
        out["providers"][key] = {
            "marketplace": key,
            "domain": domain,
            "credential_state": credential_state,
            "auth_state": auth_state,
            "phase": "P11_DEFERRAL",
            "note": (
                "structured-official rail not configured; Direct HTTP / embedded-JSON / "
                "Cloudflare-Browser / Playwright / Monid fallback are evaluated only after "
                "TM/SG/SH official rails are exhausted; no paid provider purchased"
            ),
        }
        _record_auth(conn, provider=key, provider_kind="platform_api",
                     credential_state=credential_state, auth_state=auth_state, detail=key)
    return out


def _record_auth(conn, *, provider, provider_kind, credential_state, auth_state, detail) -> None:
    conn.execute(
        """
        INSERT INTO acquisition.source_auth_status
            (status_id, provider, provider_kind, credential_state, auth_state,
             api_calls, browser_calls, monid_calls, cost_usd, useful_observations,
             detail, checked_at, rights_status, commercial_use_status, ingested_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0.0, 0, ?, CURRENT_TIMESTAMP,
                'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', CURRENT_TIMESTAMP)
        ON CONFLICT (provider, provider_kind) DO UPDATE
          SET credential_state = excluded.credential_state,
              auth_state = excluded.auth_state, detail = excluded.detail,
              checked_at = excluded.checked_at
        """,
        [hashlib.sha256(f"{provider}|{provider_kind}".encode()).hexdigest()[:32],
         provider, provider_kind, credential_state, auth_state, detail],
    )