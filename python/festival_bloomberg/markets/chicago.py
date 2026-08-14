"""Strict Chicago city-proper geography.

Nearby markets (Rosemont, Tinley Park, Evanston) are not Chicago.
Search query strings are never evidence. Structured city/state/country from
the provider object is required.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

CHICAGO_MARKET_ID = "Chicago, IL"
CHICAGO_CITY = "chicago"
CHICAGO_STATE_CODES = frozenset({"IL", "ILLINOIS"})
CHICAGO_COUNTRY_CODES = frozenset({"US", "USA", "UNITED STATES"})
NEARBY_NOT_CHICAGO = frozenset(
    {
        "rosemont",
        "tinley park",
        "evanston",
        "oakbrook",
        "oak brook",
        "naperville",
        "schaumburg",
        "hoffman estates",
        "bridgeview",
    }
)


@dataclass(frozen=True)
class GeoAssignment:
    market_id: str | None
    method: str
    city: str | None = None
    state_code: str | None = None
    country_code: str | None = None

    @property
    def is_chicago(self) -> bool:
        return self.market_id == CHICAGO_MARKET_ID


def _fold(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_only.lower().strip().split())


def chicago_from_structured_geo(
    *,
    city: str | None,
    state_code: str | None = None,
    state: str | None = None,
    country_code: str | None = None,
    country: str | None = None,
) -> GeoAssignment:
    """Assign Chicago only from structured venue/city geography."""
    city_n = _fold(city)
    state_key = _fold(state_code or state).upper()
    country_key = _fold(country_code or country).upper()
    if city_n in NEARBY_NOT_CHICAGO:
        return GeoAssignment(
            market_id=None,
            method="NEARBY_MARKET_EXCLUDED",
            city=city,
            state_code=state_code or state,
            country_code=country_code or country,
        )
    if city_n != CHICAGO_CITY:
        return GeoAssignment(
            market_id=None,
            method="UNKNOWN",
            city=city,
            state_code=state_code or state,
            country_code=country_code or country,
        )
    if state_key not in CHICAGO_STATE_CODES:
        return GeoAssignment(
            market_id=None,
            method="UNKNOWN",
            city=city,
            state_code=state_code or state,
            country_code=country_code or country,
        )
    if country_key and country_key not in CHICAGO_COUNTRY_CODES:
        return GeoAssignment(
            market_id=None,
            method="UNKNOWN",
            city=city,
            state_code=state_code or state,
            country_code=country_code or country,
        )
    return GeoAssignment(
        market_id=CHICAGO_MARKET_ID,
        method="STRUCTURED_GEOGRAPHY",
        city=city,
        state_code=state_code or state,
        country_code=country_code or country,
    )
