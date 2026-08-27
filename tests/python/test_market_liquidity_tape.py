"""Regression coverage for MARKET_LIQUIDITY_TAPE_V1.

All offline via scripted FakeTransport — no network, no paid calls.

- Migration 045 tables (market_price_observations, artist_marketplace_links,
  source_auth_status, artist_forward_tape) exist
- Ticketmaster attraction linker: canonical candidates vs ambiguous fail-closed
- Bootstrap cohort: TM attraction-ID double-confirm → VERIFIED links
- TM structured price observations: STANDARD_PRICE_RANGE kept distinct from
  CURRENT_AVAILABLE_INVENTORY (never merged)
- SeatGeek / StubHub / Inventory-Status auth probes fail closed
- Forward artist tape: wiki latest daily + LB current + YT BLOCKED_INVALID_KEY
- Product join into asm.artist_market_security_v1 (descriptive only)
- Perspective monitor market-liquidity columns
- Rights/cost + success report
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations

from tests.python.conftest import FakeTransport

from festival_bloomberg.attention.wikimedia_historical import daily_observation_key
from festival_bloomberg.attention.youtube_forward import classify_youtube_api_key

from festival_bloomberg.security.forward_tape import (
    ingest_wiki_latest_daily,
    ingest_listenbrainz_current,
    ingest_youtube_credential,
    run_forward_tape,
)
from festival_bloomberg.security.artist_security_master import select_security_universe
from festival_bloomberg.security.market_liquidity import (
    build_bootstrap_cohort,
    collect_tm_price_observations,
    measure_longitudinal_depth,
    probe_inventory_status_auth,
    probe_seatgeek_auth,
    probe_stubhub_auth,
    resolve_tm_attractions,
)
from festival_bloomberg.security.market_liquidity_join import (
    join_market_liquidity_into_security,
)
from festival_bloomberg.security.market_liquidity_report import (
    build_market_liquidity_report,
)
from festival_bloomberg.security.marketplace_probe import probe_other_marketplaces
from festival_bloomberg.security.perspective_monitor import export_monitor_rows


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "market_liquidity.duckdb"))
    c.execute("SET memory_limit='2GB'")
    apply_pending_migrations(c)
    yield c
    c.close()


def _seed_artist(conn, artist_key, name, mbid):
    conn.execute(
        """INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name, sort_name)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT (artist_key) DO NOTHING""",
        [artist_key, mbid, name, name.lower(), name],
    )


def _seed_tm_estate(conn, event_id, artist_name, city, state, local_date, venue="Venue A",
                    attraction_id=None):
    conn.execute(
        """INSERT INTO events.provider_event_snapshots
           (snapshot_key, provider, platform_object_id, event_name, artist_name,
            venue_name, city, state_code, country_code, local_date, event_status,
            canonical_url, retrieved_at, knowledge_time, rights_status,
            commercial_use_status, software_version, ingested_at, attractions)
           VALUES (?, 'ticketmaster', ?, ?, ?, ?, ?, ?, 'US', ?, 'onsale', ?,
                   '2026-08-20T00:00:00Z', '2026-08-20T00:00:00Z',
                   'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', 'test', CURRENT_TIMESTAMP,
                   ?)""",
        [f"snap_{event_id}", event_id, f"{artist_name} live", artist_name,
         venue, city, state, local_date, f"https://tm.com/event/{event_id}",
         json.dumps([{"ticketmaster_attraction_id": attraction_id, "attraction_name": artist_name}]) if attraction_id else "[]"],
    )


def _seed_artist_market_row(conn, artist_key, market_key="chicago-il"):
    conn.execute(
        """INSERT INTO asm.artist_market_security_v1
           (row_key, artist_key, market_key, as_of, historical_shows,
            days_since_last_market_show, market_venues_played, venue_progression,
            upcoming_market_events, nearby_competing_events, ticket_evidence_count,
            source_system, source_version, retrieved_at, rights_status,
            commercial_use_status, evidence_json, ingested_at)
           VALUES (?, ?, ?, ?, 1, 10, 1, NULL, 0, 0, 0, 'test', 'test', ?,
                   'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', '{}', CURRENT_TIMESTAMP)""",
        [f"rk_{artist_key}_{market_key}", artist_key, market_key,
         date.today().isoformat(), "2026-08-20T00:00:00Z"],
    )


# ---------------------------------------------------------------------------
# Migration 045
# ---------------------------------------------------------------------------

class TestMigration045:
    def test_market_liquidity_tables_exist(self, conn):
        for table in (
            "acquisition.market_price_observations",
            "acquisition.artist_marketplace_links",
            "acquisition.source_auth_status",
            "metrics.artist_forward_tape",
        ):
            schema, name = table.split(".")
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema=? AND table_name=?",
                [schema, name],
            ).fetchone()
            assert row, f"{table} missing"
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='acquisition' AND table_name='market_price_observations'"
        ).fetchall()}
        for required in ("standard_primary_min", "standard_primary_max",
                         "current_available_min", "current_available_max",
                         "price_basis", "inventory_basis", "availability_state",
                         "listing_count", "retrieved_at", "raw_evidence_ref"):
            assert required in cols


# ---------------------------------------------------------------------------
# P0/P3: TM attraction linker (search candidates fail closed)
# ---------------------------------------------------------------------------

class TestAttractionLinker:
    def test_canonical_search_becomes_candidate(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        t = FakeTransport([
            (200, {"_embedded": {"attractions": [
                {"id": "K8vZ01", "name": "Alpha Artist"},
            ]}}),
        ])
        s = resolve_tm_attractions(
            conn, t, universe=select_security_universe(conn, limit=10),
            api_key="key", min_interval_seconds=0,
        )
        assert s["status"] == "COMPLETE"
        assert s["candidates"] == 1
        row = conn.execute(
            "SELECT resolution_status, attraction_id FROM identity.ticketmaster_artist_resolutions "
            "WHERE artist_key='mbid::a1' AND attraction_id IS NOT NULL"
        ).fetchone()
        assert row and row[0] == "MATCHED_ARTIST"
        assert row[1] == "K8vZ01"

    def test_tribute_attraction_excluded(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        t = FakeTransport([
            (200, {"_embedded": {"attractions": [
                {"id": "K8vZ02", "name": "The Alpha Artist Tribute Band"},
                {"id": "K8vZ01", "name": "Alpha Artist"},
            ]}}),
        ])
        s = resolve_tm_attractions(
            conn, t, universe=select_security_universe(conn, limit=10),
            api_key="key", min_interval_seconds=0,
        )
        # the single canonical (non-tribute) hit wins the candidate
        assert s["candidates"] == 1
        rows = conn.execute(
            "SELECT attraction_id FROM identity.ticketmaster_artist_resolutions "
            "WHERE artist_key='mbid::a1' AND attraction_id IS NOT NULL"
        ).fetchall()
        assert (
            "K8vZ01",
        ) in rows  # the canonical act won; the tribute band was excluded

    def test_no_key_fails_closed(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        s = resolve_tm_attractions(
            conn, FakeTransport(), universe=select_security_universe(conn, limit=10), api_key=None,
        )
        assert s["status"] == "NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# P5: bootstrap cohort (double-confirm via attraction ID)
# ---------------------------------------------------------------------------

class TestBootstrapCohort:
    def test_cohort_requires_double_confirm(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        # artist-level CANDIDATE resolution (dedicated TM identity table)
        conn.execute(
            """INSERT INTO identity.ticketmaster_artist_resolutions
               (resolution_key, attraction_id, attraction_name, normalized_name,
                artist_key, artist_mbid, matched_name, resolution_status,
                match_method, match_similarity, source_table, knowledge_time,
                software_version, ingested_at)
               VALUES ('res', 'K8vZ01', 'Alpha Artist', 'alpha artist', 'mbid::a1', 'a1',
                       'Alpha Artist', 'MATCHED_ARTIST', 'TICKETMASTER_ATTRACTION_SEARCH', 0.9,
                       'ticketmaster_discovery_attractions',
                       '2026-08-20T00:00:00Z', 'test', CURRENT_TIMESTAMP)"""
        )
        # event with that attraction id AND matching attribution → cohort
        _seed_tm_estate(conn, "evt101", "Alpha Artist", "Chicago", "IL",
                        "2026-11-01", attraction_id="K8vZ01")
        # event with same attraction id but DIFFERENT attribution → excluded
        _seed_tm_estate(conn, "evt102", "Some Other Band", "New York", "NY",
                        "2026-11-02", attraction_id="K8vZ01")
        from festival_bloomberg.security.event_tape import ingest_provider_estate_events
        ingest_provider_estate_events(conn)
        result = build_bootstrap_cohort(conn, max_events=50)
        assert result["status"] == "COMPLETE"
        cohort_events = [ev["provider_event_id"] for ev in result["cohort"]]
        assert "evt101" in cohort_events
        assert "evt102" not in cohort_events
        # the promoted event link is VERIFIED
        row = conn.execute(
            "SELECT link_status FROM acquisition.artist_marketplace_links "
            "WHERE artist_key='mbid::a1' AND event_key='event::tm:evt101'"
        ).fetchone()
        assert row and row[0] == "VERIFIED"


# ---------------------------------------------------------------------------
# P0: TM structured price observation (standard vs inventory distinct)
# ---------------------------------------------------------------------------

class TestTmPriceObservations:
    def _cohort(self):
        return [{
            "event_key": "event::tm:evtX", "market_key": "chicago-il",
            "event_date": date(2026, 11, 1), "provider_event_id": "evtX",
            "artist_key": "mbid::a1", "artist_name": "Alpha Artist",
            "canonical_url": "https://tm.com/event/evtX",
        }]

    def test_standard_range_observation(self, conn):
        ev = self._cohort()[0]
        tm_payload = {
            "id": "evtX", "name": "Alpha Artist Live", "url": "https://tm.com/event/evtX",
            "source": "ticketmaster",
            "dates": {"start": {"localDate": "2026-11-01"},
                      "status": {"code": "onsale"}},
            "sales": {"public": {"startDateTime": "2026-08-01T00:00:00Z"}},
            "priceRanges": [{"min": 49.0, "max": 199.0, "currency": "USD", "type": "standard"}],
            "promoter": {"name": "Live Nation"},
            "_embedded": {"venues": [{"name": "Venue A",
                                      "city": {"name": "Chicago"},
                                      "state": {"stateCode": "IL"}}]},
        }
        t = FakeTransport([(200, tm_payload)])
        s = collect_tm_price_observations(
            conn, t, cohort=self._cohort(), api_key="key", min_interval_seconds=0,
        )
        assert s["status"] == "COMPLETE"
        assert s["observations"] == 1
        row = conn.execute(
            "SELECT standard_primary_min, standard_primary_max, primary_currency, "
            "price_basis, inventory_basis, availability_state, event_status, promoter "
            "FROM acquisition.market_price_observations"
        ).fetchone()
        assert row[0] == 49.0 and row[1] == 199.0
        assert row[2] == "USD"
        assert row[3] == "STANDARD_PRICE_RANGE"
        # inventory price is NOT_EXPOSED and unset — never merged into standard
        assert row[4] == "NOT_EXPOSED"
        assert row[5] == "onsale"
        assert row[7] == "Live Nation"

    def test_no_standard_range_keeps_null(self, conn):
        tm_payload = {"id": "evtX", "name": "A", "url": "https://tm.com/event/evtX",
                      "source": "ticketmaster",
                      "dates": {"start": {"localDate": "2026-11-01"},
                                "status": {"code": "offsale"}},
                      "sales": {}, "priceRanges": None,
                      "_embedded": {"venues": []}}
        t = FakeTransport([(200, tm_payload)])
        s = collect_tm_price_observations(
            conn, t, cohort=self._cohort(), api_key="key", min_interval_seconds=0,
        )
        assert s["observations"] == 1
        row = conn.execute(
            "SELECT standard_primary_min, price_basis, availability_state "
            "FROM acquisition.market_price_observations"
        ).fetchone()
        assert row[0] is None
        assert row[1] == "UNKNOWN"  # no standard range exposed
        assert row[2] == "offsale"

    def test_no_key_fails_closed(self, conn):
        s = collect_tm_price_observations(
            conn, FakeTransport(), cohort=self._cohort(), api_key=None,
        )
        assert s["status"] == "NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# P0/P2: Auth probes fail closed
# ---------------------------------------------------------------------------

class TestAuthProbes:
    def test_inventory_status_not_authorized(self, conn):
        r = probe_inventory_status_auth(conn, FakeTransport(), api_key=None)
        assert r["status"] == "ABSENT"
        row = conn.execute(
            "SELECT auth_state FROM acquisition.source_auth_status "
            "WHERE provider='ticketmaster' AND provider_kind='inventory_api'"
        ).fetchone()
        assert row and row[0] == "ABSENT"

    def test_seatgeek_no_key_not_authorized(self, conn):
        r = probe_seatgeek_auth(conn, {})
        assert r["status"] == "NOT_AUTHORIZED"

    def test_stubhub_no_key_not_authorized(self, conn):
        r = probe_stubhub_auth(conn, {})
        assert r["status"] == "NOT_AUTHORIZED"

    def test_other_marketplaces_deferred(self, conn):
        r = probe_other_marketplaces(conn, {})
        assert r["providers"]["vividseats"]["auth_state"] == "NOT_AUTHORIZED"
        assert r["providers"]["gametime"]["phase"] == "P11_DEFERRAL"


# ---------------------------------------------------------------------------
# P7: Forward artist tape
# ---------------------------------------------------------------------------

class TestForwardTape:
    def _seed(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        today = date.today()
        for i, delta in ((1, 1), (2, 2)):
            d = today - timedelta(days=delta)
            conn.execute(
                """INSERT INTO metrics.artist_attention_observations
                   (observation_key, artist_key, source_system, metric_kind, value,
                    value_unit, status, source_url, retrieved_at, period_start, period_end,
                    granularity, metric_version)
                   VALUES (?, 'mbid::a1', 'wikimedia', 'pageviews', ?, 'pageviews', 'ok',
                           'https://src', '2026-08-20T00:00:00Z', ?, ?,
                           'daily', 'wikimedia_pageviews_daily_v1')""",
                [daily_observation_key(artist_key="mbid::a1", day=d.isoformat()),
                 100 + i, d.isoformat(), d.isoformat()],
            )
        conn.execute(
            """INSERT INTO metrics.artist_attention_observations
               (observation_key, artist_key, source_system, metric_kind, value,
                value_unit, status, source_url, retrieved_at, granularity, metric_version)
               VALUES ('lb1', 'mbid::a1', 'listenbrainz', 'LISTENBRAINZ_TOTAL_LISTEN_COUNT',
                       1000.0, 'listens', 'ok', 'https://src',
                       '2026-08-20T00:00:00Z', 'all_time', 'v1')"""
        )

    def test_wiki_latest_daily(self, conn):
        self._seed(conn)
        universe = select_security_universe(conn, limit=10)
        r = ingest_wiki_latest_daily(conn, universe=universe)
        assert r["status"] == "COMPLETE"
        assert r["artists_with_latest_daily"] == 1
        row = conn.execute(
            "SELECT feed, period_date, value, freshness_days, status "
            "FROM metrics.artist_forward_tape WHERE feed='wiki_daily'"
        ).fetchone()
        assert row[0] == "wiki_daily"
        assert row[1] == date.today() - timedelta(days=1)
        assert row[2] == 101.0  # latest day, max value
        assert row[4] == "OBSERVED"

    def test_listenbrainz_current(self, conn):
        self._seed(conn)
        universe = select_security_universe(conn, limit=10)
        r = ingest_listenbrainz_current(conn, universe=universe)
        assert r["artists_with_listens"] == 1
        row = conn.execute(
            "SELECT value, status FROM metrics.artist_forward_tape WHERE feed='listenbrainz'"
        ).fetchone()
        assert row[0] == 1000.0
        assert row[1] == "OBSERVED"

    def test_yt_invalid_key_blocks(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        universe = select_security_universe(conn, limit=10)
        r = ingest_youtube_credential(conn, universe=universe, api_key="dead-key")
        assert r["credential_state"] == "INVALID_KEY"
        row = conn.execute(
            "SELECT status, detail FROM metrics.artist_forward_tape WHERE feed='youtube_channel'"
        ).fetchone()
        assert row[0] == "BLOCKED"
        assert "FAIL_CLOSED" in (row[1] or "")


# ---------------------------------------------------------------------------
# P6: longitudinal depth
# ---------------------------------------------------------------------------

class TestLongitudinalDepth:
    def test_measure_returns_real_counters(self, conn):
        m = measure_longitudinal_depth(conn)
        assert m["status"] == "COMPLETE"
        assert "pit_event_marketplace_days_total" in m
        assert "pair_depth_distribution" in m
        _seed_tm_estate(conn, "evt1", "Alpha Artist", "Chicago", "IL", "2026-11-01")
        from festival_bloomberg.security.event_tape import ingest_provider_estate_events
        ingest_provider_estate_events(conn)
        m2 = measure_longitudinal_depth(conn)
        assert m2["events_with_price_observation"] == 0  # no price obs yet


# ---------------------------------------------------------------------------
# P8: product join into artist × market security
# ---------------------------------------------------------------------------

class TestProductJoin:
    def test_join_populates_descriptive_columns(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        _seed_artist_market_row(conn, "mbid::a1")
        # one price observation in chicago
        from festival_bloomberg.security.market_liquidity import _persist_price_observation
        _persist_price_observation(conn, {
            "observation_id": "obs1", "event_key": "event::tm:evtX",
            "artist_key": "mbid::a1", "market_key": "chicago-il",
            "marketplace": "ticketmaster", "provider_event_id": "evtX",
            "observed_at": "2026-08-20T00:00:00Z", "available_at": None,
            "retrieved_at": "2026-08-20T00:00:00Z", "knowledge_time": "2026-08-20T00:00:00Z",
            "standard_primary_min": 49.0, "standard_primary_max": 199.0,
            "primary_currency": "USD", "current_available_min": None,
            "current_available_max": None, "inventory_currency": None,
            "listings_extend_beyond_max": None, "listing_count": None,
            "average_public_offer": None, "lowest_public_offer": None,
            "highest_public_offer": None, "availability_state": "onsale",
            "event_status": "onsale", "price_basis": "STANDARD_PRICE_RANGE",
            "inventory_basis": "NOT_EXPOSED", "source": "ticketmaster_discovery_v2",
            "source_origin": "ticketmaster", "raw_evidence_ref": "evtX",
            "canonical_url": None, "promoter": None, "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY", "software_version": "test",
        })
        result = join_market_liquidity_into_security(conn)
        assert result["status"] == "COMPLETE"
        row = conn.execute(
            "SELECT marketplace_count, price_observation_count, latest_tm_standard_min, "
            "latest_tm_standard_max, latest_tm_onsale_state "
            "FROM asm.artist_market_security_v1 WHERE artist_key='mbid::a1'"
        ).fetchone()
        assert row[0] == 1
        assert row[1] == 1
        assert row[2] == 49.0 and row[3] == 199.0
        assert row[4] == "onsale"


# ---------------------------------------------------------------------------
# P9: Perspective monitor market-liquidity columns
# ---------------------------------------------------------------------------

class TestMonitorLiquidityColumns:
    def test_market_liquidity_columns_present(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        from festival_bloomberg.security.market_liquidity import _persist_price_observation
        _persist_price_observation(conn, {
            "observation_id": "obs1", "event_key": "event::tm:evtX",
            "artist_key": "mbid::a1", "market_key": "chicago-il",
            "marketplace": "ticketmaster", "provider_event_id": "evtX",
            "observed_at": "2026-08-20T00:00:00Z", "available_at": None,
            "retrieved_at": "2026-08-20T00:00:00Z", "knowledge_time": "2026-08-20T00:00:00Z",
            "standard_primary_min": 49.0, "standard_primary_max": 199.0,
            "primary_currency": "USD", "current_available_min": None,
            "current_available_max": None, "inventory_currency": None,
            "listings_extend_beyond_max": None, "listing_count": 25,
            "average_public_offer": None, "lowest_public_offer": None,
            "highest_public_offer": None, "availability_state": "onsale",
            "event_status": "onsale", "price_basis": "STANDARD_PRICE_RANGE",
            "inventory_basis": "NOT_EXPOSED", "source": "ticketmaster_discovery_v2",
            "source_origin": "ticketmaster", "raw_evidence_ref": "evtX",
            "canonical_url": None, "promoter": None, "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY", "software_version": "test",
        })
        rows = export_monitor_rows(conn, artist_keys=["mbid::a1"])
        assert len(rows) == 1
        r = rows[0]
        assert r["marketplace_count"] == 1
        assert r["price_observation_count"] == 1
        assert r["latest_tm_standard_min"] == 49.0
        assert r["latest_tm_standard_max"] == 199.0
        assert r["tm_onsale_state"] == "onsale"
        assert r["market_listing_count"] == 25


# ---------------------------------------------------------------------------
# Success report
# ---------------------------------------------------------------------------

class TestSuccessReport:
    def test_report_builds_real_counts(self, conn):
        _seed_tm_estate(conn, "evt1", "Alpha Artist", "Chicago", "IL", "2026-11-01")
        from festival_bloomberg.security.event_tape import ingest_provider_estate_events
        ingest_provider_estate_events(conn)
        report = build_market_liquidity_report(conn)
        assert report["status"] == "COMPLETE"
        assert report["price_observations"]["observations"] == 0
        assert report["multi_marketplace"]["events_2_plus"] >= 0
        assert report["provider_scorecard"]["providers"] is not None

    def test_report_has_rights_note(self, conn):
        report = build_market_liquidity_report(conn)
        note = report["rights_status"]["note"].lower()
        assert "never" in note and "attendance" in note