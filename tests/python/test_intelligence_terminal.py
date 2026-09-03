"""Offline regressions for FESTIVAL_INTELLIGENCE_TERMINAL_MVP_V1.

Covers the activity tape invariants (append-only, idempotent, no UNCHANGED
rows), the read-only read models, the grounded ASK layer (no arbitrary SQL, no
fabricated facts, no evidence writes), the fail-closed provider scaffolds, the
read-only HTTP dispatcher, and the live OA driver end-to-end. No network.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import duckdb
import pytest
from festival_bloomberg.intelligence.ask import answer, run_tool
from festival_bloomberg.intelligence.providers import (
    ALL_PROVIDERS,
    AUTH_MISSING,
    OPERATIONAL,
    PUBLIC_NO_AUTH,
    provider_statuses,
)
from festival_bloomberg.intelligence.readmodels import (
    get_artist,
    get_event,
    get_market,
    get_sources,
    get_venue,
    query_tape,
    search_entities,
)
from festival_bloomberg.intelligence.tape import (
    ACTIVITY_TYPES,
    build_tape_row,
    derive_tape_entries,
    insert_tape_entries,
)
from festival_bloomberg.migrations import apply_pending_migrations


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "iterm.duckdb"))
    apply_pending_migrations(c)
    yield c
    c.close()


def _seed_forward_event(conn, watch_id, artist, venue, market, event_date, first_seen):
    conn.execute(
        """
        INSERT INTO flywheel.forward_watch_events
            (watch_event_id, provider, provider_event_id, artist_name, venue_name,
             market, event_date, first_seen_at, tracking_started_at, tracking_status,
             knowledge_time, rights_status, commercial_use_status, observation_class)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [watch_id, "musicbrainz", f"pe-{watch_id}", artist, venue, market,
         event_date, first_seen, first_seen, "TRACKING", first_seen,
         "RESEARCH_ONLY", "RESEARCH_ONLY", "OBSERVED_PUBLIC"],
    )


def _seed_observation(conn, obs_id, watch_id, observed_at, status=None, pmin=None, pmax=None):
    conn.execute(
        """
        INSERT INTO flywheel.forward_watch_observations
            (observation_id, watch_event_id, observed_at, retrieved_at, knowledge_time,
             event_status, price_min, price_max, source_provider, rights_status,
             commercial_use_status, observation_class)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [obs_id, watch_id, observed_at, observed_at, observed_at, status, pmin, pmax,
         "ticketmaster", "RESEARCH_ONLY", "RESEARCH_ONLY", "OBSERVED_PUBLIC"],
    )


def _seed_pit(conn, canonical_id, pub, provider="pollstar", doc_id="d1"):
    conn.execute(
        """
        INSERT INTO flywheel.pit_reconstruction_evidence
            (evidence_id, canonical_event_id, evidence_class, source_publication_time,
             source_url, source_provider, source_document_id, rights_status,
             commercial_use_status, knowledge_time)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        [f"pit-{canonical_id}-{doc_id}", canonical_id, "OBSERVED_DAY", pub,
         "https://x", provider, doc_id, "RESEARCH_ONLY", "RESEARCH_ONLY", pub],
    )


