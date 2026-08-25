"""Monid/Apify source bakeoff — discover, inspect, benchmark, measure.

Integrates with Monid CLI for discovery and execution of external data sources
(Apify actors, web scraping, social media APIs). Wraps monid discover/inspect/run
commands and logs structured results into planning.source_evaluation_log.

Credentials:
- MONID_API_KEY read from environment via localenv.load_local_env()
- APIFY_TOKEN read from environment for direct Apify calls when available
- No credential values are ever printed, logged, or serialized
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..localenv import load_local_env

SOFTWARE_VERSION = "source_bakeoff_v1"

# Verdict taxonomy
VERDICT_ADOPT = "ADOPT"
VERDICT_PILOT_ONLY = "PILOT_ONLY"
VERDICT_RESEARCH_ONLY = "RESEARCH_ONLY"
VERDICT_REJECT = "REJECT"
VERDICT_TERMS_REVIEW = "TERMS_REVIEW_REQUIRED"

VALID_VERDICTS = {
    VERDICT_ADOPT, VERDICT_PILOT_ONLY, VERDICT_RESEARCH_ONLY,
    VERDICT_REJECT, VERDICT_TERMS_REVIEW,
}

# Rights taxonomy
RIGHTS_CLEARED = "CLEARED"
RIGHTS_TERMS_REVIEW = "TERMS_REVIEW_REQUIRED"
RIGHTS_RESEARCH_ONLY = "RESEARCH_ONLY"
RIGHTS_UNKNOWN = "UNKNOWN"

VALID_RIGHTS = {RIGHTS_CLEARED, RIGHTS_TERMS_REVIEW, RIGHTS_RESEARCH_ONLY, RIGHTS_UNKNOWN}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _h(material: str, n: int = 32) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:n]


def _monid_cli() -> str:
    """Return the path to the monid CLI, or 'monid' if not found."""
    try:
        result = subprocess.run(
            ["which", "monid"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    # Try common npm global paths.
    for candidate in [
        os.path.expanduser("~/.npm-global/bin/monid"),
        "/usr/local/bin/monid",
    ]:
        if os.path.isfile(candidate):
            return candidate
    return "monid"


def _run_monid(args: list[str], timeout: int = 120) -> dict[str, Any]:
    """Run a monid CLI command with JSON output and return parsed result.

    Sets NO_COLOR=1 for machine-readable output. Never logs output.
    """
    load_local_env()
    env = {**os.environ, "NO_COLOR": "1"}
    monid = _monid_cli()
    try:
        result = subprocess.run(
            [monid] + args + ["-j"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "error": f"monid command timed out after {timeout}s"}
    except FileNotFoundError:
        return {"status": "NOT_INSTALLED", "error": "monid CLI not found"}

    if result.returncode != 0:
        return {"status": "ERROR", "error": result.stderr.strip() or "non-zero exit",
                "exit_code": result.returncode}

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "PARSE_ERROR", "raw_stdout": result.stdout[:500],
                "error": "could not parse monid JSON output"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover_endpoints(query: str, min_score: int = 50, limit: int = 10) -> dict[str, Any]:
    """Search Monid for relevant data endpoints.

    Returns structured discovery results with provider, endpoint, and relevance.
    """
    result = _run_monid([
        "discover", "-q", query, "-s", str(min_score), "-l", str(limit),
    ])
    if result.get("status") == "ERROR":
        return {"status": "ERROR", "query": query, "error": result.get("error")}
    return {
        "status": "OBSERVED",
        "query": query,
        "min_score": min_score,
        "results": result.get("results", []),
        "hints": result.get("hints", []),
    }


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
def inspect_endpoint(provider: str, endpoint: str) -> dict[str, Any]:
    """Inspect a specific endpoint schema (cost, fields, parameters)."""
    result = _run_monid([
        "inspect", "-p", provider, "-e", endpoint,
    ])
    if result.get("status") == "ERROR":
        return {"status": "ERROR", "provider": provider, "endpoint": endpoint,
                "error": result.get("error")}
    return {
        "status": "OBSERVED",
        "provider": provider,
        "endpoint": endpoint,
        "schema": result,
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def execute_source(
    *,
    provider: str,
    endpoint: str,
    input_body: dict[str, Any],
    output_file: str | None = None,
    wait_timeout: int = 60,
    query_context: str = "",
) -> dict[str, Any]:
    """Execute a Monid endpoint, wait for completion, and return results.

    Saves raw output to output_file if specified. Logs cost/latency/record_count.
    Never prints credential values.
    """
    start = time.monotonic()
    input_json = json.dumps(input_body)

    # Fire the run with --wait
    run_result = _run_monid([
        "run", "-p", provider, "-e", endpoint,
        "-i", input_json,
        "-w", str(wait_timeout),
    ], timeout=wait_timeout + 30)

    latency = time.monotonic() - start

    if run_result.get("status") in ("ERROR", "TIMEOUT", "NOT_INSTALLED", "PARSE_ERROR"):
        return {
            "status": "ERROR",
            "provider": provider,
            "endpoint": endpoint,
            "query_context": query_context,
            "error": run_result.get("error"),
            "latency_ms": round(latency * 1000),
        }

    # Extract useful metrics from the run result.
    record_count = _estimate_record_count(run_result)
    cost_usd = _extract_cost(run_result)

    output: dict[str, Any] = {
        "status": "COMPLETED",
        "provider": provider,
        "endpoint": endpoint,
        "query_context": query_context,
        "retrieved_at": _now(),
        "latency_ms": round(latency * 1000),
        "record_count": record_count,
        "cost_usd": cost_usd,
        "run_id": run_result.get("runId") or run_result.get("id"),
        "raw_summary": _summarize_payload(run_result),
    }

    if output_file:
        try:
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as fh:
                json.dump(run_result, fh, default=str, indent=2)
            output["saved_to"] = output_file
        except OSError as e:
            output["save_error"] = str(e)

    return output


def _estimate_record_count(result: dict[str, Any]) -> int:
    """Estimate the number of records in a Monid result payload."""
    output = result.get("output") or result.get("result") or result
    if isinstance(output, list):
        return len(output)
    if isinstance(output, dict):
        # Common patterns: results, items, data, records.
        for key in ("results", "items", "data", "records", "events"):
            if key in output and isinstance(output[key], list):
                return len(output[key])
        # Count top-level keys if no array found.
        return len(output)
    return 0


def _extract_cost(result: dict[str, Any]) -> float | None:
    """Extract cost from Monid run result."""
    cost = result.get("cost") or result.get("usage", {}).get("cost")
    if cost is not None:
        try:
            return float(cost)
        except (TypeError, ValueError):
            pass
    return None


def _summarize_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Create a safe summary of the payload (no secrets)."""
    return {
        "has_output": bool(result.get("output") or result.get("result")),
        "status": result.get("status"),
        "run_id": result.get("runId") or result.get("id"),
        "keys": list(result.keys()),
    }


