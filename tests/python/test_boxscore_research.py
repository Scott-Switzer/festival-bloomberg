"""Offline regressions for Public Boxscore Research Corpus V1.

Covers the BOXOFFICE_ENGAGEMENT parsers (Billboard / Pollstar / Touring Data)
and the promotion path into the outcome claim ledger. The core invariants:

* multi-show aggregates are NEVER divided across nights, and never promoted
  to event-level claims
* estimated Touring Data rows are NEVER promoted as observations
* Pollstar "Tickets Sold" is PAID tickets (per Pollstar policy) and promotes
  to PAID_TICKETS, never the broader TICKETS_SOLD
* headcount definitions are preserved (REPORTED_ATTENDANCE vs PAID_TICKETS)
* every promoted claim carries RESEARCH_ONLY / TERMS_REVIEW_REQUIRED rights;
  the commercial-eligible corpus is always zero (fail closed)
* engagement insert is idempotent (append-only, no overwrite)

All offline: no network, no paid calls.
"""

from __future__ import annotations

import pytest

from festival_bloomberg.economics.repository import EconomicsRepository
from festival_bloomberg.research.acquisition import corpus_report
from festival_bloomberg.research.boxscore import (
    HEADCOUNT_PAID_TICKETS,
    HEADCOUNT_REPORTED_ATTENDANCE,
    BoxofficeEngagement,
    _dates_from_raw,
    html_to_text_lines,
    parse_billboard_boxscore_html,
    parse_pollstar_hot_tickets,
    parse_touring_data,
)
from festival_bloomberg.research.repository import ResearchRepository
from festival_bloomberg.warehouse.repository import FestivalRepository


def _repos(tmp_path, name="boxscore.duckdb"):
    repo = FestivalRepository(str(tmp_path / name))
    return repo, ResearchRepository(repo.conn), EconomicsRepository(repo.conn)


BILLBOARD_HTML = """
<table>
<tr><th>Rank</th><th>Artist/Event</th><th>Venue</th><th>City/State</th><th>Event Dates</th><th>Gross Sales</th><th>Attend/Capacity</th><th>Shows/Sellouts</th><th>Prices</th><th>Promoters</th></tr>
<tr><td>1</td><td>Taylor Swift</td><td>United Center</td><td>Chicago, IL</td><td>Oct. 26, 2013</td><td>$1,234,567</td><td>15,000 / 15,000</td><td>1/1</td><td>$49.50, $199.50</td><td>AEG Live</td></tr>
<tr><td>2</td><td>Fleetwood Mac</td><td>Madison Square Garden</td><td>New York, NY</td><td>Oct. 27-28, 2013</td><td>$2,500,000</td><td>30,000 / 30,000</td><td>2/2</td><td>$75-$250</td><td>Live Nation</td></tr>
</table>
"""

POLLSTAR_TEXT = """
10,001 - 15,000 Capacity
1) Taylor Swift
Tickets Sold: 15,000; Venue: United Center, Chicago; Gross: $1,234,567; Ticket Range: $49.50 - $199.50; Promoter: AEG Live; Dates: Oct. 26, 2013; No. of Shows: 1
"""

