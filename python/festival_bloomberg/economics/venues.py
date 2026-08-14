"""Venue identity resolution hierarchy for canonical venue master.

Deterministic venue resolution rules without fuzzy auto-merging.
Hierarchy: EXACT_EXTERNAL_ID > EXACT_CANONICAL_MAPPING > EXACT_NORMALIZED_NAME + CITY
> COORDINATE_MATCH + name compatibility > ALIAS_MATCH > FUZZY_REVIEW_REQUIRED > UNRESOLVED.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..acquisition.contracts import utc_now


# Resolution methods
RESOLUTION_EXACT_EXTERNAL_ID = "EXACT_EXTERNAL_ID"
RESOLUTION_EXACT_CANONICAL_MAPPING = "EXACT_CANONICAL_MAPPING"
RESOLUTION_EXACT_NAME_CITY = "EXACT_NAME_CITY"
RESOLUTION_COORDINATE_MATCH = "COORDINATE_MATCH"
RESOLUTION_ALIAS_MATCH = "ALIAS_MATCH"
RESOLUTION_FUZZY_REVIEW_REQUIRED = "FUZZY_REVIEW_REQUIRED"
RESOLUTION_UNRESOLVED = "UNRESOLVED"

# Known sponsor prefixes for alias matching
SPONSOR_PREFIXES = [
    "byline bank",
    "huntington bank",
    "first midwest bank",
    "allstate",
    "wrigley",
    "soldier field",
    "united",
]


def normalize_venue_name(name: str) -> str:
    """Normalize venue name for comparison."""
    if not name:
        return ""
    # Lowercase, strip extra whitespace
    normalized = " ".join(name.lower().split())
    # Remove leading "the "
    if normalized.startswith("the "):
        normalized = normalized[4:]
    return normalized


def strip_sponsor_prefix(name: str) -> str:
    """Strip known sponsor prefixes for alias matching."""
    normalized = normalize_venue_name(name)
    for prefix in SPONSOR_PREFIXES:
        if normalized.startswith(prefix + " "):
            return normalized[len(prefix) + 1:]
    return normalized


class VenueResolver:
    """Deterministic venue identity resolution."""
    
    def __init__(self, events_repo, economics_repo) -> None:
        self.events_repo = events_repo
        self.economics_repo = economics_repo
        self.coordinate_tolerance_meters = 150
    
    def resolve_venue_identity(
        self,
        venue_name: str,
        city: str,
        state: str | None = None,
        country: str = "US",
        ticketmaster_venue_id: str | None = None,
        setlistfm_venue_id: str | None = None,
        wikidata_qid: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict[str, Any]:
        """Resolve venue to canonical ID using hierarchy."""
        
        # 1. EXACT_EXTERNAL_ID - highest priority
        if ticketmaster_venue_id:
            existing = self._find_by_external_id("ticketmaster_venue_id", ticketmaster_venue_id)
            if existing:
                return {
                    "canonical_venue_id": existing["venue_id"],
                    "resolution_method": RESOLUTION_EXACT_EXTERNAL_ID,
                    "confidence": 1.0,
                }
        
        if setlistfm_venue_id:
            existing = self._find_by_external_id("setlistfm_venue_id", setlistfm_venue_id)
            if existing:
                return {
                    "canonical_venue_id": existing["venue_id"],
                    "resolution_method": RESOLUTION_EXACT_EXTERNAL_ID,
                    "confidence": 1.0,
                }
        
        if wikidata_qid:
            existing = self._find_by_external_id("wikidata_qid", wikidata_qid)
            if existing:
                return {
                    "canonical_venue_id": existing["venue_id"],
                    "resolution_method": RESOLUTION_EXACT_EXTERNAL_ID,
                    "confidence": 1.0,
                }
        
        # 2. EXACT_CANONICAL_MAPPING - check known mappings
        canonical_mapping = self._check_canonical_mapping(venue_name, city, state, country)
        if canonical_mapping:
            return {
                "canonical_venue_id": canonical_mapping,
                "resolution_method": RESOLUTION_EXACT_CANONICAL_MAPPING,
                "confidence": 1.0,
            }
        
        # 3. EXACT_NORMALIZED_NAME + CITY
        exact_match = self._find_by_exact_name_city(venue_name, city, state, country)
        if exact_match:
            return {
                "canonical_venue_id": exact_match["venue_id"],
                "resolution_method": RESOLUTION_EXACT_NAME_CITY,
                "confidence": 1.0,
            }
        
        # 4. COORDINATE_MATCH + name compatibility
        if latitude is not None and longitude is not None:
            coord_match = self._find_by_coordinate_match(
                venue_name, city, state, country, latitude, longitude
            )
            if coord_match:
                return {
                    "canonical_venue_id": coord_match["venue_id"],
                    "resolution_method": RESOLUTION_COORDINATE_MATCH,
                    "confidence": 0.9,
                }
        
        # 5. ALIAS_MATCH - sponsor prefix stripping
        alias_match = self._find_by_alias_match(venue_name, city, state, country)
        if alias_match:
            return {
                "canonical_venue_id": alias_match["venue_id"],
                "resolution_method": RESOLUTION_ALIAS_MATCH,
                "confidence": 0.8,
            }
        
        # No match found
        return {
            "canonical_venue_id": None,
            "resolution_method": RESOLUTION_UNRESOLVED,
            "confidence": 0.0,
        }
    
    def merge_venues(
        self,
        source_venue_ids: list[str],
        target_canonical_venue_id: str,
        resolution_method: str,
        supporting_observations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Merge venue identities with audit trail."""
        
        merge_action_id = str(uuid4())
        now = utc_now()
        
        # Record merge action
        self.economics_repo.conn.execute(
            """
            INSERT INTO economics.venue_merge_actions
                (merge_action_id, source_venue_ids, target_canonical_venue_id,
                 resolution_method, supporting_observations_json, merged_at,
                 software_version, knowledge_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                merge_action_id,
                json.dumps(source_venue_ids),
                target_canonical_venue_id,
                resolution_method,
                json.dumps(supporting_observations or []),
                now.isoformat(),
                "forward_market_history_v1",
                now.isoformat(),
            ],
        )
        
        # Create venue aliases
        for source_id in source_venue_ids:
            if source_id == target_canonical_venue_id:
                continue
            
            # Get source venue details
            source_venue = self._get_venue_by_id(source_id)
            if not source_venue:
                continue
            
            alias_id = str(uuid4())
            self.economics_repo.conn.execute(
                """
                INSERT INTO economics.venue_aliases
                    (alias_id, canonical_venue_id, alias_venue_id, alias_name,
                     alias_provider, alias_provider_venue_id, superseded_at,
                     superseded_by, knowledge_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    alias_id,
                    target_canonical_venue_id,
                    source_id,
                    source_venue.get("venue_name"),
                    source_venue.get("provider"),
                    source_venue.get("provider_venue_id"),
                    now.isoformat(),
                    merge_action_id,
                    now.isoformat(),
                ],
            )
            
            # Mark source as superseded
            self.events_repo.conn.execute(
                "UPDATE events.venues SET superseded_by = ?, canonical_method = ? WHERE venue_id = ?",
                [target_canonical_venue_id, resolution_method, source_id],
            )
        
        # Merge provider IDs into canonical venue
        for source_id in source_venue_ids:
            if source_id == target_canonical_venue_id:
                continue
            
            source_venue = self._get_venue_by_id(source_id)
            if not source_venue:
                continue
            
            # Copy provider IDs if canonical doesn't have them
            if source_venue.get("ticketmaster_venue_id") and not self._canonical_has_provider_id(
                target_canonical_venue_id, "ticketmaster_venue_id"
            ):
                self.events_repo.conn.execute(
                    "UPDATE events.venues SET ticketmaster_venue_id = ? WHERE venue_id = ?",
                    [source_venue["ticketmaster_venue_id"], target_canonical_venue_id],
                )
            
            if source_venue.get("setlistfm_venue_id") and not self._canonical_has_provider_id(
                target_canonical_venue_id, "setlistfm_venue_id"
            ):
                self.events_repo.conn.execute(
                    "UPDATE events.venues SET setlistfm_venue_id = ? WHERE venue_id = ?",
                    [source_venue["setlistfm_venue_id"], target_canonical_venue_id],
                )
        
        self.economics_repo.conn.commit()
        self.events_repo.conn.commit()
        
        return {
            "merge_action_id": merge_action_id,
            "source_venue_ids": source_venue_ids,
            "target_canonical_venue_id": target_canonical_venue_id,
            "resolution_method": resolution_method,
            "merged_at": now.isoformat(),
        }
    
    def _find_by_external_id(self, id_field: str, external_id: str) -> dict[str, Any] | None:
        """Find venue by external ID."""
        row = self.events_repo.conn.execute(
            f"SELECT * FROM events.venues WHERE {id_field} = ?",
            [external_id],
        ).fetchone()
        if not row:
            return None
        cols = [col[0] for col in self.events_repo.conn.description]
        return dict(zip(cols, row))
    
    def _check_canonical_mapping(
        self, venue_name: str, city: str, state: str | None, country: str
    ) -> str | None:
        """Check known canonical mappings (hardcoded for major venues)."""
        # Known exact canonical mappings for Chicago venues
        canonical_mappings = {
            ("united center", "chicago", "il", "us"): "venue_united_center_chicago",
            ("soldier field", "chicago", "il", "us"): "venue_soldier_field_chicago",
            ("wrigley field", "chicago", "il", "us"): "venue_wrigley_field_chicago",
            ("metro", "chicago", "il", "us"): "venue_metro_chicago",
            ("chicago theatre", "chicago", "il", "us"): "venue_chicago_theatre",
            ("aragon ballroom", "chicago", "il", "us"): "venue_aragon_ballroom",
            ("riviera theatre", "chicago", "il", "us"): "venue_riviera_theatre",
        }
        
        key = (normalize_venue_name(venue_name), normalize_venue_name(city), 
               normalize_venue_name(state or ""), normalize_venue_name(country))
        return canonical_mappings.get(key)
    
    def _find_by_exact_name_city(
        self, venue_name: str, city: str, state: str | None, country: str
    ) -> dict[str, Any] | None:
        """Find venue by exact normalized name and city."""
        normalized_name = normalize_venue_name(venue_name)
        normalized_city = normalize_venue_name(city)
        
        rows = self.events_repo.conn.execute(
            """
            SELECT * FROM events.venues 
            WHERE LOWER(venue_name) = ? 
            AND LOWER(city) = ?
            AND superseded_by IS NULL
            """,
            [normalized_name, normalized_city],
        ).fetchall()
        
        if not rows:
            return None
        
        cols = [col[0] for col in self.events_repo.conn.description]
        return dict(zip(cols, rows[0]))
    
    def _find_by_coordinate_match(
        self,
        venue_name: str,
        city: str,
        state: str | None,
        country: str,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any] | None:
        """Find venue by coordinate proximity and name compatibility."""
        # Simple distance check (not geodesic for simplicity)
        rows = self.events_repo.conn.execute(
            """
            SELECT * FROM events.venues 
            WHERE latitude IS NOT NULL 
            AND longitude IS NOT NULL
            AND superseded_by IS NULL
            AND LOWER(city) = ?
            """,
            [normalize_venue_name(city)],
        ).fetchall()
        
        if not rows:
            return None
        
        cols = [col[0] for col in self.events_repo.conn.description]
        normalized_name = normalize_venue_name(venue_name)
        
        for row in rows:
            venue = dict(zip(cols, row))
            lat_diff = abs(venue["latitude"] - latitude)
            lon_diff = abs(venue["longitude"] - longitude)
            
            # Rough distance check (not accurate but sufficient for proximity)
            if lat_diff < 0.002 and lon_diff < 0.002:  # ~150m tolerance
                # Check name compatibility
                venue_normalized = normalize_venue_name(venue.get("venue_name", ""))
                if normalized_name in venue_normalized or venue_normalized in normalized_name:
                    return venue
        
        return None
    
    def _find_by_alias_match(
        self, venue_name: str, city: str, state: str | None, country: str
    ) -> dict[str, Any] | None:
        """Find venue by alias (sponsor prefix stripping)."""
        stripped_name = strip_sponsor_prefix(venue_name)
        if stripped_name == normalize_venue_name(venue_name):
            return None  # No sponsor prefix to strip
        
        # Try to find match with stripped name
        return self._find_by_exact_name_city(stripped_name, city, state, country)
    
    def _get_venue_by_id(self, venue_id: str) -> dict[str, Any] | None:
        """Get venue by ID."""
        row = self.events_repo.conn.execute(
            "SELECT * FROM events.venues WHERE venue_id = ?",
            [venue_id],
        ).fetchone()
        if not row:
            return None
        cols = [col[0] for col in self.events_repo.conn.description]
        return dict(zip(cols, row))
    
    def _canonical_has_provider_id(self, canonical_venue_id: str, provider_id_field: str) -> bool:
        """Check if canonical venue already has a provider ID."""
        row = self.events_repo.conn.execute(
            f"SELECT {provider_id_field} FROM events.venues WHERE venue_id = ?",
            [canonical_venue_id],
        ).fetchone()
        if not row:
            return False
        return row[0] is not None