def _seed_cutoff(conn, cutoff_id, canonical_id, ctype, ckind, kt, ub=None, ts=None):
    conn.execute(
        """
        INSERT INTO flywheel.pre_event_cutoff_evidence
            (cutoff_id, canonical_event_id, cutoff_type, cutoff_kind, evidence_class,
             granularity, cutoff_timestamp, upper_bound, source_provider,
             rights_status, commercial_use_status, knowledge_time)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [cutoff_id, canonical_id, ctype, ckind, "ARCHIVE_CAPTURE_UPPER_BOUND",
         "EXACT", ts, ub, "musicbrainz", "RESEARCH_ONLY", "RESEARCH_ONLY", kt],
    )


# ---------------------------------------------------------------------------
# Tape invariants
# ---------------------------------------------------------------------------
def test_tape_activity_types_closed():
    assert "EVENT_DISCOVERED" in ACTIVITY_TYPES
    assert "OUTCOME_PUBLISHED" in ACTIVITY_TYPES
    assert "PRICE_CHANGED" in ACTIVITY_TYPES
    with pytest.raises(ValueError):
        build_tape_row(
            activity_type="NOT_A_REAL_TYPE", entity_type="EVENT", entity_id="x",
            observed_at=datetime.now(UTC), source_provider="p",
            source_record_id="r", knowledge_time=datetime.now(UTC),
            rights_status="R",
        )


def test_tape_derivation_is_idempotent(conn):
    _seed_forward_event(conn, "w1", "Artist A", "Venue A", "Chicago", date(2099, 1, 1),
                        datetime(2026, 8, 15, 10, 0, 0))
    conn.commit()
    rows1 = derive_tape_entries(conn)
    assert len(rows1) == 1
    n1 = insert_tape_entries(conn, rows1)
    assert n1 == 1
    # Re-derive: same rows, zero new writes (append-only, never rewritten).
    n2 = insert_tape_entries(conn, derive_tape_entries(conn))
    assert n2 == 0
    assert conn.execute("SELECT COUNT(*) FROM terminal.activity_tape").fetchone()[0] == 1


def test_unchanged_observation_never_becomes_tape_entry(conn):
    _seed_forward_event(conn, "w1", "Artist A", "Venue A", "Chicago", date(2099, 1, 1),
                        datetime(2026, 8, 15, 10, 0, 0))
    _seed_observation(conn, "o1", "w1", datetime(2026, 8, 16, 10, 0, 0), status="ONSALE", pmin=50.0, pmax=120.0)
    _seed_observation(conn, "o2", "w1", datetime(2026, 8, 17, 10, 0, 0), status="ONSALE", pmin=50.0, pmax=120.0)
    conn.commit()
    rows = derive_tape_entries(conn)
    # Only EVENT_DISCOVERED + one status + one price change; the second
    # identical observation contributes nothing (UNCHANGED is not a change).
    types = [r["activity_type"] for r in rows]
    assert types.count("PRICE_CHANGED") == 1
    assert types.count("EVENT_STATUS_CHANGED") == 1
    assert types.count("EVENT_DISCOVERED") == 1


def test_price_change_preserves_old_and_new(conn):
    _seed_forward_event(conn, "w1", "Artist A", "Venue A", "Chicago", date(2099, 1, 1),
                        datetime(2026, 8, 15, 10, 0, 0))
    _seed_observation(conn, "o1", "w1", datetime(2026, 8, 16, 10, 0, 0), pmin=50.0, pmax=120.0)
    _seed_observation(conn, "o2", "w1", datetime(2026, 8, 17, 10, 0, 0), pmin=60.0, pmax=140.0)
    conn.commit()
    rows = derive_tape_entries(conn)
    # The o1 -> o2 transition carries old + new; the very first observation
    # (o1) is a change-from-unknown with old_value NULL.
    price = [r for r in rows if r["activity_type"] == "PRICE_CHANGED" and r["old_value_json"]][0]
    assert json.loads(price["old_value_json"]) == {"min": 50.0, "max": 120.0}
    assert json.loads(price["new_value_json"]) == {"min": 60.0, "max": 140.0}


def test_unknown_is_never_encoded_as_zero(conn):
    # No forward events -> derivation yields no EVENT_DISCOVERED row; an
    # absent fact is not represented by a fabricated zero row.
    assert derive_tape_entries(conn) == []
    assert query_tape(conn) == []


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------
def test_search_returns_canonical_entities(conn):
    _seed_forward_event(conn, "w1", "Taylor Swift", "United Center", "Chicago", date(2099, 1, 1),
                        datetime(2026, 8, 15, 10, 0, 0))
    conn.execute(
        "INSERT INTO research.canonical_boxoffice_engagements "
        "(canonical_engagement_id, artist, venue, city, start_date, number_of_shows, is_multi_show) "
        "VALUES ('e1','Taylor Swift','United Center','Chicago, United States','2024-06-01',1,false)"
    )
    conn.commit()
    res = search_entities(conn, "taylor")
    assert any(r["entity_type"] == "ARTIST" and r["name"] == "Taylor Swift" for r in res)
    res = search_entities(conn, "united center")
    assert any(r["entity_type"] == "VENUE" and r["name"] == "United Center" for r in res)
    res = search_entities(conn, "chicago")
    assert any(r["entity_type"] == "MARKET" and r["name"] == "Chicago" for r in res)


def test_artist_read_model(conn):
    _seed_forward_event(conn, "w1", "Kendrick Lamar", "United Center", "Chicago", date(2099, 1, 1),
                        datetime(2026, 8, 15, 10, 0, 0))
    conn.execute(
        "INSERT INTO research.canonical_boxoffice_engagements "
        "(canonical_engagement_id, artist, venue, city, start_date, number_of_shows, is_multi_show) "
        "VALUES ('e1','Kendrick Lamar','United Center','Chicago, United States','2024-06-01',1,false)"
    )
    conn.commit()
    a = get_artist(conn, "kendrick lamar")
    assert a is not None
    assert a["history_count"] == 1
    assert a["upcoming_count"] == 1
    assert a["history"][0]["canonical_engagement_id"] == "e1"


def test_event_read_model_forward_and_historical(conn):
    _seed_forward_event(conn, "w1", "Ariana Grande", "Amerant Bank Arena", "Sunrise",
                        date(2099, 1, 1), datetime(2026, 8, 15, 10, 0, 0))
    _seed_cutoff(conn, "c1", "w1", "GENERAL_ONSALE", "FIRST_SEEN_UPPER_BOUND",
                 datetime(2026, 8, 15, 10, 0, 0), ub=datetime(2026, 8, 15, 10, 0, 0))
    conn.execute(
        "INSERT INTO research.canonical_boxoffice_engagements "
        "(canonical_engagement_id, artist, venue, city, start_date, number_of_shows, is_multi_show) "
        "VALUES ('e1','Ariana Grande','Amerant Bank Arena','Sunrise, United States','2026-06-30',1,false)"
    )
    conn.commit()
    fwd = get_event(conn, "w1")
    assert fwd is not None and fwd["kind"] == "FORWARD"
    assert len(fwd["timeline"]) == 1
    hist = get_event(conn, "e1")
    assert hist is not None and hist["kind"] == "HISTORICAL"


def test_venue_capacity_claims_remain_claims(conn):
    conn.execute(
        "INSERT INTO research.canonical_boxoffice_engagements "
        "(canonical_engagement_id, artist, venue, city, start_date, number_of_shows, is_multi_show) "
        "VALUES ('e1','A','United Center','Chicago, United States','2024-06-01',1,false)"
    )
    conn.execute(
        "INSERT INTO economics.venue_source_ids "
        "(mapping_id, canonical_venue_id, venue_name, resolution_status, knowledge_time) "
        "VALUES ('vs1','venue-uc','United Center','resolved','2024-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO economics.venue_capacity_claims "
        "(claim_id, canonical_venue_id, capacity_value, capacity_kind, provider, source, "
        " retrieved_at, knowledge_time, claim_status) "
        "VALUES ('vc1','venue-uc',20917,'MAXIMUM','wikipedia','https://w','2024-01-01T00:00:00','2024-01-01T00:00:00','ACCEPTED')"
    )
    conn.commit()
    v = get_venue(conn, "united center")
    assert v is not None
    # Capacity is returned as a LIST of claims, never collapsed to one number.
    assert v["capacity_claims"][0]["capacity"] == 20917.0
    assert "capacity" not in v


def test_market_read_model(conn):
    _seed_forward_event(conn, "w1", "A", "United Center", "Chicago", date(2099, 1, 1),
                        datetime(2026, 8, 15, 10, 0, 0))
    conn.commit()
    m = get_market(conn, "chicago")
    assert m is not None
    assert m["upcoming_count"] == 1


def test_sources_merge_health_and_survive_api(conn):
    conn.execute(
        "INSERT INTO flywheel.source_registry "
        "(source_id, source_name, source_kind, pipeline, access_status, rights_status, "
        " commercial_use_status, registered_at) "
        "VALUES ('setlistfm','Setlist.fm','OUTCOME','OUTCOME_HUNTER','KEY_REQUIRED',"
        " 'TERMS_REVIEW_REQUIRED','TERMS_REVIEW_REQUIRED', CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO terminal.provider_health "
        "(provider, operational_status, measured_at) VALUES ('setlistfm','NOT_CONFIGURED', CURRENT_TIMESTAMP)"
    )
    conn.commit()
    sources = get_sources(conn)
    s = [x for x in sources if x["source_id"] == "setlistfm"][0]
    # Research-only / terms-review status survives into the read model (the UI
    # shows restrictions rather than hiding them).
    assert s["rights_status"] == "TERMS_REVIEW_REQUIRED"
    assert s["operational"]["operational_status"] == "NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# ASK
# ---------------------------------------------------------------------------
def test_ask_cannot_query_arbitrary_sql(conn):
    # The tool surface is closed: there is no SQL execution primitive, so a
    # hostile prompt requesting SQL is rejected (fail closed, ok=False).
    res = run_tool(conn, "run_sql", {"sql": "DROP TABLE x"})
    assert res["ok"] is False
    res = run_tool(conn, "DROP_TABLE", {})
    assert res["ok"] is False


def test_ask_is_read_only_and_cannot_persist(conn):
    # No tool writes; a "persist" tool name is simply not in the surface.
    assert "persist_evidence" not in {
        "search_entities", "get_artist", "get_event", "get_venue", "get_market",
        "get_festival", "get_activity_tape", "get_boxoffice_history",
        "get_attention_series", "get_news", "get_competing_events", "get_source_evidence",
    }
    res = run_tool(conn, "persist_evidence", {"x": 1})
    assert res["ok"] is False
    assert conn.execute("SELECT COUNT(*) FROM terminal.activity_tape").fetchone()[0] == 0


def test_ask_never_invents_facts(conn):
    _seed_forward_event(conn, "w1", "Sabrina Carpenter", "United Center", "Chicago",
                        date(2099, 1, 1), datetime(2026, 8, 15, 10, 0, 0))
    conn.commit()
    a = answer(conn, "what changed in chicago")
    # A factual answer carries evidence rows; it never emits invented values.
    assert a["mode"] == "deterministic"
    assert "evidence" in a
    # Every evidence row traces to a persisted tape/source row.
    for ev in a["evidence"]:
        assert ev.get("source_provider") or ev.get("activity_type")


def test_ask_factual_answer_has_evidence_references(conn):
    _seed_forward_event(conn, "w1", "Guns N Roses", "Stadium", "Chicago",
                        date(2099, 1, 1), datetime(2026, 8, 15, 10, 0, 0))
    conn.commit()
    insert_tape_entries(conn, derive_tape_entries(conn))
    conn.commit()
    a = answer(conn, "what changed in chicago")
    assert a["evidence"], "a grounded answer must cite its underlying rows"


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def test_providers_fail_closed_without_keys(monkeypatch):
    monkeypatch.delenv("JAMBASE_API_KEY", raising=False)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    monkeypatch.delenv("NOAA_API_TOKEN", raising=False)
    statuses = provider_statuses()
    by_name = {s["provider"]: s for s in statuses}
    # Keyed providers without keys are AUTH_MISSING, never fabricated OPERATIONAL.
    for name in ("jambase", "census", "ticketmaster-discovery", "spotify", "nvidia"):
        assert by_name[name]["operational_status"] == AUTH_MISSING
    # Public no-key providers can NEVER be AUTH_MISSING / NOT_CONFIGURED.
    for name in ("listenbrainz", "gdelt", "nws", "wikimedia", "commoncrawl"):
        assert by_name[name]["auth_status"] == PUBLIC_NO_AUTH
    assert by_name["nws"]["operational_status"] == OPERATIONAL
    assert by_name["listenbrainz"]["operational_status"] == OPERATIONAL
    # JamBase absence never breaks the terminal: it is OPTIONAL.
    assert "jambase" in by_name
    assert len(ALL_PROVIDERS) == 20


def test_provider_failure_does_not_break_read_path(conn):
    # A provider scaffold with no transport reports AUTH_MISSING without
    # touching the warehouse; read models still answer.
    from festival_bloomberg.intelligence.providers import JamBaseProvider

    p = JamBaseProvider(transport=None)
    assert p.run_bounded(conn)["status"] == AUTH_MISSING
    _seed_forward_event(conn, "w1", "A", "V", "M", date(2099, 1, 1),
                        datetime(2026, 8, 15, 10, 0, 0))
    conn.commit()
    assert len(search_entities(conn, "a")) >= 1


# ---------------------------------------------------------------------------
# Terminal dispatcher (read-only HTTP layer)
# ---------------------------------------------------------------------------
def test_dispatcher_endpoints(conn):
    from festival_bloomberg.terminal.server import TerminalApp

    _seed_forward_event(conn, "w1", "Taylor Swift", "United Center", "Chicago",
                        date(2099, 1, 1), datetime(2026, 8, 15, 10, 0, 0))
    conn.commit()
    insert_tape_entries(conn, derive_tape_entries(conn))
    conn.commit()
    app = TerminalApp(conn)

    assert app.dispatch("GET", "/api/search", "q=taylor")["status"] == 200
    assert app.dispatch("GET", "/api/tape")["status"] == 200
    assert app.dispatch("GET", "/api/sources")["status"] == 200
    assert app.dispatch("GET", "/api/events/w1")["status"] == 200
    assert app.dispatch("GET", "/api/artists/taylor swift")["status"] == 200
    assert app.dispatch("GET", "/api/venues/united center")["status"] == 200
    assert app.dispatch("GET", "/api/markets/chicago")["status"] == 200
    ask = app.dispatch("POST", "/api/ask", "", json.dumps({"question": "what changed in chicago"}).encode())
    assert ask["status"] == 200
    body = json.loads(ask["body"].decode())
    assert body["mode"] == "deterministic"
    # Missing / unknown entities return 404, not a fabricated object.
    assert app.dispatch("GET", "/api/events/does-not-exist")["status"] == 404
    assert app.dispatch("GET", "/api/festivals/none")["status"] == 200  # honest null body


def test_dispatcher_event_resolves_alert_style_keys(conn):
    """Alerts/TODAY link events as ``tm::<provider_event_id>``; the event
    route must resolve both that form and the raw provider id, never 404 on
    a real event."""
    from festival_bloomberg.terminal.server import TerminalApp

    _seed_forward_event(conn, "watch_abc123", "Taylor Swift", "United Center", "Chicago",
                        date(2099, 1, 1), datetime(2026, 8, 15, 10, 0, 0))
    # _seed_forward_event sets provider_event_id = f"pe-{watch_id}"; rewrite it
    # to the bare Ticketmaster id that forward-watch rows actually carry.
    conn.execute("UPDATE flywheel.forward_watch_events SET provider_event_id = 'vvXYZ' WHERE watch_event_id = 'watch_abc123'")
    conn.commit()
    app = TerminalApp(conn)

    for key in ("tm::vvXYZ", "vvXYZ"):
        res = app.dispatch("GET", f"/api/events/{key}")
        assert res["status"] == 200, key
        body = json.loads(res["body"].decode())
        assert body["watch_event_id"] == "watch_abc123"
        assert body["kind"] == "FORWARD"

    # Ticketmaster snapshot events (alert-style tm:: keys) must resolve too.
    conn.execute("""
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, artist_name,
             venue_name, city, state_code, country_code, local_date, event_status,
             onsale_start, price_min, price_max, price_currency, promoter, retrieved_at,
             knowledge_time, rights_status, commercial_use_status, ingested_at)
        VALUES ('snap1', 'ticketmaster', 'vvSNAP1', 'Snap Show', 'Snap Artist',
                'Snap Venue', 'Chicago', 'IL', 'US', '2026-09-01', 'onsale',
                '2026-08-01T10:00:00Z', 40.0, 120.0, 'USD', 'Snap Promoter',
                '2026-08-18T10:00:00Z', '2026-08-18T10:00:00Z',
                'TERMS_REVIEW_REQUIRED', 'TERMS_REVIEW_REQUIRED',
                '2026-08-18T10:00:00Z')
    """)
    conn.commit()
    res = app.dispatch("GET", "/api/events/tm::vvSNAP1")
    assert res["status"] == 200
    body = json.loads(res["body"].decode())
    assert body["kind"] == "SNAPSHOT"
    assert body["entity_key"] == "tm::vvSNAP1"
    assert body["entity_name"] == "Snap Show"
    assert len(body["observations"]) == 1


def test_dispatcher_serves_static_index(conn):
    from festival_bloomberg.terminal.server import TerminalApp

    app = TerminalApp(conn)
    res = app.dispatch("GET", "/")
    assert res["status"] == 200
    assert res["headers"]["Content-Type"] == "text/html"
    assert b"FESTIVAL" in res["body"] or b"Festival" in res["body"]


# ---------------------------------------------------------------------------
# OA driver end-to-end
# ---------------------------------------------------------------------------
def test_intelligence_terminal_oa_end_to_end(tmp_path):
    from festival_bloomberg.oa.intelligence_terminal import run_intelligence_terminal_oa

    db = str(tmp_path / "oa.duckdb")
    c = duckdb.connect(db)
    apply_pending_migrations(c)
    _seed_forward_event(c, "w1", "Taylor Swift", "United Center", "Chicago",
                        date(2099, 1, 1), datetime(2026, 8, 15, 10, 0, 0))
    _seed_observation(c, "o1", "w1", datetime(2026, 8, 16, 10, 0, 0), status="ONSALE")
    _seed_pit(c, "e1", datetime(2026, 8, 1, 12, 0, 0))
    c.commit()
    c.close()

    manifest = run_intelligence_terminal_oa(
        research_db=db, report_path=str(tmp_path / "manifest.json")
    )
    assert manifest["software_version"] == "intelligence_terminal_mvp_v1"
    assert manifest["activity_tape"]["new_rows_written"] >= 1
    assert manifest["entity_coverage"]["forward_events"] == 1
    # Provider health is fail-closed: keyed providers without keys are
    # AUTH_MISSING; public providers are never NOT_CONFIGURED.
    by_name = {h["provider"]: h for h in manifest["provider_health"]}
    assert by_name["jambase"]["operational_status"] == "AUTH_MISSING"
    assert by_name["nws"]["operational_status"] == "OPERATIONAL"
    assert by_name["listenbrainz"]["operational_status"] == "OPERATIONAL"
    assert (tmp_path / "manifest.json").is_file()
