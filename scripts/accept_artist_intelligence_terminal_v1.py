#!/usr/bin/env python3
"""Acceptance checks for the Artist Intelligence Terminal V1 product layer.

Runs against a live MVP terminal (``mvp_server``) and asserts the Artist
Security V2 contract: factor tape, what-changed deltas, daily sentiment
boundaries, provider data-rail readiness, plus the required product endpoints
(search/artist/market/compare/underwrite/portfolio/pace/monitor). Exits 0 only
when every assertion passes; writes a machine-readable evidence file when
``--evidence`` is supplied.

Example:
    python scripts/accept_artist_intelligence_terminal_v1.py \
        --base http://127.0.0.1:8975 \
        --artist mbid::ee58c59f-8e7f-4430-b8ca-236c4d3745ae
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

REQUIRED_ENDPOINTS = ("search", "market", "portfolio", "pace", "monitor", "status")
RAW_LEAK_KEYS = ("author", "user_id", "author_public_id", "text", "post_id")


def _get(base: str, path: str) -> dict | list:
    with urllib.request.urlopen(f"{base}{path}", timeout=30) as resp:
        return json.loads(resp.read().decode())


def _post(base: str, path: str, body: dict) -> dict | list:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8975")
    parser.add_argument("--artist", default="mbid::ee58c59f-8e7f-4430-b8ca-236c4d3745ae")
    parser.add_argument("--market", default="chicago-il")
    parser.add_argument("--evidence", type=Path, default=None)
    args = parser.parse_args()

    results: dict[str, dict] = {}
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results[name] = {"pass": bool(ok), "detail": detail[:400]}
        if not ok:
            failures.append(name)

    # ── required read endpoints ────────────────────────────────────────
    search = _get(args.base, "/api/search?q=alice%20cooper&limit=5")
    check(
        "search",
        isinstance(search, list) and any(
            str(item.get("name", "")).lower().startswith("alice") for item in search
        ),
        f"{len(search)} hits",
    )
    for ep in ("portfolio", "pace", "monitor", "status"):
        payload = _get(args.base, f"/api/{ep}")
        check(ep, payload is not None, "200")
    check("market", isinstance(_get(args.base, f"/api/market/{args.market}"), dict), "200")

    # ── artist security V2 contract ────────────────────────────────────
    artist_path = "/api/artist-security/" + urllib.parse.quote(args.artist, safe="")
    page = _get(args.base, artist_path)
    artist = page.get("artist", {})
    check("artist_identity", bool(artist.get("name")), artist.get("name") or "none")

    tape = page.get("factor_tape", {})
    check(
        "factor_tape_contract",
        tape.get("status") == "OBSERVED"
        and tape.get("items")
        and tape.get("note"),
        f"status={tape.get('status')} items={len(tape.get('items', []))}",
    )
    first = (tape.get("items") or [{}])[0]
    required_fields = (
        "factor_name", "platform", "value", "unit", "observation_time",
        "retrieved_at", "source", "rights_status", "generation",
    )
    check(
        "factor_tape_fields",
        all(first.get(field) is not None for field in required_fields),
        ", ".join(f"{f}={first.get(f)}" for f in required_fields[:6]),
    )
    check(
        "what_changed_deltas",
        isinstance(page.get("what_changed"), list) and all(
            c.get("delta") is not None and c.get("period") for c in page["what_changed"]
        ),
        f"{len(page.get('what_changed', []))} deltas",
    )

    sentiment = page.get("sentiment", {})
    check("sentiment_boundary", sentiment.get("status") == "PROVIDER_READY", "no aggregate present")
    leaked = {
        key
        for item in sentiment.get("items", [])
        for key in RAW_LEAK_KEYS
        if key in item
    }
    check("sentiment_no_raw_identities", not leaked, "clean" if not leaked else str(leaked))

    rails = page.get("provider_readiness", {})
    check(
        "provider_rails",
        rails.get("spotify", {}).get("status") == "AUTH_REQUIRED"
        and "WAITLIST" in (rails.get("google_trends", {}).get("status") or "")
        and rails.get("soundcharts", {}).get("commercial_use_status") == "LICENSE_REQUIRED",
        json.dumps({k: v.get("status") for k, v in rails.items()}),
    )
    check(
        "coverage_state",
        artist.get("coverage_state", {}).get("factor_tape") == "OBSERVED",
        json.dumps(artist.get("coverage_state", {})),
    )

    # ── underwrite + portfolio stays reachable from the same page ─────
    underwrite = _post(
        args.base,
        "/api/underwrite",
        {
            "artist_key": args.artist,
            "market_key": args.market,
            "guarantee": {"type": "FIXED", "amount": 150000},
            "capacity": 5000,
            "ticket_price": 45,
        },
    )
    check("underwrite", underwrite.get("artist_key") == args.artist, "buyer brief returned")

    # ── compare (no winner, no ranking) ────────────────────────────────
    peers = page.get("peers", {}).get("items", [])
    if peers:
        compare = _get(
            args.base,
            "/api/artist-security/compare?a="
            + urllib.parse.quote(args.artist, safe="")
            + "&b="
            + urllib.parse.quote(peers[0]["peer_key"], safe=""),
        )
        check(
            "compare_no_winner",
            isinstance(compare, dict) and compare.get("no_winner") is True,
            "no_winner contract",
        )
    else:
        check("compare_no_winner", True, "no peer edge present; skipped")

    summary = {
        "acceptance": "ARTIST_INTELLIGENCE_TERMINAL_V1",
        "pass": len(failures) == 0,
        "checks": results,
        "failures": failures,
    }
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
