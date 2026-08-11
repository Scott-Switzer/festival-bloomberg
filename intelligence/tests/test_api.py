"""API tests using FastAPI TestClient against the offline seeded warehouse."""
import sys
from datetime import date

import pytest

sys.path.insert(0, "apps/api")

from fastapi.testclient import TestClient  # noqa: E402

import apps.api.main as api  # noqa: E402
from warehouse.repository import get_repository  # noqa: E402


@pytest.fixture
def client(repo, monkeypatch):
    # Point the API's repository singleton at our seeded temp repo.
    monkeypatch.setattr(api, "_repo", lambda: repo)
    return TestClient(api.app)


def test_health_reports_counts(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["artists"] >= 2
    assert body["festivals"] == 2


def test_list_festivals_real(client):
    resp = client.get("/festivals")
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()}
    assert "Lollapalooza" in names


def test_get_festival(client):
    resp = client.get("/festivals/name::lollapalooza")
    assert resp.status_code == 200
    assert resp.json()["capacity"] == 400000


def test_get_festival_404(client):
    resp = client.get("/festivals/name::nope")
    assert resp.status_code == 404


def test_search_artists(client):
    resp = client.post("/artists/search", json={"query": "radio", "limit": 5})
    assert resp.status_code == 200
    assert any(a["name"] == "Radiohead" for a in resp.json())


def test_get_artist_real_mbid(client):
    resp = client.get("/artists/a74b1b7f-36a9-4d22-a1cf-017dc00396d0")
    assert resp.status_code == 200
    assert resp.json()["musicbrainz_id"] == "a74b1b7f-36a9-4d22-a1cf-017dc00396d0"


def test_momentum_uses_real_wikipedia_metric(client):
    resp = client.get("/artists/a74b1b7f-36a9-4d22-a1cf-017dc00396d0/momentum")
    assert resp.status_code == 200
    body = resp.json()
    # 96903 ** 0.5 / 10 ~= 31.13
    assert round(body["momentum_score"], 1) == 31.1
    assert "wikipedia_pageviews" in body["sources"]


def test_booking_value_from_momentum(client):
    resp = client.get("/artists/a74b1b7f-36a9-4d22-a1cf-017dc00396d0/booking-value")
    assert resp.status_code == 200
    body = resp.json()
    assert body["booking_value_index"] > 0
    assert body["predicted_billing_tier"] in {"headliner", "sub-headliner", "supporting"}


def test_comparison_genre_entropy_computed(client):
    resp = client.get("/festivals/name::lollapalooza/comparison")
    assert resp.status_code == 200
    body = resp.json()
    # 3 genres -> entropy > 0, no placeholder 0.78
    assert body["genre_entropy"] is not None
    assert body["genre_entropy"] > 0


def test_market_overview_ranks_real_artists(client):
    resp = client.get("/market/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["top_momentum_artists"]
    assert body["upcoming_festivals"]
    # No fake placeholder artist names
    names = {a["name"] for a in body["top_momentum_artists"]}
    assert "Artist A" not in names


def test_revenue_simulation_runs(client):
    resp = client.post("/revenue/simulate", json={
        "festival_id": "name::lollapalooza",
        "capacity": 400000,
        "expected_attendance": 100000,
        "ticket_tiers": {"ga": {"price": 400, "share": 0.8}, "vip": {"price": 1200, "share": 0.2}},
        "vip_mix": 0.2,
        "sponsorship_commitments": 5000000,
        "per_capita_fnb_spending": 80,
        "per_capita_merch_spending": 40,
        "artist_cost_range": [8000000, 15000000],
        "production_costs": 4000000,
        "weather_assumption": "clear",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_revenue"] > 0
    assert body["p50_base_case"] is not None
