"""Monid-based marketplace event URL resolver.

Resolves a canonical event (artist + venue + city + date) ONCE to its
exact marketplace event page. Once the mapping exists, recurring observation
uses targeted fetch of the known URL.

Strategy:
  Phase 1 — FREE tinyfish/search to find marketplace event pages
  Phase 2 — FREE tinyfish/fetch to get the exact page
  Phase 3 — Parse structured data (JSON-LD first) from the HTML
  Phase 4 — context.dev/web/scrape/html as fallback ($0.0009/call)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from ..localenv import load_local_env

BASE_URL = "https://api.monid.ai"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key() -> str:
    load_local_env()
    return os.environ.get("MONID_API_KEY") or ""


def _post(path: str, body: dict) -> dict[str, Any]:
    key = _key()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", errors="replace")[:400]}


def _get(path: str) -> dict[str, Any]:
    key = _key()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", errors="replace")[:400]}


def monid_run(provider: str, endpoint: str, query_params: dict | None = None,
              body: dict | None = None, wait_poll: float = 2.0, max_polls: int = 15) -> dict[str, Any]:
    """Run a Monid endpoint, poll until complete, return full output."""
    run_body: dict[str, Any] = {"provider": provider, "endpoint": endpoint}
    if query_params:
        run_body["queryParams"] = query_params
    if body:
        run_body["body"] = body

    start = time.monotonic()
    resp = _post("/v1/run", run_body)
    run_id = resp.get("runId") or resp.get("run_id")
    status = resp.get("status", "RUNNING")

    polls = 0
    while status not in ("COMPLETED", "FAILED", "ERROR") and polls < max_polls:
        time.sleep(wait_poll)
        resp = _get(f"/v1/runs/{run_id}")
        status = resp.get("status", "RUNNING")
        polls += 1

    output = resp.get("output") or resp.get("data") or {}
    return {
        "run_id": run_id,
        "status": status,
        "output": output,
        "cost": resp.get("cost") or resp.get("price"),
        "latency_ms": int((time.monotonic() - start) * 1000),
        "polls": polls,
    }


# ── URL Resolution ─────────────────────────────────────────────────────

MARKETPLACES = ["seatgeek.com", "vividseats.com", "stubhub.com", "gametime.co", "tickpick.com"]


def build_search_query(event: dict[str, Any], marketplace: str) -> str:
    """Build a site-scoped search query for one canonical event."""
    artist = event.get("artist_name") or ""
    venue = event.get("venue_name") or ""
    city = event.get("city") or ""
    date_str = str(event.get("event_date") or "")[:10]
    return f'site:{marketplace} "{artist}" "{venue}" {city} {date_str}'


def resolve_marketplace_url(event: dict[str, Any], marketplace: str,
                            search_provider: str = "tinyfish") -> dict[str, Any]:
    """Resolve one event to its marketplace event page URL.

    Returns {url, title, confidence, method, raw_result}.
    """
    query = build_search_query(event, marketplace)
    result = monid_run(search_provider, "/search", query_params={"query": query})
    output = result.get("output") or {}
    results = output.get("results") or []
    if not results:
        return {"status": "NOT_FOUND", "query": query, "result": result}

    # Score candidate URLs: title + snippet must validate artist + venue.
    artist_lower = (event.get("artist_name") or "").lower().strip()
    venue_lower = (event.get("venue_name") or "").lower().strip()
    city_lower = (event.get("city") or "").lower().strip()
    date_part = str(event.get("event_date") or "")[:10]
    # Extract month+day from event date for snippet matching.
    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_part)
        month_day = d.strftime("%b %-d").lower()  # e.g. "nov 7"
        month_day_num = d.strftime("%B %-d").lower()
    except Exception:
        month_day = ""
        month_day_num = ""

    for r in results:
        title = (r.get("title") or "").lower()
        snippet = (r.get("snippet") or "").lower()
        url = r.get("url") or ""
        # Must be an event-specific URL (not a general search page like the homepage).
        if not url or marketplace.replace("www.", "") not in url:
            continue
        # Skip pure homepage/search-portal URLs (must contain artist or venue slug).
        parsed = urlparse(url)
        if not parsed.path or parsed.path in ("/", ""):
            continue
        # Validate artist in title+snippet.
        if artist_lower and artist_lower not in title and artist_lower not in snippet:
            continue
        validation = ""
        confidence = 0.0
        if artist_lower:
            if artist_lower in title or artist_lower in snippet:
                validation += "ARTIST "
                confidence += 0.4
        if venue_lower and (venue_lower in title or venue_lower in snippet):
            validation += "VENUE "
            confidence += 0.3
        if month_day and (month_day in snippet or month_day_num in snippet):
            validation += "DATE "
            confidence += 0.2
        if city_lower and city_lower in snippet:
            validation += "CITY "
            confidence += 0.1

        status = "MATCHED_EXACT" if confidence >= 0.7 else (
            "MATCHED_HIGH_CONFIDENCE" if confidence >= 0.4 else "AMBIGUOUS"
        )
        return {
            "status": status,
            "url": url,
            "title": r.get("title"),
            "confidence": round(confidence, 2),
            "validation": validation.strip(),
            "method": f"monid_{search_provider}_search",
            "query": query,
        }

    return {"status": "NOT_FOUND", "query": query, "result_count": len(results)}


# ── Persistence ─────────────────────────────────────────────────────────

def persist_mapping(conn, mapping: dict[str, Any]) -> str:
    """Insert/replace a marketplace event mapping. Returns mapping_id."""
    material = f"{mapping.get('event_key')}|{mapping.get('marketplace')}"
    mid = "map::" + hashlib.sha256(material.encode()).hexdigest()[:16]

    conn.execute(
        """
        INSERT OR REPLACE INTO acquisition.marketplace_event_mappings (
            mapping_id, event_key, artist_key, venue_key, market_key,
            marketplace, marketplace_event_id, marketplace_event_url,
            resolution_method, resolution_status, resolution_confidence,
            validation_checked, source_query, source_result_url,
            resolved_at, rights_status, commercial_use_status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            mid,
            mapping.get("event_key"),
            mapping.get("artist_key"),
            mapping.get("venue_key"),
            mapping.get("market_key"),
            mapping.get("marketplace"),
            mapping.get("marketplace_event_id"),
            mapping.get("marketplace_event_url"),
            mapping.get("resolution_method"),
            mapping.get("resolution_status"),
            mapping.get("resolution_confidence"),
            mapping.get("validation_checked"),
            mapping.get("source_query"),
            mapping.get("source_result_url"),
            mapping.get("resolved_at", _now()),
            mapping.get("rights_status", "TERMS_REVIEW_REQUIRED"),
            mapping.get("commercial_use_status", "PROTOTYPE_ONLY"),
            mapping.get("notes"),
        ],
    )
    return mid


