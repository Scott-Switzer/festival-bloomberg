"""Regression coverage for INTELLIGENCE_DATA_ESTATE_AND_FESTIVAL_SPINE_V1.

Covers: festival spine semantics (festival != edition, billing observations
coexist, unresolved artists stay unresolved), provider status taxonomy (a
no-key provider can never be NOT_CONFIGURED), credential-presence safety (no
values leak), and NVIDIA/LLM fail-closed behavior.
"""

from __future__ import annotations

import duckdb
import pytest

from festival_bloomberg.acquisition.contracts import AcquisitionRequest
from festival_bloomberg.config import all_credential_status, credential_status, provider_credential_status
from festival_bloomberg.festivals.repository import (
    FestivalSpineRepository,
    billing_trajectory,
    co_occurrence,
)
from festival_bloomberg.festivals.seed import build_seed_rows
from festival_bloomberg.intelligence.ask import answer, run_tool
from festival_bloomberg.intelligence.llm import ModelRouter, NimClient
from festival_bloomberg.intelligence.providers import (
    AUTH_MISSING,
    DISABLED_RIGHTS,
    NOT_IMPLEMENTED,
    OPERATIONAL,
    PUBLIC_NO_AUTH,
    ListenBrainzProvider,
    NwsProvider,
    SeatGeekProvider,
    provider_statuses,
)
from festival_bloomberg.intelligence.readmodels import get_festival, search_entities
from festival_bloomberg.intelligence.tape import (
    derive_festival_tape_entries,
    insert_tape_entries,
)
from festival_bloomberg.migrations import apply_pending_migrations

from conftest import FakeTransport, make_request


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "estate.duckdb"))
    apply_pending_migrations(c)
    yield c
    c.close()


@pytest.fixture()
def seeded(conn):
    repo = FestivalSpineRepository(conn)
    repo.ingest_seed(build_seed_rows())
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Seed + spine semantics
# ---------------------------------------------------------------------------
def test_seed_is_deterministic_and_source_backed():
    rows = build_seed_rows()
    assert len(rows["festivals"]) == 6
    assert len(rows["editions"]) == 6
    assert len(rows["lineup_slots"]) == len(rows["billing_observations"]) == 96
    # Every billing observation must carry a source URL and be a research seed.
    for b in rows["billing_observations"]:
        assert b["source_url"], b["raw_artist_name"]
        assert b["evidence_class"] == "RESEARCH_DISCOVERY_SEED"
        assert b["commercial_use_status"] == "RESEARCH_ONLY"


def test_festival_ingest_is_idempotent(conn):
    repo = FestivalSpineRepository(conn)
    first = repo.ingest_seed(build_seed_rows())
    conn.commit()
    second = repo.ingest_seed(build_seed_rows())
    assert first["festivals"] == 6
    assert second["festivals"] == 0
    assert second["lineup_slots"] == 0


def test_festival_is_distinct_from_edition(seeded):
    festivals = seeded.execute("SELECT COUNT(*) FROM core.festivals").fetchone()[0]
    editions = seeded.execute("SELECT COUNT(*) FROM core.festival_editions").fetchone()[0]
    assert festivals == 6
    assert editions == 6


def test_unresolved_artist_stays_unresolved(seeded):
    # Research-seed lineups never force an artist identity.
    n_unresolved = seeded.execute(
        "SELECT COUNT(*) FROM core.lineup_slots WHERE artist_key IS NULL"
    ).fetchone()[0]
    assert n_unresolved == 96


