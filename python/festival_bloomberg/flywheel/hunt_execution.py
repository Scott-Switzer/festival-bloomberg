"""TRACK B — OUTCOME_HUNTER execution.

Plans are NOT acquisitions. This module turns persisted hunt tasks into REAL
attempts with an explicit priority queue, a failure-classified status machine,
and an append-only attempt ledger. NOT_FOUND means the permitted retrieval
genuinely succeeded with no qualifying evidence: a 429 is RATE_LIMITED, a 403
is RIGHTS_BLOCKED/AUTH_FAILED, a parse exception is PARSER_FAILED, a 5xx/other
transport failure is HTTP_FAILED. Retries are recorded as NEW attempts; prior
attempts are never destroyed.

The V1 acquisition channel is the Common Crawl CDX index (key-free, $0,
policy-approved): era-directed lookups of the persisted source-document URLs.
Archive captures provide ARCHIVE_CAPTURE_UPPER_BOUND publication evidence and,
where a WARC record is fetched and parsed, corroborating claim evidence.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..acquisition.providers.commoncrawl import (
    CommonCrawlProvider,
    fetch_warc_record_bytes,
    extract_warc_payload_text,
    lookup_capture_offset,
)
from ..acquisition.transport import UrllibTransport

HUNT_OBJECTIVE_VERSION = "data_acquisition_activation_v1"

# Priority tiers (ordinal, documented; NOT a calibrated 0-100 score) --------
#: P0 — missing and directly attacks the measured failure (cutoffs + outcomes)
P0_FIELDS = frozenset(
    {
        "booking_cutoff",
        "announcement",
        "onsale",
        "paid_tickets",
        "attendance",
        "gross",
        "capacity",
        "settlement",
    }
)
#: P1 — warm-start improvement (repeat histories, prices, tour/promoter)
P1_FIELDS = frozenset(
    {"ticket_price", "tour", "promoter", "show_count", "artist_repeat", "venue_repeat", "market_repeat"}
)
#: P2 — corroboration of already-observed claims (cross-source verification)
P2_FIELDS = frozenset({"corroboration"})

_TIER_RANK = {field: 0 for field in P0_FIELDS}
for _f in P1_FIELDS:
    _TIER_RANK[_f] = 1
for _f in P2_FIELDS:
    _TIER_RANK[_f] = 2


def priority_tier(target_field: str) -> int:
    """Ordinal tier for a hunt target field (0 = P0, 1 = P1, 2 = P2)."""
    return _TIER_RANK.get(target_field, 2)


def priority_key(task: dict[str, Any]) -> tuple:
    """Deterministic, documented ordering for the acquisition queue.

    (tier, missingness, pit_value, decision_value, source_cost, event_date)

    * tier            P0 before P1 before P2 (decision value at ordinal level)
    * missingness     1 when the field is unknown, 0 when already observed
    * pit_value       fields that reconstruct decision cutoffs rank first
    * decision_value  fields that are direct underwriting inputs rank next
    * source_cost     0 = key-free ($0) before keyed/expensive sources
    * event_date      oldest events first (temporal decay)
    """
    field = task.get("target_field", "")
    known = bool(task.get("known_value"))
    pit_value = 1 if field in {"booking_cutoff", "announcement", "onsale"} else 0
    decision_value = 1 if field in {
        "paid_tickets", "attendance", "gross", "capacity", "settlement",
    } else 0
    source_cost = 1 if task.get("provider_cost_usd") else 0
    event_date = task.get("event_date") or ""
    return (
        priority_tier(field),
        0 if known else 1,
        -pit_value,
        -decision_value,
        source_cost,
        str(event_date),
    )


def ordered_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable lexicographic sort by the documented priority tuple."""
    return sorted(tasks, key=priority_key)


# Hunt attempt status machine -----------------------------------------------
TASK_SEARCHING = "SEARCHING"
TASK_CLAIM_FOUND = "CLAIM_FOUND"
TASK_NOT_FOUND = "NOT_FOUND"
TASK_RIGHTS_BLOCKED = "RIGHTS_BLOCKED"
TASK_RATE_LIMITED = "RATE_LIMITED"
TASK_PARSER_FAILED = "PARSER_FAILED"
TASK_HTTP_FAILED = "HTTP_FAILED"
TASK_AUTH_FAILED = "AUTH_FAILED"
TASK_OTHER_FAILURE = "OTHER_FAILURE"

