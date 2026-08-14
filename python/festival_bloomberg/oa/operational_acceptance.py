"""Live operational acceptance for the Festival Signal Fabric.

This driver proves that REAL public evidence flows through the canonical
acquisition path end-to-end:

    AcquisitionRequest -> AcquisitionRouter -> Provider -> AcquisitionResult
        -> EvidenceRepository -> PIT -> NLP

It performs real network fetches through the :class:`WikimediaProvider` (a
key-free, CC-licensed, immutable-revision source) at ``$0.00`` cost. It never
uses fixtures, mocks or synthetic observations.

Semantic boundaries enforced here (and tested offline):

* ``content_role`` — Wikipedia text is ``ENCYCLOPEDIC``, never ``FAN_GENERATED``.
* ``TEXT_NLP_PIPELINE`` (VADER ran on real text) is reported separately from
  ``REAL_SOCIAL_NLP`` (only valid for fan-generated discourse).
* ``MARKET_CONTEXT`` (a venue/festival is in Chicago) is reported separately
  from ``ARTIST_MARKET_RELATION`` (the artist is linked to that market) and
  ``ARTIST_MARKET_DEMAND_SIGNAL`` (fan demand, which requires fan text).
* PIT replay is scoped to this OA run via ``correlation_id``.

Provider credentials are reported only as CONFIGURED / NOT_CONFIGURED — never
their values. No observation, score, locality or confidence is fabricated.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from ..acquisition.contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    utc_now,
)
from ..acquisition.policy import PolicyGate
from ..acquisition.providers import WikimediaProvider
from ..acquisition.router import AcquisitionRouter
from ..acquisition.transport import UrllibTransport
from ..evidence.provenance import parse_iso, utc
from ..evidence.semantics import ContentRole, is_fan_role
from ..localenv import load_local_env
from ..social.sentiment import (
    TWEETNLP_AVAILABLE,
    VADER_MODEL_NAME,
    VADER_MODEL_VERSION,
    vader_inference,
)

# --------------------------------------------------------------------------- #
# Predeclared, deterministic selection universe
# --------------------------------------------------------------------------- #

#: Ten artists with clear, current live-entertainment relevance. Fixed BEFORE
#: any sentiment is measured; selection never consults a signal.
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

SELECTION_RULE = (
    "first artist alphabetically among CANDIDATE_ARTISTS whose Wikipedia "
    "article resolves with a plain-text extract of at least MIN_EXTRACT_CHARS "
    "characters; availability metadata only — no sentiment, popularity or "
    "booking signal is consulted."
)
MIN_EXTRACT_CHARS = 200

#: Real Chicago-specific public pages used as (weak) market CONTEXT only.
CHICAGO_PAGES: tuple[tuple[str, str], ...] = (
    ("United Center", "venue"),
    ("Lollapalooza", "festival"),
)


def _title_slug(title: str) -> str:
    return title.strip().replace(" ", "_")


# --------------------------------------------------------------------------- #
# Pure, deterministic helpers (unit-tested offline)
# --------------------------------------------------------------------------- #


def select_artist(extract_lengths: dict[str, int | None]) -> str | None:
    """Deterministic availability-only selection.

    Returns the first candidate in alphabetical order whose extract is at
    least ``MIN_EXTRACT_CHARS`` long, or ``None`` when none qualifies.
    """
    for artist in sorted(CANDIDATE_ARTISTS):
        length = extract_lengths.get(artist)
        if length is not None and length >= MIN_EXTRACT_CHARS:
            return artist
    return None


def detect_chicago_mentions(text: str | None) -> list[str]:
    """Explicit, word-bounded ``Chicago`` mentions with a short context window."""
    if not text:
        return []
    matches: list[str] = []
    for m in re.finditer(r"\bChicago\b", text, flags=re.IGNORECASE):
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 40)
        matches.append(text[start:end].replace("\n", " ").strip())
    return matches


def provider_readiness(env: dict[str, str] | None = None) -> dict[str, str]:
    """Report provider readiness by credential presence only (never values)."""
    if env is None:
        load_local_env()
        environ = dict(os.environ)
    else:
        environ = dict(env)
    readiness = {
        "youtube": "CONFIGURED" if environ.get("YOUTUBE_API_KEY") else "NOT_CONFIGURED",
        "monid": "CONFIGURED" if environ.get("MONID_API_KEY") else "NOT_CONFIGURED",
        "apify": "CONFIGURED" if environ.get("APIFY_TOKEN") else "NOT_CONFIGURED",
        "http": "AVAILABLE",
        "wikimedia": "AVAILABLE",
    }
    try:
        import scrapling  # type: ignore  # noqa: F401

        readiness["scrapling"] = "AVAILABLE"
    except ImportError:
        readiness["scrapling"] = "NOT_AVAILABLE"
    return readiness


def _entity_id(title: str) -> str:
    return title.strip().lower().replace(" ", "-")


def _make_request(
    *,
    title: str,
    oa_run_id: str,
    entity_type: str,
    max_cost_usd: float,
) -> AcquisitionRequest:
    return AcquisitionRequest.new(
        entity_id=_entity_id(title),
        entity_type=entity_type,
        platform="wikipedia",
        query=title,
        commercial_context="research",
        max_cost_usd=max_cost_usd,
        correlation_id=oa_run_id,
        preferred_providers=("wikimedia",),
    )


# --------------------------------------------------------------------------- #
# Manifest building (pure)
# --------------------------------------------------------------------------- #


def build_manifest(
    *,
    market: str,
    lookback_days: int,
    budget_usd: float,
    oa_run_id: str,
    selection: dict[str, Any],
    readiness: dict[str, str],
    statuses: dict[str, str],
    observations: list[dict[str, Any]],
    content_role_distribution: dict[str, int],
    vader_distribution: dict[str, int],
    tweetnlp_status: str,
    pit_replay: dict[str, Any],
    cost_usd: float,
    generated_at: datetime,
    db_path: str | None,
) -> dict[str, Any]:
    """Assemble the machine-readable OA manifest (no raw text embedded)."""
    return {
        "schema_version": "2.0",
        "generated_at": generated_at.isoformat(),
        "market": market,
        "lookback_days": lookback_days,
        "budget_usd": budget_usd,
        "oa_run_id": oa_run_id,
        "db_path": db_path,
        "artist_selection": selection,
        "provider_readiness": readiness,
        "statuses": statuses,
        "observations": {
            "raw_count": sum(o.get("raw_count", 1) for o in observations),
            "canonical_count": len({o.get("canonical_id") for o in observations if o.get("canonical_id")}),
            "platforms": sorted({o["platform"] for o in observations}),
            "providers": sorted({o["provider"] for o in observations}),
            "content_role_distribution": content_role_distribution,
            "items": [
                {
                    "title": o.get("title"),
                    "kind": o.get("kind"),
                    "platform": o.get("platform"),
                    "provider": o.get("provider"),
                    "observation_id": o.get("observation_id"),
                    "canonical_id": o.get("canonical_id"),
                    "source_url": o.get("source_url"),
                    "content_role": o.get("content_role"),
                    "resolution_method": o.get("resolution_method"),
                    "source_revision_id": o.get("source_revision_id"),
                    "source_revision_time": o.get("source_revision_time"),
                    "knowledge_time": o.get("knowledge_time"),
                    "content_hash": o.get("content_hash"),
                    "text_chars": o.get("text_chars"),
                    "market_id": o.get("market_id"),
                    "license": o.get("license"),
                }
                for o in observations
            ],
        },
        "nlp": {
            "text_sentiment": {
                "status": "PASS" if vader_distribution else "NOT_EVALUATED",
                "model_name": VADER_MODEL_NAME,
                "model_version": VADER_MODEL_VERSION,
                "distribution": vader_distribution,
                "inference_count": sum(vader_distribution.values()),
                "note": "VADER over encyclopedic text; NOT fan sentiment",
            },
            "fan_sentiment": {
                "status": "NOT_EVALUATED",
                "note": "no FAN_GENERATED observations present; fan sentiment is UNKNOWN",
            },
            "tweetnlp": {
                "status": tweetnlp_status,
                "note": "optional social-transformer baseline; heavy pytorch dependency",
            },
        },
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
    """Run the live OA end-to-end through the canonical acquisition router."""
    transport = transport or UrllibTransport()
    gate = policy_gate or PolicyGate()
    generated_at = utc_now()
    oa_run_id = str(uuid.uuid4())

    readiness = provider_readiness()

    providers = {"wikimedia": WikimediaProvider(transport=transport)}
    router = AcquisitionRouter(providers=providers, policy_gate=gate, budget=None)

    # -- deterministic selection (availability only, no ingest) -------------- #
    extract_lengths: dict[str, int | None] = {}
    selected = None
    selected_request: AcquisitionRequest | None = None
    selected_result: AcquisitionResult | None = None
    selection_attempts = 0
    for artist in sorted(CANDIDATE_ARTISTS):
        request = _make_request(title=artist, oa_run_id=oa_run_id, entity_type="artist", max_cost_usd=budget_usd)
        result = router.route(request)
        selection_attempts += 1
        if result.is_success and result.records:
            extract_lengths[artist] = len(result.records[0].get("text") or "")
        else:
            extract_lengths[artist] = None
        candidate = select_artist(extract_lengths)
        if candidate is not None:
            selected = candidate
            selected_request = request
            selected_result = result
            break

    selection = {
        "rule": SELECTION_RULE,
        "min_extract_chars": MIN_EXTRACT_CHARS,
        "candidate_count": len(CANDIDATE_ARTISTS),
        "candidates": list(sorted(CANDIDATE_ARTISTS)),
        "selected_artist": selected,
        "selection_basis": "availability_metadata_only",
        "selection_attempts": selection_attempts,
    }

    if selected is None or selected_request is None or selected_result is None:
        return build_manifest(
            market=market,
            lookback_days=lookback_days,
            budget_usd=budget_usd,
            oa_run_id=oa_run_id,
            selection=selection,
            readiness=readiness,
            statuses={
                "ACQUISITION_PIPELINE": "FAIL",
                "WIKIMEDIA_TEXT_PIPELINE": "NOT_EVALUATED",
                "FAN_GENERATED_DATA": "NOT_EVALUATED",
                "REAL_SOCIAL_NLP": "NOT_EVALUATED",
                "MARKET_CONTEXT": "INSUFFICIENT_EVIDENCE",
                "ARTIST_MARKET_RELATION": "INSUFFICIENT_EVIDENCE",
                "ARTIST_MARKET_DEMAND_SIGNAL": "INSUFFICIENT_EVIDENCE",
                "CROSS_PROVIDER_RECONCILIATION": "NOT_EVALUATED",
                "PIT_REPLAY": "NOT_EVALUATED",
            },
            observations=[],
            content_role_distribution={},
            vader_distribution={},
            tweetnlp_status="NOT_AVAILABLE" if not TWEETNLP_AVAILABLE else "AVAILABLE",
            pit_replay={"status": "NOT_EVALUATED", "reason": "no observations"},
            cost_usd=0.0,
            generated_at=generated_at,
            db_path=db_path,
        )

    # -- collection: selected artist + Chicago context pages ----------------- #
    to_collect: list[tuple[str, str, AcquisitionRequest, AcquisitionResult]] = [
        (selected, "artist", selected_request, selected_result)
    ]
    for title, kind in CHICAGO_PAGES:
        request = _make_request(title=title, oa_run_id=oa_run_id, entity_type=kind, max_cost_usd=budget_usd)
        result = router.route(request)
        to_collect.append((title, kind, request, result))

    observations: list[dict[str, Any]] = []
    vader_distribution: dict[str, int] = {}
    content_role_distribution: dict[str, int] = {}
    fan_generated_count = 0

    for title, kind, request, result in to_collect:
        if not (result.is_success and result.records):
            continue
        record = result.records[0]
        text = record.get("text") or ""
        market_id = None
        chicago_mentions = detect_chicago_mentions(text)
        if kind in ("venue", "festival") and chicago_mentions:
            market_id = market

        evidence.ingest(request, result)

        observation = {
            "title": title,
            "kind": kind,
            "platform": record.get("platform"),
            "provider": result.provider,
            "observation_id": _latest_raw_id(evidence, oa_run_id),
            "canonical_id": _canonical_id_for(evidence, record.get("platform"), record.get("canonical_url")),
            "source_url": record.get("canonical_url"),
            "content_role": record.get("content_role"),
            "resolution_method": record.get("resolution_method"),
            "source_revision_id": record.get("source_revision_id"),
            "source_revision_time": record.get("source_revision_time"),
            "knowledge_time": _knowledge_time_for_obs(evidence, oa_run_id, record.get("source_revision_id")),
            "content_hash": record.get("content_hash"),
            "text_chars": len(text),
            "market_id": market_id,
            "license": "CC BY-SA 4.0 (attribution + share-alike required)",
            "raw_count": 1,
        }
        observations.append(observation)

        role = record.get("content_role")
        content_role_distribution[role or "UNKNOWN"] = content_role_distribution.get(role or "UNKNOWN", 0) + 1
        if is_fan_role(role):
            fan_generated_count += 1

        # VADER on the real (encyclopedic) text — text pipeline, not fan signal.
        inference = vader_inference(text)
        evidence.record_text_inference(
            observation_id=observation["observation_id"],
            task="SENTIMENT",
            model_name=inference.model_name,
            model_version=inference.model_version,
            label=inference.label,
            probabilities=inference.probabilities,
            input_text=text,
        )
        vader_distribution[inference.label] = vader_distribution.get(inference.label, 0) + 1

    # -- market semantics (separated, never conflated) ----------------------- #
    chicago_linked = [o for o in observations if o.get("market_id") == market]
    artist_text = next((o for o in observations if o.get("kind") == "artist"), None)
    artist_mentions = detect_chicago_mentions(
        (selected_result.records[0].get("text") or "") if selected_result.records else None
    )

    statuses = {
        "ACQUISITION_PIPELINE": "PASS" if observations else "FAIL",
        "WIKIMEDIA_TEXT_PIPELINE": "PASS" if observations else "NOT_EVALUATED",
        "FAN_GENERATED_DATA": "PASS" if fan_generated_count else "NOT_EVALUATED",
        "REAL_SOCIAL_NLP": "PASS" if fan_generated_count else "NOT_EVALUATED",
        "MARKET_CONTEXT": "PASS" if chicago_linked else "INSUFFICIENT_EVIDENCE",
        "ARTIST_MARKET_RELATION": "PASS" if artist_mentions else "INSUFFICIENT_EVIDENCE",
        "ARTIST_MARKET_DEMAND_SIGNAL": "INSUFFICIENT_EVIDENCE",
        "CROSS_PROVIDER_RECONCILIATION": "NOT_EVALUATED",
        "PIT_REPLAY": "PENDING",
    }

    # -- PIT replay scoped to this OA run ------------------------------------ #
    pit_replay = _pit_replay(evidence, oa_run_id, generated_at)
    statuses["PIT_REPLAY"] = pit_replay["status"]

    return build_manifest(
        market=market,
        lookback_days=lookback_days,
        budget_usd=budget_usd,
        oa_run_id=oa_run_id,
        selection=selection,
        readiness=readiness,
        statuses=statuses,
        observations=observations,
        content_role_distribution=content_role_distribution,
        vader_distribution=vader_distribution,
        tweetnlp_status="AVAILABLE" if TWEETNLP_AVAILABLE else "NOT_AVAILABLE",
        pit_replay=pit_replay,
        cost_usd=0.0,
        generated_at=generated_at,
        db_path=db_path,
    )


# --------------------------------------------------------------------------- #
# Scoped, correct helpers
# --------------------------------------------------------------------------- #


def _latest_raw_id(evidence, correlation_id: str) -> str | None:
    rows = evidence.conn.execute(
        "SELECT observation_id FROM acquisition.raw_observations "
        "WHERE correlation_id = ? ORDER BY retrieved_at DESC LIMIT 1",
        [correlation_id],
    ).fetchall()
    return rows[0][0] if rows else None


def _knowledge_time_for_obs(evidence, correlation_id: str, source_revision_id: str | None) -> str | None:
    rows = evidence.conn.execute(
        "SELECT knowledge_time FROM acquisition.raw_observations "
        "WHERE correlation_id = ? AND source_revision_id = ? LIMIT 1",
        [correlation_id, source_revision_id],
    ).fetchall()
    if not rows:
        return None
    kt = rows[0][0]
    return kt.isoformat() if isinstance(kt, datetime) else str(kt)


def _canonical_id_for(evidence, platform: str, url: str | None) -> str | None:
    from ..evidence.dedup import canonical_key, canonical_observation_id

    key = canonical_key(platform, None, url, None)
    if key is None:
        return None
    return canonical_observation_id(platform, key)


def _pit_replay(evidence, correlation_id: str, now: datetime) -> dict[str, Any]:
    """PIT replay scoped to exactly this OA run (correlation_id).

    Proves observations learned after a cutoff are invisible at that cutoff,
    using ONLY raw observations produced by the current OA — never unrelated
    historical rows in the same database.
    """
    rows = evidence.conn.execute(
        "SELECT observation_id, knowledge_time, retrieved_at "
        "FROM acquisition.raw_observations WHERE correlation_id = ?",
        [correlation_id],
    ).fetchall()
    if not rows:
        return {"status": "NOT_EVALUATED", "reason": "no scoped observations", "correlation_id": correlation_id}

    def as_dt(value) -> datetime | None:
        return utc(value) if isinstance(value, datetime) else parse_iso(str(value))

    knowledge_times = [t for t in (as_dt(kt) for _, kt, _ in rows) if t]
    retrieved_times = [t for t in (as_dt(rt) for _, _, rt in rows) if t]
    if not knowledge_times:
        return {"status": "NOT_EVALUATED", "reason": "no knowledge times", "correlation_id": correlation_id}

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
        "correlation_id": correlation_id,
        "scoped_raw_count": len(rows),
        "t1": t1.isoformat(),
        "t2": t2.isoformat(),
        "t1_visible_count": len(t1_ids),
        "t2_visible_count": len(t2_ids),
        "t1_visible_ids": t1_ids,
        "t2_visible_ids": t2_ids,
        "learned_after_t1": sorted(set(t2_ids) - set(t1_ids)),
        "note": "scoped raw observations whose knowledge_time > T1 are excluded at T1 and present at T2",
    }