def test_conflicting_billing_observations_coexist(seeded):
    repo = FestivalSpineRepository(seeded)
    # A second, conflicting source-specific claim for the same act is kept
    # (different source_provider -> different dedupe_key), never merged away.
    rows = build_seed_rows()
    conflict = dict(rows["billing_observations"][0])
    conflict["observation_id"] = "conflict-1"
    conflict["source_provider"] = "other_source"
    conflict["source_url"] = "https://example.org/different"
    conflict["printed_tier"] = 1  # contradicts the original
    conflict["dedupe_key"] = "conflicting-claim-1"
    seeded.execute(
        """
        INSERT INTO core.festival_billing_observations
            (observation_id, festival_key, edition_key, raw_artist_name,
             billing_context, printed_tier, source_provider, source_url,
             rights_status, commercial_use_status, evidence_class, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            conflict["observation_id"], conflict["festival_key"], conflict["edition_key"],
            conflict["raw_artist_name"], conflict["billing_context"], conflict["printed_tier"],
            conflict["source_provider"], conflict["source_url"], conflict["rights_status"],
            conflict["commercial_use_status"], conflict["evidence_class"], conflict["dedupe_key"],
        ],
    )
    seeded.commit()
    n = seeded.execute(
        "SELECT COUNT(*) FROM core.festival_billing_observations WHERE raw_artist_name = ?",
        [conflict["raw_artist_name"]],
    ).fetchone()[0]
    assert n == 2  # original + conflicting claim coexist


def test_billing_trajectory_and_cooccurrence(seeded):
    traj = billing_trajectory(seeded, "Jimi Hendrix Experience")
    assert traj  # Monterey 1967 tier 1
    assert traj[0]["festival_name"]
    # Ravi Shankar appears at both Monterey (1967) and Woodstock (1969).
    co = co_occurrence(seeded, "Ravi Shankar")
    assert len(co) > 0


def test_festival_tape_derivation_is_idempotent(seeded):
    rows = derive_festival_tape_entries(seeded)
    assert rows
    types = {r["activity_type"] for r in rows}
    assert "FESTIVAL_EDITION_DISCOVERED" in types
    assert "LINEUP_ANNOUNCED" in types
    first = insert_tape_entries(seeded, rows)
    seeded.commit()
    second = insert_tape_entries(seeded, derive_festival_tape_entries(seeded))
    assert first > 0
    assert second == 0  # idempotent


# ---------------------------------------------------------------------------
# Read models + ASK
# ---------------------------------------------------------------------------
def test_search_finds_festivals(seeded):
    results = search_entities(seeded, "woodstock")
    assert any(r["entity_type"] == "FESTIVAL" for r in results)


def test_get_festival_readmodel(seeded):
    fest = get_festival(seeded, "coachella-valley-music-and-arts-festival")
    assert fest is not None
    assert fest["entity_type"] == "FESTIVAL"
    assert len(fest["editions"]) == 1
    assert fest["editions"][0]["lineup"]
    assert fest["editions"][0]["billing"]


def test_ask_festival_intent_routes_and_unknown_tool_fails(seeded):
    res = answer(seeded, "show me the lollapalooza lineup")
    assert res["evidence"], "festival question should return source-backed evidence"
    # The ASK tool surface is closed: arbitrary SQL is rejected.
    assert run_tool(seeded, "SELECT * FROM anything", {})["ok"] is False
    assert run_tool(seeded, "not_a_tool", {})["ok"] is False


# ---------------------------------------------------------------------------
# Provider status taxonomy (the no-key bug fix)
# ---------------------------------------------------------------------------
def test_no_key_provider_never_not_configured():
    nws = NwsProvider().describe()
    assert nws["auth_status"] == PUBLIC_NO_AUTH
    assert nws["operational_status"] == OPERATIONAL

    lb = ListenBrainzProvider().describe()
    assert lb["auth_status"] == PUBLIC_NO_AUTH
    assert lb["operational_status"] == NOT_IMPLEMENTED
    # the old bug: NOT_CONFIGURED must never appear for a no-key provider
    assert lb["operational_status"] != "NOT_CONFIGURED"


def test_seatgeek_disabled_rights():
    assert SeatGeekProvider().describe()["operational_status"] == DISABLED_RIGHTS


def test_provider_statuses_unified_and_no_values():
    statuses = provider_statuses()
    assert len(statuses) >= 15
    for s in statuses:
        assert "credentials" in s
        assert set(s["credentials"].keys()) == {"configured", "keys"}
        # never the secret value
        assert "value" not in s["credentials"]


def test_credential_status_reports_presence_only(monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "super-secret-value")
    status = credential_status("TICKETMASTER_API_KEY")
    assert status["present"] is True
    assert status["nonempty"] is True
    assert status["source"] == "env"
    # the value itself never appears in the status payload
    assert "super-secret-value" not in repr(status)
    for entry in all_credential_status().values():
        assert set(entry.keys()) == {"name", "present", "nonempty", "source"}
    for entry in provider_credential_status().values():
        assert set(entry.keys()) == {"provider", "keys", "present_any", "nonempty_any"}


# ---------------------------------------------------------------------------
# NVIDIA / LLM fail-closed
# ---------------------------------------------------------------------------
def test_nim_client_fails_closed_without_key():
    client = NimClient(api_key=None)
    assert client.is_configured is False
    assert client.list_models() == {"status": "NOT_CONFIGURED", "models": []}
    assert client.chat(messages=[{"role": "user", "content": "hi"}])["ok"] is False


def test_nim_client_malformed_response_fails_closed():
    transport = FakeTransport([
        (200, {"data": [{"id": "meta/llama-3.3-70b-instruct"}]}),
        (200, {"not": "the schema"}),
    ])
    client = NimClient(api_key="k", transport=transport)
    assert client.is_configured is True
    resp = client.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp["ok"] is False
    assert resp["status"] == "MALFORMED_RESPONSE"  # malformed -> fail closed


def test_model_router_resolves_from_catalog():
    router = ModelRouter()
    assert router.route("FAST_EXTRACT")
    # catalog hint matching picks a real available model over the default
    picked = router.resolve("DEEP_REASON", ["meta/llama-3.3-70b-instruct", "deepseek-ai/deepseek-r1"])
    assert "deepseek-r1" in picked


def test_model_router_explicit_override_wins():
    catalog = ["meta/llama-3.3-70b-instruct", "deepseek-ai/deepseek-r1"]
    router = ModelRouter(catalog=catalog, tasks={"FAST_EXTRACT": "deepseek-ai/deepseek-r1"})
    assert router.route("FAST_EXTRACT") == "deepseek-ai/deepseek-r1"


def test_model_router_override_absent_from_catalog_rejected():
    catalog = ["meta/llama-3.3-70b-instruct"]
    router = ModelRouter(catalog=catalog, tasks={"FAST_EXTRACT": "not/in/catalog"})
    # invalid override is skipped; fallback is used only because it IS in catalog
    assert router.route("FAST_EXTRACT") == "meta/llama-3.3-70b-instruct"


def test_model_router_unavailable_fails_closed():
    # a catalog with no compatible model must never yield an invented id
    router = ModelRouter(catalog=["some/other-model"])
    assert router.route("FAST_EXTRACT") == ModelRouter.UNAVAILABLE


def test_nim_client_chat_uses_catalog_valid_model():
    import json

    catalog = [{"id": "deepseek-ai/deepseek-r1"}]
    transport = FakeTransport([
        (200, {"data": catalog}),
        (200, {"choices": [{"message": {"content": "grounded answer"}}]}),
    ])
    client = NimClient(api_key="k", transport=transport)
    resp = client.chat(messages=[{"role": "user", "content": "hi"}], task="DEEP_REASON")
    assert resp["ok"] is True
    assert resp["model"] == "deepseek-ai/deepseek-r1"
    chat_req = [r for r in transport.requests if r["url"].endswith("/chat/completions")][0]
    sent = json.loads(chat_req["body"].decode("utf-8"))
    assert sent["model"] in [m["id"] for m in catalog]  # never an off-catalog id


def test_nim_client_model_unavailable_fails_closed():
    transport = FakeTransport([
        (200, {"data": [{"id": "some/other-model"}]}),
    ])
    client = NimClient(api_key="k", transport=transport)
    resp = client.chat(messages=[{"role": "user", "content": "hi"}], task="FAST_EXTRACT")
    assert resp["ok"] is False
    assert resp["status"] == "MODEL_UNAVAILABLE"
    # no chat request was ever issued
    assert all(not r["url"].endswith("/chat/completions") for r in transport.requests)


def test_nim_client_embed_fails_closed_on_unavailable():
    transport = FakeTransport([
        (200, {"data": [{"id": "some/other-model"}]}),
    ])
    client = NimClient(api_key="k", transport=transport)
    resp = client.embed(inputs=["hello"])
    assert resp["ok"] is False
    assert resp["status"] == "MODEL_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Spotify (classic client-credentials, 2026 catalog-only) acquisition
# ---------------------------------------------------------------------------
def test_spotify_provider_catalog_fields_only():
    from festival_bloomberg.acquisition.providers.spotify import SpotifyProvider

    transport = FakeTransport([
        (200, {"access_token": "tok", "expires_in": 3600}),
        (200, {"artists": {"items": [{
            "id": "id1", "name": "Taylor Swift",
            "external_urls": {"spotify": "https://open.spotify.com/artist/id1"},
            "images": [], "type": "artist", "uri": "spotify:artist:id1",
        }], "total": 1}}),
    ])
    provider = SpotifyProvider(
        transport=transport,
        env={"SPOTIFY_CLIENT_ID": "cid", "SPOTIFY_CLIENT_SECRET": "csec"},
    )
    result = provider.acquire(make_request(query="Taylor Swift", platform="spotify"))
    assert result.status.value == "SUCCESS"
    assert result.record_count == 1
    rec = result.records[0]
    assert rec["spotify_id"] == "id1"
    # 2026 Dev Mode removals are never persisted or assumed.
    assert "popularity" not in rec
    assert "followers" not in rec
    assert "genres" not in rec
    assert rec["fields_present"] == ["external_urls", "id", "images", "name", "type", "uri"]


def test_spotify_provider_401_refreshes_token_and_retries():
    from festival_bloomberg.acquisition.providers.spotify import SpotifyProvider

    transport = FakeTransport([
        (200, {"access_token": "tok1", "expires_in": 3600}),
        (401, {"error": "invalid token"}),
        (200, {"access_token": "tok2", "expires_in": 3600}),
        (200, {"artists": {"items": [{"id": "id2", "name": "A", "type": "artist"}], "total": 1}}),
    ])
    provider = SpotifyProvider(
        transport=transport,
        env={"SPOTIFY_CLIENT_ID": "cid", "SPOTIFY_CLIENT_SECRET": "csec"},
    )
    result = provider.acquire(make_request(query="A", platform="spotify"))
    assert result.status.value == "SUCCESS"
    assert len(transport.requests) == 4  # token, search(401), token, search(200)


def test_spotify_provider_missing_credentials_is_not_configured():
    from festival_bloomberg.acquisition.providers.spotify import SpotifyProvider

    provider = SpotifyProvider(transport=FakeTransport(), env={})
    result = provider.acquire(make_request(query="A", platform="spotify"))
    assert result.status.value == "NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# NWS (public, no key) acquisition semantics
# ---------------------------------------------------------------------------
def test_nws_provider_forecast_snapshot():
    from festival_bloomberg.acquisition.providers.nws import NwsProvider

    transport = FakeTransport([
        (200, {"properties": {"forecast": "https://api.weather.gov/gridpoints/LOT/74,75/forecast"}}),
        (200, {
            "properties": {
                "generatedAt": "2026-08-15T12:00:00+00:00",
                "periods": [
                    {
                        "number": 1,
                        "startTime": "2026-08-15T18:00:00+00:00",
                        "endTime": "2026-08-16T06:00:00+00:00",
                        "temperature": 72,
                        "temperatureUnit": "F",
                        "probabilityOfPrecipitation": {"value": 20},
                        "windSpeed": "10 mph",
                        "shortForecast": "Partly Cloudy",
                    }
                ],
            }
        }),
    ])
    req = make_request(query="41.8781,-87.6298", platform="nws")
    result = NwsProvider(transport=transport).acquire(req)
    assert result.record_count == 1
    rec = result.records[0]
    # forecast generation time is kept separate from validity window
    assert rec["generation_time"] == "2026-08-15T12:00:00+00:00"
    assert rec["valid_start"] == "2026-08-15T18:00:00+00:00"