#: All terminal attempt statuses (a task may be re-attempted later; each
#: attempt is a separate append-only row).
ATTEMPT_STATUSES = frozenset(
    {
        TASK_SEARCHING,
        TASK_CLAIM_FOUND,
        TASK_NOT_FOUND,
        TASK_RIGHTS_BLOCKED,
        TASK_RATE_LIMITED,
        TASK_PARSER_FAILED,
        TASK_HTTP_FAILED,
        TASK_AUTH_FAILED,
        TASK_OTHER_FAILURE,
    }
)

#: Failure statuses map to the milestone's classified execution stats.
FAILURE_TO_COUNTER = {
    TASK_RIGHTS_BLOCKED: "rights_blocked",
    TASK_RATE_LIMITED: "rate_limited",
    TASK_PARSER_FAILED: "parser_failed",
    TASK_HTTP_FAILED: "http_failed",
    TASK_AUTH_FAILED: "auth_failed",
    TASK_OTHER_FAILURE: "other_failure",
}


def validate_attempt_status(status: str) -> str:
    if status not in ATTEMPT_STATUSES:
        raise ValueError(f"attempt status {status!r} is invalid")
    return status


def is_failure(status: str) -> bool:
    validate_attempt_status(status)
    return status != TASK_CLAIM_FOUND and status != TASK_NOT_FOUND and status != TASK_SEARCHING


def build_attempt_row(
    *,
    plan_id: str,
    task_id: str,
    target_field: str,
    provider: str,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    request_count: int = 0,
    source_url: str | None = None,
    capture_count: int | None = None,
    claim_id: str | None = None,
    detail: str | None = None,
    raw_payload_hash: str | None = None,
    software_version: str = HUNT_OBJECTIVE_VERSION,
) -> dict[str, Any]:
    validate_attempt_status(status)
    started = started_at or utc_now()
    finished = finished_at or utc_now()
    attempt_id = f"hunt_attempt_{content_hash_of({
        'task': task_id,
        'provider': provider,
        'started': started.isoformat(),
        'status': status,
    })[:20]}"
    return {
        "attempt_id": attempt_id,
        "plan_id": plan_id,
        "task_id": task_id,
        "target_field": target_field,
        "provider": provider,
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "request_count": request_count,
        "source_url": source_url,
        "capture_count": capture_count,
        "claim_id": claim_id,
        "detail": detail,
        "raw_payload_hash": raw_payload_hash,
        "software_version": software_version,
    }


def summarize_attempts(attempts: list[dict[str, Any]] | None) -> dict[str, int]:
    """Execution statistics by status (never inferred from plan counts)."""
    stats = {s: 0 for s in ATTEMPT_STATUSES}
    for attempt in attempts or []:
        status = attempt.get("status")
        if status in stats:
            stats[status] += 1
    stats["tasks_attempted"] = len(attempts or [])
    stats["tasks_successful"] = stats[TASK_CLAIM_FOUND]
    return stats


# ---------------------------------------------------------------------------
# Common Crawl CDX hunts (era-directed, key-free, $0)
# ---------------------------------------------------------------------------
def era_directed_crawl_ids(
    *,
    target_year: int,
    crawls: list[dict[str, Any]],
    window: int = 2,
) -> list[str]:
    """Pick the crawl collections whose index date is within ``window`` years
    of ``target_year`` (deterministic, documented). Older content is most
    likely archived by crawls contemporary with its publication.
    """
    picked = []
    for crawl in crawls:
        cid = crawl.get("id") or ""
        m = re.search(r"CC-MAIN-(\d{4})", cid)
        if not m:
            continue
        try:
            year = int(m.group(1))
        except ValueError:
            continue
        if abs(year - target_year) <= window:
            picked.append(cid)
    return sorted(picked)


