"""Direct Apify client for source bakeoff — bypasses Monid proxy.

Uses the Apify REST API directly (https://api.apify.com/v2/) with APIFY_TOKEN
from the established config system.

Works alongside the Monid CLI wrapper. When APIFY_TOKEN is available:
- Actor metadata/schema fetched directly
- Runs are fired, polled, and datasets retrieved via direct HTTP
- Cost and run metadata preserved

When APIFY_TOKEN is NOT configured:
- Monid CLI is used for all Apify actor access (Monid proxies to Apify)
- This is functionally equivalent but incurs Monid's aggregation margin

Architecture: Monid for discovery, direct Apify when cheaper/faster.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

from ..localenv import load_local_env

DEFAULT_BASE_URL = "https://api.apify.com/v2"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token() -> str | None:
    """Return Apify token without printing it."""
    load_local_env()
    return os.environ.get("APIFY_TOKEN") or None


def is_configured() -> bool:
    return _token() is not None


def inspect_actor(actor_id: str) -> dict[str, Any]:
    """Fetch actor metadata and input schema from Apify directly.

    Returns: { actor_id, title, description, input_schema, default_run_options, ... }
    The actor_id uses ~ as namespace separator (e.g. crawlergang~songkick-scraper).
    """
    token = _token()
    if token is None:
        return {"status": "NOT_CONFIGURED", "actor_id": actor_id, "error": "APIFY_TOKEN not set"}

    url = f"{DEFAULT_BASE_URL}/acts/{actor_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"status": "ERROR", "actor_id": actor_id, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"status": "ERROR", "actor_id": actor_id, "error": str(e)}

    actor_data = data.get("data", data)  # handle both wrapped/unwrapped

    # Extract input schema from tagged build
    input_schema = {}
    build_id = None
    tagged = actor_data.get("taggedBuilds", {})
    if tagged:
        latest_tag = tagged.get("latest", {})
        build_id = latest_tag.get("buildId")

    if build_id:
        build_url = f"{DEFAULT_BASE_URL}/acts/{actor_id}/builds/{build_id}"
        try:
            b_req = urllib.request.Request(build_url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(b_req, timeout=15) as b_resp:
                build_data = json.loads(b_resp.read().decode("utf-8"))
            bd = build_data.get("data", build_data)
            raw_schema = bd.get("inputSchema", {})
            # inputSchema may be a JSON string — parse if needed
            if isinstance(raw_schema, str):
                try:
                    input_schema = json.loads(raw_schema)
                except json.JSONDecodeError:
                    input_schema = {}
            else:
                input_schema = raw_schema
        except Exception:
            pass  # schema unavailable

    # Pricing info
    pricing = actor_data.get("pricingInfos", {})
    usage_usd = actor_data.get("usageTotalUsd") or (pricing.get("pricePerUnitUsd") if isinstance(pricing, dict) else None)

    return {
        "status": "OBSERVED",
        "actor_id": actor_id,
        "title": actor_data.get("title", ""),
        "description_short": (actor_data.get("description") or "")[:500],
        "input_schema": input_schema,
        "schema_properties": list(input_schema.get("properties", {}).keys()) if isinstance(input_schema, dict) else [],
        "example_run_input": actor_data.get("exampleRunInput", {}),
        "default_run_options": actor_data.get("defaultRunOptions", {}),
        "stats": actor_data.get("stats", {}),
        "build_version": tagged.get("latest", {}).get("buildNumber", "unknown") if tagged else "unknown",
        "build_id": build_id,
        "pricing_model": str(pricing)[:200] if pricing else None,
        "usage_total_usd": usage_usd,
        "modified_at": actor_data.get("modifiedAt"),
        "categories": actor_data.get("categories", []),
        "is_deprecated": actor_data.get("isDeprecated", False),
    }


def run_actor(
    actor_id: str,
    input_body: dict[str, Any],
    *,
    max_polls: int = 30,
    poll_interval: float = 2.0,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run an Apify actor, poll until completion, retrieve dataset records.

    Returns: { status, run_id, records, record_count, cost_usd, latency_ms, ... }
    """
    token = _token()
    if token is None:
        return {"status": "NOT_CONFIGURED", "actor_id": actor_id, "error": "APIFY_TOKEN not set"}

    started = time.monotonic()
    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Step 1: Start the run.
    run_url = f"{DEFAULT_BASE_URL}/acts/{actor_id}/runs"
    body = json.dumps({"input": input_body}).encode("utf-8")
    req = urllib.request.Request(run_url, data=body, headers=auth, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            run_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {
            "status": "ERROR", "actor_id": actor_id,
            "error": f"HTTP {e.code}: {e.reason}",
            "error_body": e.read().decode("utf-8", errors="replace")[:500],
        }
    except Exception as e:
        return {"status": "ERROR", "actor_id": actor_id, "error": str(e)}

    run_id = run_data.get("data", {}).get("id")
    if not run_id:
        return {"status": "ERROR", "actor_id": actor_id, "error": "no run_id in response"}

    # Step 2: Poll until complete.
    state = run_data.get("data", {}).get("status", "RUNNING")
    polls = 0
    while state in ("RUNNING", "READY", "QUEUED") and polls < max_polls:
        time.sleep(poll_interval)
        status_url = f"{DEFAULT_BASE_URL}/actor-runs/{run_id}"
        req = urllib.request.Request(status_url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                run_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"status": "ERROR", "actor_id": actor_id, "run_id": run_id, "error": str(e)}
        state = run_data.get("data", {}).get("status", "UNKNOWN")
        polls += 1

    latency = time.monotonic() - started

    if state != "SUCCEEDED":
        return {
            "status": "RUN_FAILED", "actor_id": actor_id, "run_id": run_id,
            "final_state": state, "polls": polls, "latency_ms": round(latency * 1000),
        }

    # Step 3: Retrieve dataset.
    dataset_id = run_data.get("data", {}).get("defaultDatasetId")
    records: list[dict] = []
    if dataset_id:
        items_url = f"{DEFAULT_BASE_URL}/datasets/{dataset_id}/items?limit=100"
        req = urllib.request.Request(items_url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            if isinstance(items, list):
                records = items
            elif isinstance(items, dict) and "data" in items:
                records = items["data"]
        except Exception as e:
            pass  # Empty dataset or error — records remains []

    # Step 4: Extract cost/usage.
    usage = run_data.get("data", {}).get("usage", {})
    cost = usage.get("ACTOR_COMPUTE_UNITS") or usage.get("totalCost")

    return {
        "status": "COMPLETED",
        "actor_id": actor_id,
        "run_id": run_id,
        "records": records,
        "record_count": len(records),
        "cost_usd": cost,
        "latency_ms": round(latency * 1000),
        "polls": polls,
        "final_state": state,
        "dataset_id": dataset_id,
    }


def inspect_and_run(
    actor_id: str,
    input_body: dict[str, Any],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Full inspect + run pipeline for one actor.

    In dry_run mode, only inspects (free). Otherwise runs with bounded input.
    """
    if not is_configured():
        return {
            "status": "NOT_CONFIGURED",
            "actor_id": actor_id,
            "message": "Set APIFY_TOKEN to use direct Apify; use Monid CLI as fallback",
        }

    inspection = inspect_actor(actor_id)
    if dry_run:
        return {
            "status": "INSPECTED_DRY_RUN",
            "actor_id": actor_id,
            "inspection": inspection,
        }

    execution = run_actor(actor_id, input_body)
    execution["inspection"] = inspection
    return execution