# ── Page fetch ──────────────────────────────────────────────────────────

def fetch_page(url: str, fetch_provider: str = "tinyfish", format: str = "html") -> dict[str, Any]:
    """Fetch a single page. Returns structured output for parsing."""
    if fetch_provider == "tinyfish":
        result = monid_run("tinyfish", "/fetch", body={"urls": [url], "format": format, "ttl": 3600})
        output = result.get("output") or {}
        pages = output.get("pages") or output.get("results") or []
        if pages:
            return {"status": "FETCHED", "page": pages[0], "provider": "tinyfish", "cost": result.get("cost")}
        # Fallback: try context.dev
        result2 = monid_run("context.dev", "/web/scrape/html", query_params={"url": url})
        html = result2.get("output") or {}
        return {"status": "FETCHED", "page": html, "provider": "context.dev", "cost": result2.get("cost")}

    if fetch_provider == "context.dev":
        result = monid_run("context.dev", "/web/scrape/html", query_params={"url": url})
        return {"status": "FETCHED", "page": result.get("output") or {}, "provider": "context.dev", "cost": result.get("cost")}

    return {"status": "UNKNOWN_PROVIDER", "provider": fetch_provider}


# ── Structured extraction ───────────────────────────────────────────────

def extract_from_page(page: dict[str, Any], marketplace: str) -> dict[str, Any]:
    """Try extracting structured ticket-market data from a fetched page.

    Priority: JSON-LD → embedded JSON → script blocks → HTML text.
    """
    html = page.get("html") or page.get("content") or page.get("text") or ""
    if not isinstance(html, str):
        html = json.dumps(page)

    extracted: dict[str, Any] = {}

    # 1. JSON-LD
    ld_matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
    for ld in ld_matches:
        try:
            ld_data = json.loads(ld)
            if isinstance(ld_data, dict):
                ld_type = ld_data.get("@type", "")
                if ld_type in ("Event", "MusicEvent", "Concert"):
                    offers = ld_data.get("offers") or {}
                    if isinstance(offers, dict):
                        extracted["price"] = offers.get("price")
                        extracted["currency"] = offers.get("priceCurrency")
                        extracted["availability"] = offers.get("availability")
                    elif isinstance(offers, list) and offers:
                        extracted["price_min"] = min(
                            float(o.get("price", 0)) for o in offers if o.get("price")
                        ) or None
                    extracted["name"] = ld_data.get("name")
                    extracted["startDate"] = ld_data.get("startDate")
                    loc = ld_data.get("location", {})
                    if isinstance(loc, dict):
                        extracted["venue_name"] = loc.get("name")
                        addr = loc.get("address", {})
                        if isinstance(addr, dict):
                            extracted["venue_city"] = addr.get("addressLocality")
                    break
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    # 2. Embedded __NEXT_DATA__, __NUXT__, window.__INITIAL_STATE__, etc.
    if not extracted:
        next_data = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if next_data:
            try:
                nd = json.loads(next_data.group(1))
                # Walk the Next.js props tree looking for event data
                props = nd.get("props", {}).get("pageProps", {})
                if props:
                    extracted["title"] = _deep_get(props, "event", "name") or _deep_get(props, "title")
                    extracted["price"] = _deep_get(props, "event", "price") or _deep_get(props, "price")
                    extracted["venue"] = _deep_get(props, "event", "venue", "name") or _deep_get(props, "venue", "name")
            except Exception:
                pass

    # 3. Generic script-data extraction
    if not extracted:
        state_match = re.search(
            r'(?:window\.)?__INITIAL_STATE__\s*=\s*({.*?});',
            html, re.DOTALL
        )
        if state_match:
            try:
                state = json.loads(state_match.group(1))
                extracted["raw_state_keys"] = list(state.keys())[:10] if isinstance(state, dict) else None
            except Exception:
                pass

    extracted["has_structured_data"] = bool(extracted.get("price") or extracted.get("name"))
    return extracted


def _deep_get(obj: Any, *keys: str) -> Any:
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k)
        else:
            return None
    return obj