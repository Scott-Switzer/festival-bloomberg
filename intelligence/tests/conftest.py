"""
Shared pytest fixtures for the Festival Bloomberg test suite.

All tests run OFFLINE: the repository is backed by a temporary DuckDB file
seeded with deterministic sample data. No network access required.
"""
import os
import sys
from datetime import date

import pytest

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from warehouse.repository import FestivalRepository, reset_repository  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    """A fresh, seeded repository backed by a temp DuckDB file (offline)."""
    db_path = str(tmp_path / "test_festival.duckdb")
    r = FestivalRepository(db_path)

    # Seed festivals
    r.upsert_festival({
        "name": "Lollapalooza", "normalized_name": "lollapalooza",
        "location_city": "Chicago", "location_country": "US",
        "capacity": 400000, "genre_focus": ["rock", "hip-hop", "pop"],
        "festival_type": "music", "venue_type": "outdoor",
        "duration_days": 4, "typical_month": 8,
    })
    r.upsert_festival({
        "name": "Coachella", "normalized_name": "coachella",
        "location_city": "Indio", "location_country": "US",
        "capacity": 250000, "genre_focus": ["rock", "electronic"],
        "festival_type": "music", "venue_type": "outdoor",
        "duration_days": 6, "typical_month": 4,
    })

    # Seed artists with MusicBrainz IDs
    radiohead_key = r.upsert_artist({
        "name": "Radiohead", "normalized_name": "radiohead",
        "musicbrainz_id": "a74b1b7f-36a9-4d22-a1cf-017dc00396d0",
        "country": "GB", "genres": ["alternative rock", "art rock"], "type": "Group",
    })
    r.upsert_artist({
        "name": "Kendrick Lamar", "normalized_name": "kendrick lamar",
        "musicbrainz_id": "2c4ddd3c-dc27-4b78-a3a3-5f4a1c2e3b0f",
        "country": "US", "genres": ["hip-hop"], "type": "Person",
    })
    # Metric (point-in-time)
    r.insert_artist_metric(
        radiohead_key, "wikipedia", "pageviews_30d", 96903.0,
        observed_date=date(2026, 1, 1),
        meta_data={"window_days": 30},
    )
    r.insert_lineup_observation(
        "Radiohead", "name::lollapalooza", 2024, position="headliner",
        source_url="https://example.com/lolla2024", parser_version="1.0",
    )
    yield r
    r.close()


@pytest.fixture(autouse=True)
def _isolate_global_repo():
    """Ensure no singleton leaks across tests."""
    reset_repository()
    yield
    reset_repository()
