#!/usr/bin/env python3
"""Hosted acceptance test for the dedicated Festival Bloomberg terminal.

This test is intentionally independent from the acquisition Worker. It checks
that a deployed terminal exposes only the read-only product, that its
container is serving the same generation and SHA as the public CURRENT pointer,
and that the important SPA routes render against that generation.

The script owns no server process: the URL is supplied by the deployment
workflow, while Playwright owns the browser lifecycle. Access service-token
headers are optional and are read from environment variables only.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _headers() -> dict[str, str]:
    headers = {"User-Agent": "festival-bloomberg-hosted-uat/1.0"}
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID", "")
    client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
    if client_id and client_secret:
        headers.update({
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        })
    return headers


def _get_json(url: str, timeout: float = 30.0) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload: Any = json.loads(body)
        except json.JSONDecodeError:
            payload = body[:500]
        return exc.code, payload
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, str(exc)


def _api(base: str, path: str, timeout: float = 45.0) -> Any:
    status, payload = _get_json(f"{base}{path}", timeout=timeout)
    if status != 200:
        raise AssertionError(f"GET {path} returned HTTP {status}: {payload}")
    return payload


def _poll_health(base: str, timeout_seconds: float) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Wait for the container and return health, status, and CURRENT metadata."""
    deadline = time.monotonic() + timeout_seconds
    last: Any = None
    while time.monotonic() < deadline:
        current_status, current = _get_json(f"{base}/_bootstrap?artifact=metadata", timeout=30)
        health_status, health = _get_json(f"{base}/health", timeout=45)
        last = {"current": current, "health": health}
        if health_status == 200 and isinstance(health, dict) and health.get("status") == "ok":
            if current_status != 200 or not isinstance(current, dict):
                raise AssertionError(f"health is ready but CURRENT metadata is invalid: {last}")
            api_status = _api(base, "/api/status", timeout=45)
            return health, api_status, current
        time.sleep(5)
    raise AssertionError(f"hosted terminal did not become healthy within {timeout_seconds}s: {last}")


def _choose_artists(base: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Choose named artists with materialized factor and sentiment evidence."""
    demos = _api(base, "/api/demo")
    candidates = demos if isinstance(demos, list) else []
    checked: list[dict[str, Any]] = []
    for candidate in candidates:
        key = candidate.get("artist_key")
        if not key:
            continue
        try:
            payload = _api(
                base,
                "/api/artist-security/" + urllib.parse.quote(str(key), safe=""),
            )
        except AssertionError:
            continue
        factor = payload.get("factor_tape") or {}
        sentiment = payload.get("sentiment") or {}
        if factor.get("status") == "OBSERVED" and sentiment.get("status") == "OBSERVED":
            checked.append({
                "artist_key": key,
                "name": (payload.get("artist") or {}).get("name") or candidate.get("name") or key,
            })
        if len(checked) >= 2:
            return checked[0], checked[1]
    raise AssertionError(
        "hosted serving generation has no two named artists with both factor and sentiment rows; "
        f"checked {len(candidates)} demo candidates"
    )


def _assert_exact_generation(
    health: dict[str, Any], status: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    generation = current.get("generation")
    sha256 = current.get("sha256")
    assert generation and str(generation).startswith("terminal_v1_"), current
    assert isinstance(sha256, str) and len(sha256) == 64, current
    assert health.get("generation") == generation, {"health": health, "current": current}
    assert health.get("sha256") == sha256, {"health": health, "current": current}
    assert status.get("generation") == generation, {"status": status, "current": current}
    assert status.get("sha256") == sha256, {"status": status, "current": current}
    return {"generation": generation, "sha256": sha256}


def _browser_uat(
    base: str,
    out_dir: Path,
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AssertionError("Playwright is required for hosted UAT") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    checks: dict[str, bool] = {}
    screenshots: list[str] = []
    trace_path = out_dir / "hosted_uat_trace.zip"

    def record(name: str, condition: bool, detail: str = "") -> None:
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(f"{name}: {detail}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(extra_http_headers=_headers(), viewport={"width": 1600, "height": 1200})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(f"{message.type}: {message.text}")
            if message.type == "error" else None,
        )

        def visit(route: str, marker: str, screenshot_name: str | None = None) -> str:
            page.goto(f"{base}{route}", wait_until="domcontentloaded", timeout=180_000)
            page.get_by_text(marker, exact=False).first.wait_for(timeout=180_000)
            text = page.locator("body").inner_text(timeout=30_000)
            if screenshot_name:
                page.screenshot(path=str(out_dir / screenshot_name), full_page=True)
                screenshots.append(screenshot_name)
            return text

        try:
            home = visit("/#/home", "Talent Buyer Terminal", "01_home.png")
            record("home_loads", "Talent Buyer Terminal" in home)

            search_route = "/#/search/" + urllib.parse.quote(left["name"], safe="")
            search = visit(search_route, left["name"])
            record("search_works", left["name"] in search)

            artist_route = "/#/artist/" + urllib.parse.quote(left["artist_key"], safe="")
            artist = visit(artist_route, "Artist factor tape", "02_artist_factor_tape.png")
            record("artist_security_loads", left["name"] in artist)
            record("factor_tape_visible", "artist factor tape" in artist.casefold())
            record("sentiment_visible", "sentiment" in artist.casefold())
            record("youtube_value_visible", "YOUTUBE_API" in artist)
            record("provenance_visible", "OFFICIAL_API" in artist or "RIGHTS" in artist.upper())
            record("underwrite_action_visible", "Underwrite" in artist)

            markets = visit("/#/markets", "Markets")
            record("markets_load", "Markets" in markets)

            compare_route = (
                "/#/compare?a="
                + urllib.parse.quote(left["artist_key"], safe="")
                + "&b="
                + urllib.parse.quote(right["artist_key"], safe="")
            )
            compare = visit(compare_route, "Compare")
            record("compare_load", "dimensions" in compare.casefold() or "evidence" in compare.casefold())

            underwrite = visit(
                "/#/underwrite?a=" + urllib.parse.quote(left["artist_key"], safe=""),
                "Underwrite",
            )
            record("underwrite_load", "pre-offer buyer brief" in underwrite.casefold())

            portfolio = visit("/#/portfolio", "Portfolio")
            record("portfolio_load", "Portfolio" in portfolio)
            record("no_page_errors", not page_errors, "; ".join(page_errors[:3]))
            record("no_console_errors", not console_errors, "; ".join(console_errors[:3]))
        except Exception:
            context.tracing.stop(path=str(trace_path))
            browser.close()
            raise
        else:
            context.tracing.stop()
            browser.close()

    (out_dir / "console_errors.json").write_text(
        json.dumps({"console_errors": console_errors, "page_errors": page_errors}, indent=2),
        encoding="utf-8",
    )
    return {"checks": checks, "screenshots": screenshots, "console_errors": console_errors, "page_errors": page_errors}


def run(url: str, out_dir: Path, timeout_seconds: float) -> int:
    base = url.rstrip("/")
    health, status, current = _poll_health(base, timeout_seconds)
    generation = _assert_exact_generation(health, status, current)
    left, right = _choose_artists(base)
    browser = _browser_uat(base, out_dir, left, right)
    result = {
        "status": "PASS",
        "url": base,
        "generation": generation,
        "artists": [left, right],
        "health": health,
        "browser": browser,
    }
    (out_dir / "hosted_uat_results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("STAGING_URL", ""))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("artifacts/hosted_terminal_uat"),
    )
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    if not args.url:
        parser.error("--url or STAGING_URL is required")
    return run(args.url, args.out_dir, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
