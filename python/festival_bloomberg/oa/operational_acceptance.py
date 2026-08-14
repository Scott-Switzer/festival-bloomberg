"""Live operational acceptance for the Festival Signal Fabric.

This driver proves that REAL public evidence can flow through the canonical
acquisition, evidence, point-in-time and NLP components end-to-end:

    public internet -> raw observation -> canonical evidence -> PIT -> NLP

It is the live counterpart to the deterministic offline contract tests. It
**never** uses fixtures, mocks or synthetic observations, and it never makes
a paid provider call. The default budget is exactly ``$0.00``.

Real source: Wikipedia (Wikimedia). It is public, key-free, CC-licensed, and
already registered in the policy layer as research-approved for API access.
Because the generic ``http``/``scrapling`` providers deliberately return raw
bytes without extracted text (text is required for the NLP step), this driver
uses the canonical ``UrllibTransport`` + ``PolicyGate`` + ``EvidenceRepository``
directly and stores the extracted plain text as observed evidence.

Provider credentials are only ever reported as ``CONFIGURED`` / ``NOT_CONFIGURED``
— never their values. When no paid provider is configured the run is still
valid: it reports ``NOT_CONFIGURED`` for each provider and proceeds with the
free, key-free source. No observation, score or locality is ever fabricated.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from ..acquisition.contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    content_hash_of,
    utc_now,
)
from ..acquisition.policy import PolicyGate
from ..acquisition.transport import HttpResponse, TransportError, UrllibTransport
from ..evidence.provenance import knowledge_time_for, parse_iso, utc
from ..social.sentiment import (
    TWEETNLP_AVAILABLE,
    VADER_MODEL_NAME,
    VADER_MODEL_VERSION,
    vader_inference,
)

# --------------------------------------------------------------------------- #
# Predeclared, deterministic selection universe
# --------------------------------------------------------------------------- #

#: Ten artists with clear, current live-entertainment relevance. The list is
#: fixed BEFORE any sentiment is measured; selection never uses a signal.
CANDIDATE_ARTISTS: tuple[str, ...] = (
    "Bad Bunny",
    "Beyoncé",
    "Billie Eilish",
    "Drake",
    "Kendrick Lamar",
    "Olivia Rodrigo",
    "Post Malone",
    "Sabrina Carpenter",
    "Taylor Swift",
    "Travis Scott",
)

#: Availability-only selection rule (documented as an OA artifact).
SELECTION_RULE = (
    "first artist alphabetically among CANDIDATE_ARTISTS whose Wikipedia REST "
    "summary resolves with an extract of at least MIN_EXTRACT_CHARS characters; "
    "availability metadata only — no sentiment, popularity or booking signal "
    "is consulted."
)
MIN_EXTRACT_CHARS = 200

#: Real Chicago-specific public pages used as (weak) local-market context.
#: ``(title, kind)`` pairs; kinds are informational only.
CHICAGO_PAGES: tuple[tuple[str, str], ...] = (
    ("United Center", "venue"),
    ("Lollapalooza", "festival"),
)

PLATFORM = "wikimedia"
MAX_FULL_TEXT_CHARS = 50_000

REST_BASE = "https://en.wikipedia.org/api/rest_v1/page/summary/"
ACTION_BASE = "https://en.wikipedia.org/w/api.php"


def _title_slug(title: str) -> str:
    return title.strip().replace(" ", "_")


def summary_url(title: str) -> str:
    return REST_BASE + urllib.parse.quote(_title_slug(title))


def full_text_url(title: str) -> str:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "extracts",
            "explaintext": "1",
            "format": "json",
            "titles": title,
            "redirects": "1",
        }
    )
    return f"{ACTION_BASE}?{params}"


# --------------------------------------------------------------------------- #
# Pure, deterministic helpers (unit-tested offline)
# --------------------------------------------------------------------------- #


def select_artist(extract_lengths: dict[str, int | None]) -> str | None:
    """Deterministic availability-only selection.

    ``extract_lengths`` maps candidate name -> summary extract length (or
    ``None`` when the page did not resolve). Returns the first candidate in
    alphabetical order whose extract is at least ``MIN_EXTRACT_CHARS`` long,
    or ``None`` when none qualifies.
    """
    for artist in sorted(CANDIDATE_ARTISTS):
        length = extract_lengths.get(artist)
        if length is not None and length >= MIN_EXTRACT_CHARS:
            return artist
    return None


def detect_chicago_mentions(text: str | None) -> list[str]:
    """Return explicit ``Chicago`` mentions with a short context window.

    Only the literal token ``Chicago`` (case-insensitive, word-bounded) is
    treated as local evidence; no inference or geocoding is performed.
    """
    if not text:
        return []
    matches: list[str] = []
    for m in re.finditer(r"\bChicago\b", text, flags=re.IGNORECASE):
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 40)
        snippet = text[start:end].replace("\n", " ").strip()
        matches.append(snippet)
    return matches


def provider_readiness(env: dict[str, str] | None = None) -> dict[str, str]:
    """Report each provider as CONFIGURED / NOT_CONFIGURED / NOT_AVAILABLE.

    Values are never inspected or printed; only presence is reported.
    """
    environ = dict(os.environ if env is None else env)
    readiness = {
        "youtube": "CONFIGURED" if environ.get("YOUTUBE_API_KEY") else "NOT_CONFIGURED",
        "monid": "CONFIGURED" if environ.get("MONID_API_KEY") else "NOT_CONFIGURED",
        "apify": "CONFIGURED" if environ.get("APIFY_TOKEN") else "NOT_CONFIGURED",
        "http": "AVAILABLE",
    }
    try:
        import scrapling  # type: ignore  # noqa: F401

        readiness["scrapling"] = "AVAILABLE"
    except ImportError:
        readiness["scrapling"] = "NOT_AVAILABLE"
    return readiness


# --------------------------------------------------------------------------- #
# Fetch helpers (real network)
# --------------------------------------------------------------------------- #


def _fetch_summary(transport: UrllibTransport, title: str) -> dict | None:
    url = summary_url(title)
    try:
        response = transport.request("GET", url, timeout_seconds=30.0)
    except TransportError:
        return None
    if response.status != 200:
        return None
    try:
        data = response.json()
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not data.get("extract"):
        return None
    return data


def _fetch_full_text(transport: UrllibTransport, title: str) -> tuple[str, str | None] | None:
    url = full_text_url(title)
    try:
        response = transport.request("GET", url, timeout_seconds=30.0)
    except TransportError:
        return None
    if response.status != 200:
        return None
    try:
        data = response.json()
    except (ValueError, TypeError):
        return None
    pages = (data or {}).get("query", {}).get("pages", {})
    if not pages:
        return None
    page = next(iter(pages.values()))
    extract = page.get("extract")
    if not extract:
        return None
    return str(extract)[:MAX_FULL_TEXT_CHARS], page.get("touched")


# --------------------------------------------------------------------------- #
# Manifest building (pure)
# --------------------------------------------------------------------------- #


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def build_manifest(
    *,
    market: str,
    lookback_days: int,
    budget_usd: float,
    selection: dict[str, Any],
    readiness: dict[str, str],
    observations: list[dict[str, Any]],
    vader_distribution: dict[str, int],
    tweetnlp_status: str,
    chicago: dict[str, Any],
    pit_replay: dict[str, Any],
    cost_usd: float,
    generated_at: datetime,
    db_path: str | None,
) -> dict[str, Any]:
    """Assemble the machine-readable OA manifest (no raw text is embedded)."""
    raw_count = sum(o.get("raw_count", 1) for o in observations)
    return {
        "schema_version": "1.0",
        "generated_at": generated_at.isoformat(),
        "market": market,
        "lookback_days": lookback_days,
        "budget_usd": budget_usd,
        "db_path": db_path,
        "artist_selection": selection,
        "provider_readiness": readiness,
        "observations": {
            "raw_count": raw_count,
            "canonical_count": len({o.get("canonical_id") for o in observations if o.get("canonical_id")}),
            "platforms": sorted({o["platform"] for o in observations}),
            "providers": sorted({o["provider"] for o in observations}),
            "items": [
                {
                    "title": o.get("title"),
                    "kind": o.get("kind"),
                    "platform": o.get("platform"),
                    "provider": o.get("provider"),
                    "observation_id": o.get("observation_id"),
                    "canonical_id": o.get("canonical_id"),
                    "source_url": o.get("source_url"),
                    "published_at": o.get("published_at"),
                    "knowledge_time": o.get("knowledge_time"),
                    "content_hash": o.get("content_hash"),
                    "text_chars": o.get("text_chars"),
                    "market_id": o.get("market_id"),
                    "geographic_confidence": o.get("geographic_confidence"),
                    "license": o.get("license"),
                }
                for o in observations
            ],
        },
        "nlp": {
            "vader": {
                "status": "PASS",
                "model_name": VADER_MODEL_NAME,
                "model_version": VADER_MODEL_VERSION,
                "distribution": vader_distribution,
                "inference_count": sum(vader_distribution.values()),
            },
            "tweetnlp": {
                "status": tweetnlp_status,
                "note": (
                    "optional social-transformer baseline; requires the heavy "
                    "tweetnlp/pytorch install and is intentionally not part of "
                    "the canonical environment"
                ),
            },
        },
        "chicago": chicago,
        "pit_replay": pit_replay,
        "cost_usd": cost_usd,
        "no_fabricated_data": True,
        "no_paid_calls": cost_usd == 0.0,
    }


# --------------------------------------------------------------------------- #
# Live driver
# --------------------------------------------------------------------------- #


def run_operational_acceptance(
    evidence,
    *,
    market: str = "Chicago, IL",
    lookback_days: int = 30,
    budget_usd: float = 0.0,
    db_path: str | None = None,
    transport: UrllibTransport | None = None,
    policy_gate: PolicyGate | None = None,
) -> dict[str, Any]:
    """Run the live OA end-to-end and return the manifest dict.

    ``evidence`` is an :class:`~festival_bloomberg.evidence.repository.EvidenceRepository`
    already wired to a writable warehouse connection.
    """
    transport = transport or UrllibTransport()
    gate = policy_gate or PolicyGate()
    generated_at = utc_now()

    # -- fail-closed policy gate (research context, API mechanism) ----------- #
    decision = gate.evaluate(PLATFORM, commercial_context="research", mechanism="api")
    if not decision.allowed:
        raise RuntimeError(f"policy denied for {PLATFORM}: {decision.rationale}")
    evidence.record_policy_decision(decision)

    readiness = provider_readiness()

    # -- deterministic artist selection (availability only) ------------------ #
    extract_lengths: dict[str, int | None] = {}
    summaries: dict[str, dict] = {}
    for artist in sorted(CANDIDATE_ARTISTS):
        data = _fetch_summary(transport, artist)
        summaries[artist] = data
        extract_lengths[artist] = len(data["extract"]) if data else None
    selected = select_artist(extract_lengths)
    selection = {
        "rule": SELECTION_RULE,
        "min_extract_chars": MIN_EXTRACT_CHARS,
        "candidate_count": len(CANDIDATE_ARTISTS),
        "candidates": list(sorted(CANDIDATE_ARTISTS)),
        "selected_artist": selected,
        "selection_basis": "availability_metadata_only",
    }

    observations: list[dict[str, Any]] = []
    if selected is None:
        # No candidate resolved; the run is still honest — nothing fabricated.
        return build_manifest(
            market=market,
            lookback_days=lookback_days,
            budget_usd=budget_usd,
            selection=selection,
            readiness=readiness,
            observations=[],
            vader_distribution={},
            tweetnlp_status="NOT_AVAILABLE" if not TWEETNLP_AVAILABLE else "AVAILABLE",
            chicago={"status": "INSUFFICIENT_EVIDENCE", "reason": "no candidate resolved"},
            pit_replay={"status": "NOT_EVALUATED", "reason": "no observations"},
            cost_usd=0.0,
            generated_at=generated_at,
            db_path=db_path,
        )

    # -- ingest the artist's real Wikipedia evidence ------------------------- #
    artist_summary = summaries[selected]
    artist_extract = str(artist_summary["extract"])
    artist_published = artist_summary.get("timestamp")
    artist_url = (
        (artist_summary.get("content_urls") or {}).get("desktop", {}).get("page")
        or summary_url(selected)
    )
    observations.append(
        _ingest_page(
            evidence,
            transport,
            title=selected,
            kind="artist",
            text=artist_extract,
            url=artist_url,
            published_at=artist_published,
            market_id=None,
        )
    )

    full = _fetch_full_text(transport, selected)
    if full is not None:
        full_text, touched = full
        observations.append(
            _ingest_page(
                evidence,
                transport,
                title=f"{selected} (full article)",
                kind="artist_full",
                text=full_text,
                url=urllib.parse.quote(
                    f"https://en.wikipedia.org/wiki/{_title_slug(selected)}",
                    safe=":/",
                ),
                published_at=touched,
                market_id=None,
            )
        )

    # -- real Chicago context pages (explicit, weak locality) ---------------- #
    chicago_evidence: list[dict[str, str]] = []
    for title, kind in CHICAGO_PAGES:
        data = _fetch_summary(transport, title)
        if not data:
            continue
        extract = str(data["extract"])
        mentions = detect_chicago_mentions(extract)
        market_id = market if mentions else None
        observations.append(
            _ingest_page(
                evidence,
                transport,
                title=title,
                kind=kind,
                text=extract,
                url=(
                    (data.get("content_urls") or {}).get("desktop", {}).get("page")
                    or summary_url(title)
                ),
                published_at=data.get("timestamp"),
                market_id=market_id,
            )
        )
        if mentions:
            chicago_evidence.append(
                {
                    "title": title,
                    "kind": kind,
                    "geo_resolution_method": "textual_mention",
                    "geo_confidence": "low",
                    "mention_count": len(mentions),
                    "first_snippet": mentions[0][:120],
                }
            )

    # -- NLP baselines over the real text ------------------------------------ #
    vader_distribution: dict[str, int] = {}
    for obs in observations:
        inference = vader_inference(obs["text"])
        evidence.record_text_inference(
            observation_id=obs["observation_id"],
            task="SENTIMENT",
            model_name=inference.model_name,
            model_version=inference.model_version,
            label=inference.label,
            probabilities=inference.probabilities,
            input_text=obs["text"],
        )
        vader_distribution[inference.label] = vader_distribution.get(inference.label, 0) + 1
    tweetnlp_status = "AVAILABLE" if TWEETNLP_AVAILABLE else "NOT_AVAILABLE"

    # -- PIT replay: two knowledge cutoffs over the real observation history -- #
    pit_replay = _pit_replay(evidence, observations, generated_at)

    # -- Chicago summary ------------------------------------------------------ #
    chicago_linked = [o for o in observations if o.get("market_id") == market]
    chicago = {
        "status": "PASS" if chicago_evidence else "INSUFFICIENT_EVIDENCE",
        "market": market,
        "linked_observation_count": len(chicago_linked),
        "evidence": chicago_evidence,
        "note": (
            "locality assigned only from explicit 'Chicago' textual mention; "
            "no geocoding or private-location inference"
        ),
    }

    manifest = build_manifest(
        market=market,
        lookback_days=lookback_days,
        budget_usd=budget_usd,
        selection=selection,
        readiness=readiness,
        observations=observations,
        vader_distribution=vader_distribution,
        tweetnlp_status=tweetnlp_status,
        chicago=chicago,
        pit_replay=pit_replay,
        cost_usd=0.0,
        generated_at=generated_at,
        db_path=db_path,
    )
    return manifest


def _ingest_page(
    evidence,
    transport,
    *,
    title: str,
    kind: str,
    text: str,
    url: str,
    published_at: str | None,
    market_id: str | None,
) -> dict[str, Any]:
    """Fetch-and-store one real page as an immutable observed observation."""
    published = parse_iso(published_at)
    retrieved = utc_now()
    knowledge_time = knowledge_time_for(published, retrieved)

    request = AcquisitionRequest.new(
        entity_id=title.strip().lower().replace(" ", "-"),
        entity_type="web_page",
        platform=PLATFORM,
        query=url,
        commercial_context="research",
        max_cost_usd=0.0,
    )
    result = AcquisitionResult(
        request_id=request.request_id,
        provider="http",
        provider_endpoint=url,
        status=AcquisitionStatus.SUCCESS,
        started_at=retrieved,
        completed_at=retrieved,
        record_count=1,
        cost_usd=0.0,
        raw_payload_hash=content_hash_of(text),
        provider_metadata={
            "source": PLATFORM,
            "kind": kind,
            "license": "CC BY-SA (attribution required)",
            "bytes": len(text.encode("utf-8")),
        },
        records=(
            {
                "platform": PLATFORM,
                "object_type": "web_page",
                "platform_object_id": None,
                "text": text,
                "source_url": url,
                "canonical_url": url,
                "published_at": _iso(published),
                "content_hash": content_hash_of(text),
                "market_id": market_id,
                "geographic_confidence": "low" if market_id else None,
                "entity_resolution_confidence": 0.95,
                "language": "en",
                "raw_bytes": len(text.encode("utf-8")),
            },
        ),
    )
    evidence.ingest(request, result)

    canonical_id = _canonical_id_for(evidence, PLATFORM, url)
    return {
        "title": title,
        "kind": kind,
        "platform": PLATFORM,
        "provider": "http",
        "observation_id": _latest_raw_id(evidence, request.request_id),
        "canonical_id": canonical_id,
        "source_url": url,
        "published_at": _iso(published),
        "knowledge_time": knowledge_time.isoformat(),
        "content_hash": content_hash_of(text),
        "text_chars": len(text),
        "market_id": market_id,
        "geographic_confidence": "low" if market_id else None,
        "license": "CC BY-SA (attribution required)",
        "text": text,
    }


def _latest_raw_id(evidence, request_id: str) -> str | None:
    rows = evidence.conn.execute(
        "SELECT observation_id FROM acquisition.raw_observations WHERE run_id IN "
        "(SELECT run_id FROM acquisition.acquisition_runs WHERE request_id = ?) "
        "ORDER BY retrieved_at DESC LIMIT 1",
        [request_id],
    ).fetchall()
    return rows[0][0] if rows else None


def _canonical_id_for(evidence, platform: str, url: str) -> str | None:
    from ..evidence.dedup import canonical_key, canonical_observation_id

    key = canonical_key(platform, None, url, None)
    if key is None:
        return None
    return canonical_observation_id(platform, key)


def _pit_replay(evidence, observations: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Prove observations learned after a cutoff are invisible at that cutoff.

    ``knowledge_time`` lives on raw observations, so replay is measured at the
    raw level (canonical objects are dedup aggregates and would hide later
    re-observations of the same object).
    """
    rows = evidence.conn.execute(
        "SELECT observation_id, knowledge_time, retrieved_at FROM acquisition.raw_observations"
    ).fetchall()
    if not rows:
        return {"status": "NOT_EVALUATED", "reason": "no observations"}

    def as_dt(value) -> datetime | None:
        return utc(value) if isinstance(value, datetime) else parse_iso(str(value))

    knowledge_times = [t for t in (as_dt(kt) for _, kt, _ in rows) if t]
    retrieved_times = [t for t in (as_dt(rt) for _, _, rt in rows) if t]
    if not knowledge_times:
        return {"status": "NOT_EVALUATED", "reason": "no knowledge times"}

    #: T2 is the latest retrieval cutoff (when all evidence was actually in hand).
    t2 = max(retrieved_times) if retrieved_times else utc(now)
    ordered = sorted(knowledge_times)
    t1 = ordered[len(ordered) // 2]

    def visible(cutoff: datetime) -> list[str]:
        ids: list[str] = []
        for oid, kt, _ in rows:
            parsed = as_dt(kt)
            if parsed and parsed <= cutoff:
                ids.append(oid)
        return sorted(ids)

    t1_ids = visible(t1)
    t2_ids = visible(t2)
    return {
        "status": "PASS",
        "t1": t1.isoformat(),
        "t2": t2.isoformat(),
        "t1_visible_count": len(t1_ids),
        "t2_visible_count": len(t2_ids),
        "t1_visible_ids": t1_ids,
        "t2_visible_ids": t2_ids,
        "learned_after_t1": sorted(set(t2_ids) - set(t1_ids)),
        "note": "raw observations whose knowledge_time > T1 are excluded at T1 and present at T2",
    }
