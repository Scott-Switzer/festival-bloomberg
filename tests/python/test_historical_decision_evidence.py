"""Offline regressions for HISTORICAL_DECISION_EVIDENCE_ENGINE_V1.

Covers the critical invariants: provenance is mandatory, archive !=
publication, "on sale now" is a bound not an exact onsale, announcement !=
booking, wrong identity is rejected, rights fail closed, UNKNOWN is never
zero, intervals never become midpoints, structured extraction precedes LLM,
claims are append-only and conflicts coexist, and the value-of-information
acquisition priority ordering.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from festival_bloomberg.flywheel.acquisition_priority import (
    acquisition_priority,
    build_warm_start_dependency_graph,
)
from festival_bloomberg.flywheel.cutoffs import (
    CUTOFF_BOOKING_OR_OFFER,
    CUTOFF_EVENT_DATE,
    CUTOFF_GENERAL_ONSALE,
    CUTOFF_PRESALE,
    CUTOFF_TICKET_PRICE_OBSERVATION,
    KIND_ANNOUNCEMENT_UPPER_BOUND,
    derive_event_date_cutoff,
)
from festival_bloomberg.flywheel.deepseek_extractor import (
    CANDIDATE_CLAIM_SCHEMA,
    DeepSeekEvidenceExtractor,
    build_public_event_dossier,
    validate_candidate_shape,
)
from festival_bloomberg.flywheel.evidence_extraction import (
    EXTRACTOR_DATE_LANG,
    EXTRACTOR_JSONLD,
    deterministic_pass,
    extract_date_language_candidates,
    extract_jsonld_candidates,
    extract_opengraph_candidates,
)
from festival_bloomberg.flywheel.evidence_verification import (
    VERIFICATION_ACCEPTED,
    VERIFICATION_REJECTED,
    verify_candidate,
)
from festival_bloomberg.flywheel.repository import FlywheelRepository
from festival_bloomberg.research.boxscore import (
    HEADCOUNT_REPORTED_ATTENDANCE,
    BoxofficeEngagement,
)
from festival_bloomberg.research.repository import ResearchRepository


def _engagement(engagement_id: str, **overrides) -> BoxofficeEngagement:
    kwargs = dict(
        engagement_id=engagement_id,
        reporting_source="pollstar",
        artist="Zach Bryan",
        venue="United Center",
        city="Chicago",
        market="Chicago",
        start_date="2024-03-05",
        end_date="2024-03-05",
        headcount_definition=HEADCOUNT_REPORTED_ATTENDANCE,
        number_of_shows=1,
        is_multi_show=False,
        is_reported=True,
        is_estimated=False,
        headcount_total=56931.0,
        ticket_gross_total=12648557.0,
        source_url="https://news.pollstar.com/2024/03/07/x/",
    )
    kwargs.update(overrides)
    return BoxofficeEngagement.build(**kwargs)


def _candidate(**overrides) -> dict:
    base = dict(
        canonical_event_id="evt_1",
        cutoff_type=CUTOFF_GENERAL_ONSALE,
        candidate_value="2024-03-03",
        granularity="DAY",
        evidence_class="OBSERVED_DAY",
        source_document_id="doc_1",
        source_url="https://x.example/page",
        evidence_text="tickets go on sale March 3",
        evidence_span_start=0,
        evidence_span_end=29,
        extractor_kind=EXTRACTOR_DATE_LANG,
        interpretation="onsale_phrase: 'March 3'",
        rights_status="RESEARCH_ONLY",
        commercial_use_status="RESEARCH_ONLY",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Deterministic verifier (admissibility)
# ---------------------------------------------------------------------------
def test_claim_without_source_document_rejected():
    res = verify_candidate(_candidate(source_document_id=None))
    assert res["verification_status"] == VERIFICATION_REJECTED
    assert res["rejection_reason"] == "claim_without_source_document"


def test_claim_without_evidence_span_rejected():
    res = verify_candidate(_candidate(evidence_span_start=None, evidence_span_end=None))
    assert res["verification_status"] == VERIFICATION_REJECTED
    assert res["rejection_reason"] == "claim_without_evidence_span"


def test_wrong_venue_or_city_rejected():
    target = {"artist": "Zach Bryan", "venue": "United Center", "city": "Chicago",
              "market": "Chicago", "start_date": "2024-03-05"}
    wrong_venue = {"artists": ["Zach Bryan"], "venues": ["Madison Square Garden"]}
    res = verify_candidate(_candidate(), target_event=target, resolved=wrong_venue)
    assert res["verification_status"] == VERIFICATION_REJECTED
    assert res["rejection_reason"] == "wrong_artist_venue_city_or_date"
    # matching identity passes identity check
    ok = {"artists": ["Zach Bryan"], "venues": ["United Center"], "cities": ["Chicago"]}
    assert verify_candidate(_candidate(), target_event=target, resolved=ok)["verification_status"] == VERIFICATION_ACCEPTED


def test_announcement_never_becomes_booking_exact():
    res = verify_candidate(_candidate(
        cutoff_type=CUTOFF_BOOKING_OR_OFFER,
        evidence_class="OBSERVED_DAY",
        interpretation="announcement page said the show is coming",
    ))
    assert res["verification_status"] == VERIFICATION_REJECTED
    assert res["rejection_reason"] == "announcement_interpreted_as_booking_exact"
    # an explicit first-party booking date IS admissible
    ok = verify_candidate(_candidate(
        cutoff_type=CUTOFF_BOOKING_OR_OFFER,
        evidence_class="OBSERVED_DAY",
        interpretation="OBSERVED_BOOKING_DATE from promoter contract",
    ))
    assert ok["verification_status"] == VERIFICATION_ACCEPTED


def test_relative_date_without_anchor_rejected():
    res = verify_candidate(_candidate(
        cutoff_type=CUTOFF_GENERAL_ONSALE, candidate_value=None,
        lower_bound=None, upper_bound=None,
    ))
    assert res["verification_status"] == VERIFICATION_REJECTED
    assert res["rejection_reason"] == "relative_date_without_anchor"


def test_interval_never_becomes_midpoint():
    res = verify_candidate(_candidate(
        candidate_value="2024-02-01",
        lower_bound="2024-01-01", upper_bound="2024-03-01",
        cutoff_type=CUTOFF_BOOKING_OR_OFFER,
        evidence_class="OBSERVED_DAY",
        interpretation="OBSERVED_BOOKING_DATE",
    ))
    # lower != upper != candidate (no true midpoint here); the verifier only
    # rejects when lower==upper==candidate (a collapsed midpoint). Prove that.
    collapsed = verify_candidate(_candidate(
        candidate_value="2024-02-01",
        lower_bound="2024-02-01", upper_bound="2024-02-01",
        cutoff_type=CUTOFF_BOOKING_OR_OFFER,
        evidence_class="OBSERVED_DAY",
        interpretation="OBSERVED_BOOKING_DATE",
    ))
    assert collapsed["verification_status"] == VERIFICATION_REJECTED
    assert collapsed["rejection_reason"] == "interval_collapsed_to_midpoint"
    assert res["verification_status"] == VERIFICATION_ACCEPTED


def test_rights_fail_closed():
    res = verify_candidate(_candidate(), rights_status="COMMERCIAL_PROHIBITED")
    assert res["verification_status"] == VERIFICATION_REJECTED
    assert res["rejection_reason"] == "source_rights_failure"


def test_unknown_never_admissible():
    res = verify_candidate(_candidate(
        cutoff_type=CUTOFF_GENERAL_ONSALE, evidence_class="UNKNOWN",
        candidate_value="2024-03-03", interpretation="x",
    ))
    assert res["verification_status"] == VERIFICATION_REJECTED
    assert res["rejection_reason"] == "unknown_never_admissible"


# ---------------------------------------------------------------------------
# Deterministic extractors
# ---------------------------------------------------------------------------
def test_on_sale_now_is_bound_not_exact():
    pub = datetime(2024, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
    cands = extract_date_language_candidates(
        "Tickets are on sale now at the box office.",
        canonical_event_id="e", source_document_id="d", source_url="https://x",
        publication_time=pub,
    )
    onsale = [c for c in cands if c["cutoff_type"] == CUTOFF_GENERAL_ONSALE]
    assert len(onsale) == 1
    c = onsale[0]
    assert c["upper_bound"] == pub.isoformat()  # bound, not exact
    assert c["candidate_value"] is None
    assert c["evidence_class"] == "ARCHIVE_CAPTURE_UPPER_BOUND"


def test_explicit_onsale_date_preserved():
    pub = datetime(2024, 1, 15, tzinfo=timezone.utc)
    cands = extract_date_language_candidates(
        "Tickets go on sale March 3 at 10 a.m.",
        canonical_event_id="e", source_document_id="d", source_url="https://x",
        publication_time=pub,
    )
    onsale = [c for c in cands if c["cutoff_type"] == CUTOFF_GENERAL_ONSALE]
    assert onsale and onsale[0]["candidate_value"] == "2024-03-03"
    assert onsale[0]["granularity"] == "DAY"


def test_price_phrase_extracted():
    cands = extract_date_language_candidates(
        "Tickets starting at $49.50.",
        canonical_event_id="e", source_document_id="d", source_url="https://x",
    )
    prices = [c for c in cands if c["cutoff_type"] == CUTOFF_TICKET_PRICE_OBSERVATION]
    assert prices and prices[0]["candidate_value"] == "49.50"


def test_jsonld_event_extraction():
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"MusicEvent","name":"Zach Bryan","startDate":"2024-03-05",'
        '"offers":{"price":"59.50"},"onsaleStart":"2024-01-03"}</script></head></html>'
    )
    cands = extract_jsonld_candidates(
        html, canonical_event_id="e", source_document_id="d", source_url="https://x"
    )
    types = {c["cutoff_type"] for c in cands}
    assert CUTOFF_EVENT_DATE in types
    assert CUTOFF_TICKET_PRICE_OBSERVATION in types
    assert CUTOFF_GENERAL_ONSALE in types
    assert all(c["extractor_kind"] == EXTRACTOR_JSONLD for c in cands)


def test_structured_extraction_precedes_llm():
    # The deterministic pass runs JSON-LD -> OpenGraph -> date-language and
    # NEVER produces a DEEPSEEK extractor kind.
    html = (
        '<meta property="og:article:published_time" content="2024-03-07T08:00:00Z">'
        "Tickets go on sale March 3."
    )
    cands = deterministic_pass(
        html, canonical_event_id="e", source_document_id="d", source_url="https://x",
        publication_time=datetime(2024, 2, 1, tzinfo=timezone.utc),
    )
    assert cands
    assert all(c["extractor_kind"] != "DEEPSEEK_V4_PRO" for c in cands)
    kinds = {c["extractor_kind"] for c in cands}
    assert kinds <= {"DETERMINISTIC_JSONLD", "DETERMINISTIC_OPENTABLE", "DETERMINISTIC_DATE_LANG"}


# ---------------------------------------------------------------------------
# DeepSeek contract client
# ---------------------------------------------------------------------------
def test_deepseek_not_configured_without_key():
    client = DeepSeekEvidenceExtractor()
    assert client.is_configured is False
    result = client.extract_candidates({"canonical_event_id": "e"})
    assert result["status"] == "NOT_CONFIGURED"
    assert result["candidates"] == []


def test_deepseek_candidate_shape_contract():
    assert validate_candidate_shape({
        "canonical_event_id": "e", "cutoff_type": "GENERAL_ONSALE",
        "source_document_id": "d", "source_url": "https://x",
        "evidence_text": "on sale March 3", "evidence_span_start": 0,
        "evidence_span_end": 15, "granularity": "DAY",
        "evidence_class": "OBSERVED_DAY", "interpretation": "x",
        "contradiction_detected": False,
    }) is None
    assert validate_candidate_shape({"cutoff_type": "X"}) == "candidate_missing_source_document_id"
    missing_span = validate_candidate_shape({
        "canonical_event_id": "e", "cutoff_type": "GENERAL_ONSALE",
        "source_document_id": "d", "source_url": "https://x",
        "evidence_text": "x", "evidence_span_start": 0, "evidence_span_end": None,
        "granularity": "DAY", "evidence_class": "OBSERVED_DAY",
        "interpretation": "x", "contradiction_detected": False,
    })
    assert missing_span == "candidate_missing_evidence_span"
    assert "additionalProperties" in CANDIDATE_CLAIM_SCHEMA
    assert CANDIDATE_CLAIM_SCHEMA["additionalProperties"] is False


def test_dossier_excludes_private_fields():
    dossier = build_public_event_dossier({
        "engagement_id": "e1", "artist": "A", "venue": "V", "city": "C",
        "start_date": "2024-03-05", "tour": "T", "reporting_source": "pollstar",
    })
    assert dossier["artist"] == "A"
    # Private outcome/settlement fields are never keys of a public dossier.
    assert not any(
        k in dossier
        for k in ("headcount_total", "ticket_gross_total", "settlement_gross",
                  "settlement_net", "promoter_contribution")
    )


# ---------------------------------------------------------------------------
# Acquisition priority (value of information)
# ---------------------------------------------------------------------------
def test_priority_graph_prefers_downstream_leverage(tmp_path):
    import duckdb

    conn = duckdb.connect(str(tmp_path / "prio.duckdb"))
    research = ResearchRepository(conn)
    # Artist A has 4 dated shows: earliest unlocks 3 downstream targets.
    for i, d in enumerate(("2022-01-10", "2023-01-10", "2024-01-10", "2025-01-10")):
        research.insert_engagement(_engagement(f"a{i}", artist="Artist A", start_date=d))
    # Artist B: one-off (low repeat frequency, no downstream).
    research.insert_engagement(_engagement("b0", artist="Artist B", start_date="2024-06-01"))
    conn.commit()

    ranked = acquisition_priority(conn, dimension="artist")
    # Artist A's earliest show ranks first (3 downstream), then its later
    # shows, then the one-off Artist B last (0 downstream, repeat 1).
    assert ranked[0]["artist"] == "Artist A"
    assert ranked[0]["start_date"] == "2022-01-10"
    assert ranked[0]["downstream_targets"] == 3
    assert ranked[0]["repeat_frequency"] == 4
    assert ranked[-1]["artist"] == "Artist B"
    assert ranked[-1]["downstream_targets"] == 0
    assert ranked[0]["rank"] == 1

    graph = build_warm_start_dependency_graph(conn)
    assert graph["targets_total"] == 5
    # only the 2025 show has >= 3 potential priors (all still unknown) -> locked.
    assert graph["warm_start_locked"] == 1
    conn.close()


# ---------------------------------------------------------------------------
# Claim support graph persistence
# ---------------------------------------------------------------------------
def test_claims_append_only_and_conflicts_coexist(tmp_path):
    import duckdb

    conn = duckdb.connect(str(tmp_path / "claims.duckdb"))
    flywheel = FlywheelRepository(conn)
    doc = {
        "document_id": "doc_1", "canonical_event_id": "evt", "source_url": "https://x",
        "document_content_hash": "hash1", "retrieved_at": datetime(2026, 8, 15).isoformat(),
        "rights_status": "RESEARCH_ONLY", "commercial_use_status": "RESEARCH_ONLY",
        "knowledge_time": datetime(2026, 8, 15).isoformat(),
    }
    assert flywheel.insert_evidence_document(doc) is True
    assert flywheel.insert_evidence_document(doc) is False  # content-addressed

    def claim(claim_id, value):
        return {
            "claim_id": claim_id, "canonical_event_id": "evt",
            "cutoff_type": CUTOFF_GENERAL_ONSALE, "candidate_value": value,
            "granularity": "DAY", "evidence_class": "OBSERVED_DAY",
            "source_document_id": "doc_1", "source_url": "https://x",
            "retrieved_at": datetime(2026, 8, 15).isoformat(),
            "knowledge_time": datetime(2026, 8, 15).isoformat(),
            "evidence_span_start": 0, "evidence_span_end": 5, "evidence_span_hash": "h",
            "extractor_kind": EXTRACTOR_DATE_LANG,
            "rights_status": "RESEARCH_ONLY", "commercial_use_status": "RESEARCH_ONLY",
            "verification_status": "ACCEPTED",
        }

    assert flywheel.insert_evidence_claim(claim("c1", "2024-03-03")) is True
    assert flywheel.insert_evidence_claim(claim("c1", "2024-03-03")) is False  # append-only
    # conflicting claims for the same (event, cutoff) coexist.
    assert flywheel.insert_evidence_claim(claim("c2", "2024-03-05")) is True
    rows = flywheel.query_evidence_claims(verification_status="ACCEPTED")
    assert len(rows) == 2
    assert {r["candidate_value"] for r in rows} == {"2024-03-03", "2024-03-05"}
    conn.close()


# ---------------------------------------------------------------------------
# Live OA driver (hermetic)
# ---------------------------------------------------------------------------
def test_historical_decision_evidence_oa_end_to_end(tmp_path):
    import duckdb

    from festival_bloomberg.oa.historical_decision_evidence import (
        run_historical_decision_evidence_oa,
    )

    research_path = tmp_path / "corpus.duckdb"
    conn = duckdb.connect(str(research_path))
    research = ResearchRepository(conn)
    for i, d in enumerate(("2022-01-10", "2023-01-10", "2024-01-10", "2025-01-10")):
        research.insert_engagement(_engagement(f"e{i}", artist="Artist A", start_date=d))
    conn.close()

    manifest = run_historical_decision_evidence_oa(
        research_db=str(research_path),
        report_path=str(tmp_path / "hdee_manifest.json"),
        top_n=10,
    )

    assert manifest["software_version"] == "historical_decision_evidence_engine_v1"
    g = manifest["acquisition_priority_graph"]
    assert g["targets_total"] == 4
    # the 2025 show has 3 potential priors (all unknown) -> the one locked target.
    assert g["warm_start_locked_targets"] == 1
    assert len(g["priority_head"]) == 4
    assert g["priority_head"][0]["artist"] == "Artist A"
    assert g["priority_head"][0]["event_date"] == "2022-01-10"
    assert manifest["deepseek_extractor"]["status"] == "NOT_CONFIGURED"
    assert manifest["deterministic_extraction"]["claims_accepted"] == 0
    assert (tmp_path / "hdee_manifest.json").is_file()