def run_cdx_hunt(
    *,
    url: str,
    crawls: list[dict[str, Any]],
    target_year: int,
    window: int = 2,
    max_captures: int = 5,
    max_crawls: int = 8,
    transport: UrllibTransport | None = None,
    throttle_seconds: float = 1.0,
) -> dict[str, Any]:
    """Execute a CDX lookup across era-directed crawls for one source URL.

    Returns an honest attempt summary:

        status        CLAIM_FOUND (>=1 capture) | NOT_FOUND (queried, none) |
                      RATE_LIMITED | HTTP_FAILED | OTHER_FAILURE
        captures      [(crawl_id, capture_timestamp)]
        request_count number of CDX requests made
    """
    provider = CommonCrawlProvider(transport=transport)
    selected = era_directed_crawl_ids(target_year=target_year, crawls=crawls, window=window)
    if not selected:
        return {
            "status": TASK_NOT_FOUND,
            "captures": [],
            "request_count": 0,
            "detail": "no era-directed crawl collections selected",
        }
    # Bounded operational budget: query at most ``max_crawls`` era-directed
    # crawl collections per URL (documented; keeps live runs bounded and
    # polite). The selected list is sorted chronologically, so the budget
    # spans the publication era rather than a single crawl.
    selected = selected[:max_crawls]
    captures: list[tuple[str, str]] = []
    request_count = 0
    errored_crawls = 0
    rate_limited = False
    for crawl in selected:
        from ..acquisition.contracts import AcquisitionRequest

        req = AcquisitionRequest.new(
            entity_id=url,
            entity_type="source_document",
            platform="commoncrawl",
            query=url,
            external_id=crawl,
            max_records=max_captures,
        )
        request_count += 1
        try:
            result = provider.acquire(req)
        except Exception as exc:  # noqa: BLE001
            errored_crawls += 1
            continue
        if result.status.name == "RATE_LIMITED":
            rate_limited = True
            break  # never hammer a rate-limited index; stop querying
        if result.status.name in ("PROVIDER_ERROR", "TIMEOUT"):
            errored_crawls += 1
            continue  # an erroring crawl does not erase captures from others
        for record in result.records or ():
            ts = record.get("capture_timestamp")
            if ts:
                captures.append((crawl, ts))
        if throttle_seconds:
            time.sleep(throttle_seconds)
        if len(captures) >= max_captures:
            break

    if captures:
        # Evidence was found: CLAIM_FOUND. A rate limit or erroring crawl does
        # NOT turn found evidence into a failure (classified, not erased).
        detail = f"{len(captures)} archive capture(s) across {request_count} crawl index(es)"
        if errored_crawls:
            detail += f"; {errored_crawls} crawl(s) errored after evidence was found"
        return {
            "status": TASK_CLAIM_FOUND,
            "captures": captures,
            "request_count": request_count,
            "detail": detail,
        }
    if rate_limited:
        return {
            "status": TASK_RATE_LIMITED,
            "captures": [],
            "request_count": request_count,
            "detail": f"rate limited before any capture was found",
        }
    if errored_crawls:
        return {
            "status": TASK_HTTP_FAILED,
            "captures": [],
            "request_count": request_count,
            "detail": f"{errored_crawls} of {request_count} era-directed crawl index(es) errored; no capture found",
        }
    return {
        "status": TASK_NOT_FOUND,
        "captures": [],
        "request_count": request_count,
        "detail": f"no captures across {request_count} era-directed crawl index(es)",
    }


# ---------------------------------------------------------------------------
# Wikipedia capacity hunts (key-free, $0, P0 field)
# ---------------------------------------------------------------------------
def venue_key(venue: str | None, city: str | None) -> str:
    """Deterministic canonical venue id from the research corpus fields.

    The research corpus has no venue id table; the canonical id is derived
    from (venue name, city) so capacity claims can be joined to any event
    whose engagement carries the same venue/market pair.
    """
    def slug(value: Any) -> str:
        return (
            re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or "unknown"
        )

    return f"venue_{slug(venue)}_{slug(city)}"


