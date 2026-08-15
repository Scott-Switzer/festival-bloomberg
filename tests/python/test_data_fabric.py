"""Offline regressions for LIVE_ENTERTAINMENT_DATA_FABRIC_V1.

Covers the GDELT news provider (metadata-only, 429 fail-closed), the
Wikimedia pageviews attention collector (missing != zero), the news-mention
tape derivation, and the Ticketmaster partition manifest (COMPLETE vs
truncated is honest). No network, no paid calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb
import pytest

from festival_bloomberg.acquisition.contracts import AcquisitionRequest
from festival_bloomberg.acquisition.providers.gdelt import GdeltProvider
from festival_bloomberg.attention.wikimedia_pageviews import collect_artist_pageviews
from festival_bloomberg.intelligence.providers import OPERATIONAL, provider_statuses
from festival_bloomberg.intelligence.readmodels import get_attention_series, get_news, get_recent_news
from festival_bloomberg.intelligence.tape import derive_news_tape_entries, insert_tape_entries
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.oa.data_fabric import _persist_news_mention, _persist_partition


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "fabric.duckdb"))
    apply_pending_migrations(c)
    yield c
    c.close()


def _gdelt_request(query="Taylor Swift"):
    return AcquisitionRequest.new(
        entity_id="name::taylor swift", entity_type="artist",
        platform="gdelt", query=f'"{query}"', max_records=25,
        commercial_context="research",
    )


ARTLIST_PAYLOAD = {
    "articles": [
        {
            "url": "https://example.com/story/1",
            "title": "Taylor Swift announces tour",
            "domain": "example.com",
            "seendate": "20260815T120000Z",
            "language": "English",
            "sourcecountry": "United States",
        },
        {
            "url": "https://example.com/story/2",
            "title": "Festival lineup revealed",
            "domain": "example.com",
            "seendate": "20260814T080000Z",
            "language": "English",
            "sourcecountry": "United States",
        },
    ]
}


# ---------------------------------------------------------------------------
# GDELT provider
# ---------------------------------------------------------------------------
def test_gdelt_parses_artlist_metadata_only(monkeypatch):
    transport = _FakeTransport([ARTLIST_PAYLOAD])
    provider = GdeltProvider(transport=transport, min_interval_seconds=0)
    result = provider.acquire(_gdelt_request())
    assert result.status.value == "SUCCESS"
    assert result.record_count == 2
    rec = result.records[0]
    assert rec["article_url"] == "https://example.com/story/1"
    assert rec["title"] == "Taylor Swift announces tour"
    assert rec["published_at"] == "2026-08-15T12:00:00"
    assert rec["content_role"] == "news_metadata"
    # Metadata-only: no article body / text field is ever carried.
    assert "body" not in rec and "text" not in rec and "content" not in rec


def test_gdelt_rate_limited_is_rate_limited(monkeypatch):
    transport = _FakeTransport([(429, b"Please limit requests to one every 5 seconds")])
    provider = GdeltProvider(transport=transport, min_interval_seconds=0)
    result = provider.acquire(_gdelt_request())
    assert result.status.value == "RATE_LIMITED"
    assert result.error_category == "rate_limited"


def test_gdelt_empty_query_is_schema_invalid(monkeypatch):
    provider = GdeltProvider(transport=_FakeTransport([]), min_interval_seconds=0)
    req = AcquisitionRequest.new(
        entity_id="x", entity_type="artist", platform="gdelt",
        query="   ", commercial_context="research",
    )
    result = provider.acquire(req)
    assert result.status.value == "SCHEMA_INVALID"


# ---------------------------------------------------------------------------
# News mentions -> tape
# ---------------------------------------------------------------------------
def test_news_mention_persistence_and_tape_are_idempotent(conn):
    rec = {
        "article_url": "https://example.com/story/1",
        "title": "Taylor Swift announces tour",
        "domain": "example.com",
        "published_at": "2026-08-15T12:00:00",
    }
    assert _persist_news_mention(
        conn, rec, entity_type="ARTIST", entity_name="Taylor Swift",
        entity_id="name::taylor swift", query='"Taylor Swift"',
        retrieved_at="2026-08-15T13:00:00+00:00",
    ) is True
    # Duplicate (same entity + URL) is not persisted again.
    assert _persist_news_mention(
        conn, rec, entity_type="ARTIST", entity_name="Taylor Swift",
        entity_id="name::taylor swift", query='"Taylor Swift"',
        retrieved_at="2026-08-15T14:00:00+00:00",
    ) is False
    conn.commit()

    rows = derive_news_tape_entries(conn)
    assert len(rows) == 1
    assert rows[0]["activity_type"] == "NEWS_MENTION"
    assert rows[0]["entity_type"] == "ARTIST"
    assert insert_tape_entries(conn, rows) == 1
    # Idempotent: re-derive adds nothing.
    assert insert_tape_entries(conn, derive_news_tape_entries(conn)) == 0

    news = get_news(conn, "Taylor Swift")
    assert len(news) == 1
    assert news[0]["article_url"] == "https://example.com/story/1"
    assert len(get_recent_news(conn)) == 1


# ---------------------------------------------------------------------------
# Wikimedia pageviews attention
# ---------------------------------------------------------------------------
def test_pageviews_missing_is_not_zero(conn):
    transport = _FakeTransport([(404, b"not found")])
    summary = collect_artist_pageviews(conn, transport, names=["No Such Artist 999"], days=30)
    assert summary["missing"] == 1
    assert summary["ok"] == 0
    # A missing article is persisted as status='missing', never a zero value.
    row = conn.execute(
        "SELECT status, value FROM metrics.artist_attention_observations WHERE status != 'ok'"
    ).fetchone()
    assert row is not None and row[0] == "missing" and row[1] is None
    # The read model only surfaces 'ok' rows; nothing is fabricated.
    assert get_attention_series(conn, "No Such Artist 999") == []


def test_pageviews_ok_persists_and_reads_back(conn):
    payload = {
        "items": [
            {"project": "en.wikipedia", "article": "Taylor_Swift", "granularity": "daily",
             "timestamp": "2026081500", "access": "all-access", "agent": "user", "views": 1200},
            {"project": "en.wikipedia", "article": "Taylor_Swift", "granularity": "daily",
             "timestamp": "2026081600", "access": "all-access", "agent": "user", "views": 950},
        ]
    }
    transport = _FakeTransport([payload])
    summary = collect_artist_pageviews(conn, transport, names=["Taylor Swift"], days=30)
    assert summary["ok"] == 1
    assert summary["rows_persisted"] == 1
    # Idempotent: same window re-collect does not duplicate.
    summary2 = collect_artist_pageviews(conn, transport, names=["Taylor Swift"], days=30)
    assert summary2["rows_persisted"] == 0

    series = get_attention_series(conn, "Taylor Swift")
    assert len(series) == 1
    assert series[0]["value"] == 2150.0
    assert series[0]["unit"] == "pageviews"
    assert series[0]["provider"] == "wikimedia"


# ---------------------------------------------------------------------------
# Ticketmaster partition manifest
# ---------------------------------------------------------------------------
def test_partition_manifest_complete_vs_truncated(conn):
    ts = "2026-08-15T10:00:00+00:00"
    start = datetime(2026, 8, 15)
    end = datetime(2027, 8, 15)
    _persist_partition(
        conn, "chicago:20260815-20270815", "Chicago,IL,US", start, end,
        "COMPLETE", 120, False, None, None, 0, ts, received=40, persisted=40,
    )
    _persist_partition(
        conn, "losangeles:20260815-20270815", "Los Angeles,CA,US", start, end,
        "TRUNCATED_BY_CAP", 8000, True, "reported_total_exceeds_ceiling",
        None, 0, ts, received=1000, persisted=1000,
    )
    conn.commit()
    rows = conn.execute(
        "SELECT market_id, status, truncated, total_expected, records_received "
        "FROM terminal.acquisition_partitions ORDER BY market_id"
    ).fetchall()
    assert len(rows) == 2
    chicago = [r for r in rows if r[0] == "Chicago,IL,US"][0]
    assert chicago[1] == "COMPLETE" and chicago[2] is False and chicago[3] == 120
    la = [r for r in rows if r[0] == "Los Angeles,CA,US"][0]
    assert la[1] == "TRUNCATED_BY_CAP" and la[2] is True and la[3] == 8000


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
def test_gdelt_provider_is_operational_public(monkeypatch):
    statuses = provider_statuses()
    gdelt = next(s for s in statuses if s["provider"] == "gdelt")
    assert gdelt["auth_status"] == "PUBLIC_NO_AUTH"
    assert gdelt["operational_status"] == OPERATIONAL
    # ListenBrainz is now a real no-auth provider (artist stats keyed by MBID).
    lb = next(s for s in statuses if s["provider"] == "listenbrainz")
    assert lb["auth_status"] == "PUBLIC_NO_AUTH"
    assert lb["operational_status"] == OPERATIONAL


# ---------------------------------------------------------------------------
# Minimal transport shim (mirrors conftest.FakeTransport semantics)
# ---------------------------------------------------------------------------
class _FakeTransport:
    def __init__(self, responses, default_status: int = 500):
        self._responses = list(responses)
        self.default_status = default_status
        self.requests: list[dict] = []

    def request(self, method, url, *, headers=None, params=None, body=None,
                timeout_seconds=30.0):
        from festival_bloomberg.acquisition.transport import HttpResponse

        self.requests.append({"method": method, "url": url})
        if self._responses:
            item = self._responses.pop(0)
            if isinstance(item, tuple):
                status, payload = item
            else:
                status, payload = 200, item
        else:
            status, payload = self.default_status, {"error": "no scripted response"}
        if isinstance(payload, bytes):
            body_bytes = payload
        elif payload is None:
            body_bytes = b""
        else:
            body_bytes = json.dumps(payload).encode("utf-8")
        return HttpResponse(status, body_bytes, {})