TOURING_TEXT = (
    "October 22, 2019: United Center, Chicago, IL (15,000 \u2013 $2,000,000)\n"
    "Total Estimated Gross: $40,000,000\n"
    "October 23, 2019: United Center, Chicago, IL (14,500 \u2013 $1,900,000)\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _engagement(**overrides) -> BoxofficeEngagement:
    kwargs = dict(
        reporting_source="billboard",
        artist="Test Artist",
        headcount_definition=HEADCOUNT_REPORTED_ATTENDANCE,
    )
    kwargs.update(overrides)
    return BoxofficeEngagement.build(**kwargs)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def test_html_to_text_lines_preserves_blocks_and_unescapes():
    html = "<p>1) Artist</p><br />Tickets Sold: 15,000 &#8211; done"
    text = html_to_text_lines(html)
    assert "1) Artist" in text
    assert "Tickets Sold: 15,000" in text
    assert "\u2013" in text  # HTML entity decoded


def test_dates_from_raw_single_vs_range():
    start, end, shows = _dates_from_raw("Oct. 26, 2013")
    assert start == end == "2013-10-26"
    assert shows == 1
    # a range is never divided into per-night dates
    start, end, shows = _dates_from_raw("Oct. 27-28, 2013")
    assert start is None
    assert end is None


# ---------------------------------------------------------------------------
# Billboard
# ---------------------------------------------------------------------------
def test_billboard_parser_single_and_multi_show():
    engagements = parse_billboard_boxscore_html(BILLBOARD_HTML, source_url="https://example.com/boxscore")
    assert len(engagements) == 2

    single = engagements[0]
    assert single.rank == 1
    assert single.artist == "Taylor Swift"
    assert single.venue == "United Center"
    assert single.city == "Chicago"
    assert single.state == "IL"
    assert single.headcount_definition == HEADCOUNT_REPORTED_ATTENDANCE
    assert single.headcount_total == 15000.0
    assert single.capacity_total == 15000.0
    assert single.ticket_gross_total == 1234567.0
    assert single.price_min == 49.5
    assert single.price_max == 199.5
    assert single.number_of_shows == 1
    assert single.reported_sellouts == 1
    assert single.is_multi_show is False
    assert single.start_date == "2013-10-26"
    assert single.rights_status == "RESEARCH_ONLY"
    assert single.commercial_use_status == "RESEARCH_ONLY"

    multi = engagements[1]
    assert multi.number_of_shows == 2
    assert multi.is_multi_show is True
    # a multi-show aggregate is not attributed to any single night
    assert multi.start_date is None


# ---------------------------------------------------------------------------
# Pollstar
# ---------------------------------------------------------------------------
def test_pollstar_tickets_sold_is_paid_tickets():
    engagements = parse_pollstar_hot_tickets(POLLSTAR_TEXT, source_url="https://example.com/hot-tickets")
    assert len(engagements) == 1
    e = engagements[0]
    assert e.artist == "Taylor Swift"
    assert e.headcount_definition == HEADCOUNT_PAID_TICKETS
    assert e.headcount_total == 15000.0
    assert e.ticket_gross_total == 1234567.0
    assert e.price_min == 49.5
    assert e.price_max == 199.5
    assert e.venue == "United Center"
    assert e.city == "Chicago"
    assert e.capacity_tier == "10,001 - 15,000 Capacity"
    assert e.number_of_shows == 1
    assert e.is_multi_show is False


# ---------------------------------------------------------------------------
# Touring Data
# ---------------------------------------------------------------------------
def test_touring_data_reported_vs_estimated():
    engagements = parse_touring_data(TOURING_TEXT, source_url="https://touringdata.org/tour", artist="Post Malone")
    assert len(engagements) == 2

    reported = engagements[0]
    assert reported.artist == "Post Malone"
    assert reported.is_reported is True
    assert reported.is_estimated is False
    assert reported.headcount_definition == HEADCOUNT_REPORTED_ATTENDANCE
    assert reported.headcount_total == 15000.0
    assert reported.ticket_gross_total == 2000000.0
    assert reported.start_date == "2019-10-22"

    estimated = engagements[1]
    assert estimated.is_reported is False
    assert estimated.is_estimated is True


# ---------------------------------------------------------------------------
# Promotion semantics
# ---------------------------------------------------------------------------
def test_multi_show_engagement_is_never_promoted(tmp_path):
    repo, research, econ = _repos(tmp_path)
    try:
        assert research.insert_engagement(_engagement(
            engagement_id="eng_multi",
            number_of_shows=2,
            headcount_total=30000.0,
            ticket_gross_total=2500000.0,
            is_multi_show=True,
        ))
        result = research.promote_single_show_engagements(econ)
        assert result["skipped_multi_show"] == 1
        assert result["claims_promoted"] == 0
        assert econ.query_outcome_claims() == []
    finally:
        repo.close()


def test_estimated_engagement_is_never_promoted(tmp_path):
    repo, research, econ = _repos(tmp_path)
    try:
        assert research.insert_engagement(_engagement(
            engagement_id="eng_est",
            reporting_source="touring_data",
            number_of_shows=1,
            headcount_total=14500.0,
            is_reported=False,
            is_estimated=True,
        ))
        result = research.promote_single_show_engagements(econ)
        assert result["skipped_estimated_or_unreported"] == 1
        assert result["claims_promoted"] == 0
        assert econ.query_outcome_claims() == []
    finally:
        repo.close()


def test_pollstar_promotes_paid_tickets_not_tickets_sold(tmp_path):
    repo, research, econ = _repos(tmp_path)
    try:
        assert research.insert_engagement(_engagement(
            engagement_id="eng_pollstar",
            reporting_source="pollstar",
            artist="Taylor Swift",
            headcount_definition=HEADCOUNT_PAID_TICKETS,
            number_of_shows=1,
            headcount_total=15000.0,
        ))
        result = research.promote_single_show_engagements(econ)
        assert result["claims_promoted"] == 1
        types = {c["outcome_type"] for c in econ.query_outcome_claims()}
        assert "PAID_TICKETS" in types
        assert "TICKETS_SOLD" not in types
    finally:
        repo.close()


def test_promoted_claims_are_research_only(tmp_path):
    repo, research, econ = _repos(tmp_path)
    try:
        assert research.insert_engagement(_engagement(
            engagement_id="eng_bb",
            number_of_shows=1,
            headcount_total=15000.0,
            ticket_gross_total=1234567.0,
            price_min=49.5,
            price_max=199.5,
            reported_sellouts=1,
        ))
        research.promote_single_show_engagements(econ)
        claims = econ.query_outcome_claims()
        assert claims
        for c in claims:
            assert c["rights_status"] in {"RESEARCH_ONLY", "TERMS_REVIEW_REQUIRED"}
            assert c["commercial_use_status"] in {"RESEARCH_ONLY", "TERMS_REVIEW_REQUIRED"}
            assert c["commercial_use_status"] != "OPEN_COMMERCIAL_OK"
        report = corpus_report(research)
        assert report["commercial_eligible_corpus"] == 0
        assert report["rights_verdict"] == "FAIL_CLOSED"
    finally:
        repo.close()


def test_insert_engagement_is_idempotent(tmp_path):
    repo, research, econ = _repos(tmp_path)
    try:
        engagement = _engagement(engagement_id="eng_idem", number_of_shows=1, headcount_total=1000.0)
        assert research.insert_engagement(engagement) is True
        assert research.insert_engagement(engagement) is False  # no duplicate
        assert len(research.query_engagements()) == 1
    finally:
        repo.close()


def test_promotion_is_idempotent(tmp_path):
    repo, research, econ = _repos(tmp_path)
    try:
        assert research.insert_engagement(_engagement(
            engagement_id="eng_idem2", number_of_shows=1, headcount_total=1000.0,
        ))
        first = research.promote_single_show_engagements(econ)
        second = research.promote_single_show_engagements(econ)
        assert first["claims_promoted"] == 1
        assert second["claims_promoted"] == 0  # append-only, no duplicate claims
        assert len(econ.query_outcome_claims()) == 1
    finally:
        repo.close()


def test_single_show_billboard_promotes_full_claim_set(tmp_path):
    repo, research, econ = _repos(tmp_path)
    try:
        assert research.insert_engagement(_engagement(
            engagement_id="eng_full",
            number_of_shows=1,
            headcount_total=15000.0,
            ticket_gross_total=1234567.0,
            price_min=49.5,
            price_max=199.5,
            reported_sellouts=1,
        ))
        research.promote_single_show_engagements(econ)
        types = {c["outcome_type"] for c in econ.query_outcome_claims()}
        assert types == {
            "REPORTED_ATTENDANCE",
            "TICKET_GROSS",
            "PRIMARY_FACE_VALUE_MIN",
            "PRIMARY_FACE_VALUE_MAX",
            "EXPLICIT_SOLD_OUT_ASSERTION",
        }
    finally:
        repo.close()
