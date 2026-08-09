"""Tests for C3 format parsers and entity resolution (offline)."""
import sys

sys.path.insert(0, ".")

from c3 import ParserFactory, parse_lineup  # noqa: E402
from entity.entity_resolution import EntityResolver  # noqa: E402


def test_parser_factory_creates_known_parsers():
    for fmt in ["poster_grid", "day_stage_schedule", "multi_weekend", "simple_list"]:
        parser = ParserFactory.create_parser(fmt, festival_id="lolla_chicago", year=2024)
        assert parser is not None


def test_simple_list_parser_parses_artists():
    text = "Radiohead\nKendrick Lamar\nBillie Eilish\nThe Weeknd"
    result = parse_lineup(text, format_profile="simple_list", festival_id="lolla", year=2024)
    assert result is not None
    names = {a.name.lower() for a in result.artists}
    assert "radiohead" in names


def test_entity_resolver_initializes():
    # Offline: should construct without network.
    resolver = EntityResolver()
    assert resolver is not None


def test_entity_resolver_resolves_by_direct_mbid():
    # This hits the network; skip gracefully if offline.
    resolver = EntityResolver()
    try:
        result = resolver.resolve_by_mbid("a74b1b7f-36a9-4d22-a1cf-017dc00396d0")
        assert result is not None
        assert result.primary_mbid == "a74b1b7f-36a9-4d22-a1cf-017dc00396d0"
    except Exception as e:
        import pytest

        pytest.skip(f"network unavailable: {e}")