# ---------------------------------------------------------------------------
# Balance check
# ---------------------------------------------------------------------------
def check_balance() -> dict[str, Any]:
    """Check Monid workspace balance."""
    result = _run_monid(["balance"])
    if result.get("status") == "ERROR":
        return {"status": "ERROR", "error": result.get("error")}
    return {"status": "OBSERVED", "balance": result}


# ---------------------------------------------------------------------------
# Source evaluation (full pipeline)
# ---------------------------------------------------------------------------
def evaluate_candidate_source(
    conn,
    *,
    source: str,  # "apify" or "monid"
    provider: str,
    endpoint: str,
    input_body: dict[str, Any],
    query_context: str,
    rights_status: str = RIGHTS_UNKNOWN,
    commercial_use_ok: bool = False,
) -> dict[str, Any]:
    """Run discovery → inspect → execute → log for one candidate source.

    Stores the full evaluation in planning.source_evaluation_log.
    Returns a structured acceptance matrix entry.
    """
    eval_key = _h(f"eval::{source}::{provider}::{endpoint}::{query_context}")

    # Step 1: Inspect schema
    inspection = inspect_endpoint(provider, endpoint)

    # Step 2: Execute with bounded input
    execution = execute_source(
        provider=provider,
        endpoint=endpoint,
        input_body=input_body,
        query_context=query_context,
    )

    # Step 3: Compute field analysis
    fields_observed = _extract_fields(execution.get("raw_summary", {}))
    null_rate = _compute_null_rate(execution) if execution.get("record_count", 0) > 0 else {}

    # Step 4: Persist to evaluation log
    conn.execute(
        """
        INSERT OR REPLACE INTO planning.source_evaluation_log
            (eval_key, source, actor_endpoint, query_context, retrieved_at,
             raw_payload, record_count, cost_usd, latency_ms, success,
             error_category, fields_observed, null_rate,
             rights_status, commercial_use_ok)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            eval_key,
            source,
            f"{provider}/{endpoint}",
            query_context,
            _now(),
            json.dumps(execution, default=str),
            execution.get("record_count"),
            execution.get("cost_usd"),
            execution.get("latency_ms"),
            execution.get("status") == "COMPLETED",
            execution.get("error") if execution.get("status") != "COMPLETED" else None,
            json.dumps(fields_observed),
            json.dumps(null_rate),
            rights_status,
            commercial_use_ok,
        ],
    )

    return {
        "eval_key": eval_key,
        "source": source,
        "endpoint": f"{provider}/{endpoint}",
        "query_context": query_context,
        "success": execution.get("status") == "COMPLETED",
        "record_count": execution.get("record_count"),
        "cost_usd": execution.get("cost_usd"),
        "latency_ms": execution.get("latency_ms"),
        "fields_observed": fields_observed,
        "rights_status": rights_status,
        "commercial_use_ok": commercial_use_ok,
        "verdict": None,  # set by acceptance matrix
    }


def _extract_fields(summary: dict[str, Any]) -> list[str]:
    """Extract field names from run result keys for schema analysis."""
    return sorted(summary.get("keys", []))


def _compute_null_rate(execution: dict[str, Any]) -> dict[str, float]:
    """Compute null rate per field."""
    # This requires access to raw records — simplified for now.
    return {}


# ---------------------------------------------------------------------------
# Acceptance matrix
# ---------------------------------------------------------------------------
def acceptance_matrix(
    conn,
    *,
    evaluations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate a structured acceptance matrix for evaluated sources.

    Each row has: source, endpoint, cost_shape, success_rate, latency, fields,
    verdict, rights_status, rationale.
    """
    matrix: list[dict[str, Any]] = []
    for ev in evaluations:
        row = {
            "source": ev.get("source"),
            "endpoint": ev.get("endpoint"),
            "query_context": ev.get("query_context"),
            "cost_usd": ev.get("cost_usd"),
            "latency_ms": ev.get("latency_ms"),
            "record_count": ev.get("record_count"),
            "fields_observed": ev.get("fields_observed", []),
            "success": ev.get("success"),
            "rights_status": ev.get("rights_status", RIGHTS_UNKNOWN),
            "commercial_use_ok": ev.get("commercial_use_ok", False),
            "verdict": ev.get("verdict", "NOT_YET_EVALUATED"),
            "rationale": ev.get("rationale", ""),
        }
        matrix.append(row)

    # Persist verdicts back to the log.
    for ev in evaluations:
        if ev.get("eval_key") and ev.get("verdict"):
            conn.execute(
                "UPDATE planning.source_evaluation_log SET verdict = ?, verdict_rationale = ? WHERE eval_key = ?",
                [ev["verdict"], ev.get("rationale", ""), ev["eval_key"]],
            )

    return matrix


# ---------------------------------------------------------------------------
# Candidate source portfolio (pre-configured for the bakeoff)
# ---------------------------------------------------------------------------
CANDIDATE_SOURCES: list[dict[str, Any]] = [
    {
        "name": "Eventbrite (epicscrapers)",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/epicscrapers/eventbrite-scraper",
        "query_context": "eventbrite_la_events",
        "input_body": {"location": "Los Angeles", "maxItems": 20},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "event_source",
    },
    {
        "name": "Eventbrite (solidcode)",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/solidcode/eventbrite-scraper",
        "query_context": "eventbrite_chi_events",
        "input_body": {"city": "Chicago", "maxResults": 20},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "event_source",
    },
    {
        "name": "Songkick",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/crawlergang/songkick-scraper",
        "query_context": "songkick_artist_history",
        "input_body": {"artistName": "Kendrick Lamar", "maxItems": 20},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "event_source",
    },
    {
        "name": "Resident Advisor",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/crawlerbros/resident-advisor-scraper",
        "query_context": "ra_events",
        "input_body": {"location": "Chicago", "maxItems": 20},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "event_source",
    },
    {
        "name": "Bandsintown",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/automation-lab/bandsintown-events-scraper",
        "query_context": "bandsintown_artist_tour",
        "input_body": {"artistName": "Kendrick Lamar", "maxItems": 20},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "event_source",
    },
    {
        "name": "TikTok (Clockworks)",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/clockworks/tiktok-scraper",
        "query_context": "tiktok_artist",
        "input_body": {"username": "kendricklamar", "maxItems": 10},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "social",
    },
    {
        "name": "Instagram",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/apify/instagram-api-scraper",
        "query_context": "instagram_artist",
        "input_body": {"username": "kendricklamar", "maxItems": 10},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "social",
    },
    {
        "name": "YouTube Scraper",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/hipersoft/youtube-scraper",
        "query_context": "youtube_artist",
        "input_body": {"searchTerm": "Kendrick Lamar", "maxResults": 10},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "social",
    },
    {
        "name": "Google Places",
        "source": "apify",
        "provider": "apify",
        "endpoint": "/compass/crawler-google-places",
        "query_context": "venue_enrichment",
        "input_body": {"searchString": "United Center Chicago", "maxCrawledPlaces": 5},
        "rights_status": RIGHTS_UNKNOWN,
        "category": "venue",
    },
]


def run_full_bakeoff(
    conn,
    *,
    sources: list[dict[str, Any]] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Run the complete source bakeoff: discover → inspect → execute → matrix.

    In dry-run mode (default), only discovers and inspects (free operations).
    Always checks balance before any paid execution.
    """
    if sources is None:
        sources = CANDIDATE_SOURCES

    load_local_env()
    results: list[dict[str, Any]] = []

    # Pre-check: Monid available?
    monid_check = _run_monid(["--version"], timeout=10)
    if monid_check.get("status") == "NOT_INSTALLED":
        return {"status": "MONID_NOT_INSTALLED", "error": "monid CLI not available"}

    # Check balance before any paid ops.
    if not dry_run:
        balance = check_balance()
        if balance.get("status") != "OBSERVED":
            return {"status": "BALANCE_CHECK_FAILED", "error": balance.get("error", "unknown")}

    for candidate in sources:
        cat = candidate.get("category", "unknown")
        query = candidate["name"]
        endpoint = candidate["endpoint"]
        provider = candidate["provider"]

        # Step 1: Discover
        discovery = discover_endpoints(query, min_score=30, limit=5)

        # Step 2: Inspect schema (free)
        inspection = inspect_endpoint(provider, endpoint)

        # Step 3: Execute (only if not dry-run)
        execution: dict[str, Any] = {"status": "SKIPPED_DRY_RUN", "record_count": 0, "cost_usd": None, "latency_ms": None}
        if not dry_run:
            execution = execute_source(
                provider=provider,
                endpoint=endpoint,
                input_body=candidate.get("input_body", {}),
                query_context=candidate.get("query_context", f"bakeoff_{cat}"),
                wait_timeout=60,
            )

        # Step 4: Build evaluation entry
        evaluation = {
            "name": candidate["name"],
            "source": candidate["source"],
            "provider": provider,
            "endpoint": endpoint,
            "category": cat,
            "query_context": candidate.get("query_context", ""),
            "success": execution.get("status") == "COMPLETED",
            "record_count": execution.get("record_count", 0),
            "cost_usd": execution.get("cost_usd"),
            "latency_ms": execution.get("latency_ms"),
            "discovery_score": discovery.get("results", []),
            "inspection_schema_keys": list(inspection.get("schema", {}).keys()) if inspection.get("schema") else [],
            "rights_status": candidate.get("rights_status", RIGHTS_UNKNOWN),
            "commercial_use_ok": False,
            "verdict": None,
            "rationale": "",
        }

        # Persist evaluation.
        eval_key = _h(f"eval::{candidate['source']}::{provider}::{endpoint}::{candidate.get('query_context', '')}")
        conn.execute(
            """
            INSERT OR REPLACE INTO planning.source_evaluation_log
                (eval_key, source, actor_endpoint, query_context, retrieved_at,
                 raw_payload, record_count, cost_usd, latency_ms, success,
                 error_category, rights_status, commercial_use_ok)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                eval_key,
                candidate["source"],
                f"{provider}/{endpoint}",
                candidate.get("query_context", ""),
                _now(),
                json.dumps(execution, default=str),
                execution.get("record_count"),
                execution.get("cost_usd"),
                execution.get("latency_ms"),
                execution.get("status") == "COMPLETED",
                execution.get("error") if execution.get("status") != "COMPLETED" else None,
                candidate.get("rights_status", RIGHTS_UNKNOWN),
                False,
            ],
        )
        evaluation["eval_key"] = eval_key
        results.append(evaluation)

    return {
        "status": "COMPLETED" if not dry_run else "DRY_RUN_COMPLETED",
        "dry_run": dry_run,
        "sources_evaluated": len(results),
        "successful": sum(1 for r in results if r.get("success")),
        "failed": sum(1 for r in results if not r.get("success")),
        "total_cost_usd": sum((r.get("cost_usd") or 0) for r in results),
        "evaluations": results,
        "matrix": acceptance_matrix(conn, evaluations=results),
    }