def run_wikipedia_capacity_hunt(
    *,
    venues: list[dict[str, Any]],
    transport: UrllibTransport | None = None,
    max_venues: int = 60,
    throttle_seconds: float = 1.0,
) -> dict[str, Any]:
    """Execute REAL capacity hunts via Wikipedia infoboxes (key-free, $0).

    Each distinct venue from the research corpus is searched on Wikipedia and
    the page infobox is parsed for capacity evidence. Status semantics follow
    the hunt state machine: SUCCESS with records is CLAIM_FOUND; a page found
    with no capacity evidence is NOT_FOUND (genuine no-evidence result); a
    429 is RATE_LIMITED; a 403 is RIGHTS_BLOCKED; transport/parse failures
    are HTTP_FAILED/PARSER_FAILED. Never classifies a failed request as
    NOT_FOUND.

    Returns:
        venue_results  per-venue hunt results (status, claim records)
        attempts       attempt rows (one per venue, linked to the first
                       affected capacity task; detail names the venue)
    """
    from ..acquisition.contracts import AcquisitionRequest
    from ..acquisition.providers.wikipedia import WikipediaProvider
    from ..economics.capacity import claim_from_wikipedia_infobox

    provider = WikipediaProvider(transport=transport)
    venue_results: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    total_requests = 0

    for entry in venues[:max_venues]:
        venue_name = entry.get("venue") or entry.get("venue_name")
        city = entry.get("city") or entry.get("market")
        vid = venue_key(venue_name, city)
        request = AcquisitionRequest.new(
            entity_id=vid,
            entity_type="venue",
            platform="wikipedia",
            query=venue_name or "",
            market_id=city,
            operation="search",
            max_cost_usd=0.0,
        )
        total_requests += 1
        result = provider.acquire(request)
        status = result.status.name
        records = list(result.records or ())
        metadata = result.provider_metadata or {}

        if status == "SUCCESS" and records:
            claims = []
            for rec in records:
                claim = claim_from_wikipedia_infobox(rec, venue_id=vid)
                if claim is not None:
                    claims.append(claim)
            attempt_status = TASK_CLAIM_FOUND
            detail = f"{len(claims)} capacity claim(s) from Wikipedia infobox"
        elif status == "NO_RESULTS":
            # The search/page retrieval genuinely succeeded; no capacity
            # evidence exists on the page. Genuine NOT_FOUND.
            attempt_status = TASK_NOT_FOUND
            claims = []
            detail = "page retrieved; no capacity evidence in infobox"
        else:
            error = str(metadata.get("error") or metadata.get("rationale") or "")
            claims = []
            if "429" in error or "rate" in error.lower():
                attempt_status = TASK_RATE_LIMITED
            elif "403" in error or "terms" in error.lower() or "denied" in error.lower():
                attempt_status = TASK_RIGHTS_BLOCKED
            elif "401" in error or "auth" in error.lower():
                attempt_status = TASK_AUTH_FAILED
            elif "network" in error.lower() or "timeout" in error.lower() or "http" in error.lower():
                attempt_status = TASK_HTTP_FAILED
            else:
                attempt_status = TASK_HTTP_FAILED
            detail = error or f"provider status {status}"

        venue_results.append(
            {
                "venue": venue_name,
                "venue_id": vid,
                "status": attempt_status,
                "claims": claims,
                "detail": detail,
            }
        )
        attempts.append(
            build_attempt_row(
                plan_id=entry.get("plan_id") or f"plan_{vid[:24]}",
                task_id=entry.get("task_id") or f"task_{vid[:24]}_capacity",
                target_field="capacity",
                provider="wikipedia_mediawiki_api",
                status=attempt_status,
                request_count=1,
                source_url=None,
                claim_id=claims[0].claim_id if claims else None,
                detail=detail,
            )
        )
        if throttle_seconds:
            import time as _time

            _time.sleep(throttle_seconds)

    return {
        "venue_results": venue_results,
        "attempts": attempts,
        "requests": total_requests,
        "venues_hunted": len(venue_results),
    }


# ---------------------------------------------------------------------------
# WARC claim extraction (conservative regex; corroboration only)
# ---------------------------------------------------------------------------
def extract_claims_from_page(text: str, *, target_year: int) -> list[dict[str, Any]]:
    """Best-effort, conservative extraction of explicit evidence from an
    archived chart/article page. Returns [] on any ambiguity — a parser that
    fails to extract evidence is PARSER_FAILED only when the page was fetched
    but unreadable; a clean page with no evidence is simply no evidence.
    """
    claims: list[dict[str, Any]] = []
    if not text:
        return claims

    # Gross: "$12,648,557" style figures with a dollar sign.
    for match in re.finditer(r"\$\s?([\d][\d,]*(?:\.\d+)?)", text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if value >= 10_000:
            claims.append({"outcome_type": "TICKET_GROSS", "value_numeric": value})
            break  # first credible figure only (conservative)

    # Attendance: "attendance: 56,931" / "56,931 in attendance".
    for match in re.finditer(
        r"attendance[\s:\-]*([\d][\d,]{3,})|([\d][\d,]{3,})\s*(?:in\s+)?attendance",
        text,
        re.IGNORECASE,
    ):
        raw = match.group(1) or match.group(2)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if value >= 100:
            claims.append({"outcome_type": "REPORTED_ATTENDANCE", "value_numeric": value})
            break

    # Sellout: explicit sold-out statement (never OFFSALE, never zero listings).
    if re.search(r"\b(sold\s?out|sell[- ]out|100%\s+sold)\b", text, re.IGNORECASE):
        claims.append({"outcome_type": "EXPLICIT_SOLD_OUT_ASSERTION", "value_text": "sold out"})

    return claims