def merge_united_center(events_repo, economics_repo) -> dict[str, Any]:
    """Merge United Center duplicate venues (Ticketmaster vs Setlist).

    Idempotent: venues already superseded are skipped, so repeated OA runs do
    not create duplicate merge actions or aliases. Returns ``already_merged``
    when the graph is already canonical.
    """
    
    resolver = VenueResolver(events_repo, economics_repo)
    
    # Find United Center venues
    united_center_venues = []
    rows = events_repo.conn.execute(
        """
        SELECT * FROM events.venues 
        WHERE LOWER(venue_name) LIKE '%united center%'
        AND LOWER(city) = 'chicago'
        """
    ).fetchall()
    
    if not rows:
        return {"status": "no_united_center_found"}
    
    cols = [col[0] for col in events_repo.conn.description]
    for row in rows:
        united_center_venues.append(dict(zip(cols, row)))
    
    # Ignore rows already superseded by a previous merge (idempotency).
    active = [v for v in united_center_venues if not v.get("superseded_by")]
    if len(active) < 2:
        canonical = next(
            (v for v in active if not v.get("superseded_by")),
            united_center_venues[0] if united_center_venues else None,
        )
        return {
            "status": "already_merged" if len(active) == 1 else "only_one_united_center",
            "canonical_venue_id": canonical["venue_id"] if canonical else None,
            "venues": _json_safe_venues(united_center_venues),
        }
    
    # Select canonical among ACTIVE rows (prefer one with more provider IDs)
    canonical = max(active, key=lambda v: sum(
        1 for field in ["ticketmaster_venue_id", "setlistfm_venue_id", "wikidata_qid"]
        if v.get(field)
    ))
    
    source_ids = [v["venue_id"] for v in active if v["venue_id"] != canonical["venue_id"]]
    
    # Perform merge
    result = resolver.merge_venues(
        source_venue_ids=source_ids,
        target_canonical_venue_id=canonical["venue_id"],
        resolution_method=RESOLUTION_EXACT_NAME_CITY,
        supporting_observations=[
            {"venue_name": v["venue_name"], "provider": v.get("provider")}
            for v in active
        ],
    )
    
    return {
        "status": "merged",
        "canonical_venue_id": canonical["venue_id"],
        "source_venue_ids": source_ids,
        "merge_action_id": result["merge_action_id"],
    }


def _json_safe_venues(venues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce datetime values to ISO strings so results are JSON-serializable."""
    from datetime import date, datetime as _dt

    out = []
    for venue in venues:
        row = dict(venue)
        for key, value in list(row.items()):
            if isinstance(value, (_dt, date)):
                row[key] = value.isoformat()
        out.append(row)
    return out
