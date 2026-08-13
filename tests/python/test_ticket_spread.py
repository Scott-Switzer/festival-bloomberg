"""
Ticket secondary-market spread tests (moved into tests/python/ for CI coverage).

These tests cover the seatgeek adapter immutability contract and the
spread_calculator FX/fee/timestamp logic. They run offline with no network
access.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D

from metrics.spread_calculator import FXTable, SpreadResult, calculate_spread
from scraper.seatgeek_adapter import SeatGeekAdapter


# Primary ticket baseline used across tests
_PRIMARY = {
    "currency": "USD",
    "total_primary_price_minor": 10000,
    "fee_components_minor": 1000,
    "created_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
}


def _secondary(**overrides):
    """Build a secondary listing dict with sensible defaults."""
    return {
        "currency": "USD",
        "total_buyer_price_minor": 14000,
        "fee_components_minor": 2000,
        "retrieved_at": datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
        **overrides,
    }


def test_same_currency():
    r = calculate_spread(_PRIMARY, _secondary(), fx=FXTable({}))
    assert r.absolute_spread_minor == 4000
    assert r.percentage_spread == D("0.4")


def test_fx_and_fallback():
    fx_with_rate = FXTable({("2026-08-10", "EUR", "USD"): D("1.1")})
    r = calculate_spread(_PRIMARY, _secondary(currency="EUR"), fx=fx_with_rate)
    assert r.absolute_spread_minor == 5400

    fx_empty = FXTable({})
    r2 = calculate_spread(_PRIMARY, _secondary(currency="EUR"), fx=fx_empty)
    assert "FX_FALLBACK" in r2.quality_flags
    assert not r2.arbitrage_candidate


def test_limits_missing_and_unknown_fees():
    stale = calculate_spread(
        _PRIMARY,
        _secondary(retrieved_at=_PRIMARY["created_at"] + timedelta(hours=25)),
        fx=FXTable({}),
        mode="historical",
    )
    assert "TIMESTAMP_OUT_OF_TOLERANCE" in stale.quality_flags

    missing_price = calculate_spread(
        {**_PRIMARY, "total_primary_price_minor": None},
        _secondary(),
        fx=FXTable({}),
    )
    assert "MISSING_PRICE" in missing_price.quality_flags

    missing_fees = calculate_spread(
        _PRIMARY,
        _secondary(fee_components_minor=None),
        fx=FXTable({}),
    )
    assert not missing_fees.arbitrage_candidate


def test_immutable_changed_listing():
    adapter = SeatGeekAdapter()
    raw = {
        "event_id": "e",
        "listing_id": "l",
        "listing_url": "u",
        "title": "VIP",
        "total_buyer_price_minor": 1,
    }
    x = adapter.snapshot(raw)
    y = adapter.snapshot({**raw, "total_buyer_price_minor": 2})
    assert x.content_hash != y.content_hash
    assert x.total_buyer_price_minor == 1
