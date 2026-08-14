"""Venue capacity enrichment from free sources.

Prioritizes: official venue pages > Wikidata > OSM > other public sources.
All sources are policy-gated and rights-decided before use.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from ..acquisition.contracts import AcquisitionRequest, AcquisitionStatus, utc_now
from ..acquisition.providers.openstreetmap import OpenStreetMapProvider
from ..acquisition.providers.wikidata import WikidataProvider
from ..acquisition.providers.wikipedia import WikipediaProvider, get_qid_for_venue
from .capacity import CapacityClaim, claims_from_osm, claim_from_wikipedia_infobox, claim_from_wikidata
from .repository import EconomicsRepository
from .venues import VenueResolver

# Source rights decisions
RIGHTS_OPEN_COMMERCIAL_OK = "OPEN_COMMERCIAL_OK"
RIGHTS_OPEN_WITH_ATTRIBUTION = "OPEN_WITH_ATTRIBUTION"
RIGHTS_TERMS_REVIEW_REQUIRED = "TERMS_REVIEW_REQUIRED"
RIGHTS_RESEARCH_ONLY = "RESEARCH_ONLY"
RIGHTS_UNKNOWN = "UNKNOWN"


class CapacityEnricher:
    """Enrich venue capacity claims from free sources."""
    
    def __init__(self, events_repo, economics_repo) -> None:
        self.events_repo = events_repo
        self.economics_repo = economics_repo
        self.venue_resolver = VenueResolver(events_repo, economics_repo)
        
        # Initialize providers
        self.wikidata_provider = WikidataProvider()
        self.wikipedia_provider = WikipediaProvider()
        self.osm_provider = OpenStreetMapProvider()
    
    def enrich_venue(self, venue_id: str, city: str = "Chicago", state: str = "IL") -> dict[str, Any]:
        """Enrich a single venue with capacity claims from all available sources."""
        
        venue = self._get_venue(venue_id)
        if not venue:
            return {"status": "venue_not_found", "venue_id": venue_id}
        
        claims_added = 0
        sources_used = []
        
        # 1. Try Wikipedia first (CC BY-SA, OPEN_WITH_ATTRIBUTION)
        wikipedia_claims = self._enrich_from_wikipedia(venue, venue_id)
        if wikipedia_claims:
            for claim in wikipedia_claims:
                if self.economics_repo.insert_capacity_claim(claim):
                    claims_added += 1
            sources_used.append("wikipedia")
        
        # 2. Try Wikidata (CC0, OPEN_COMMERCIAL_OK)
        wikidata_claims = self._enrich_from_wikidata(venue, venue_id)
        if wikidata_claims:
            for claim in wikidata_claims:
                if self.economics_repo.insert_capacity_claim(claim):
                    claims_added += 1
            sources_used.append("wikidata")
        
        # 3. Try OSM (ODbL, OPEN_WITH_ATTRIBUTION)
        osm_claims = self._enrich_from_osm(venue, venue_id)
        if osm_claims:
            for claim in osm_claims:
                if self.economics_repo.insert_capacity_claim(claim):
                    claims_added += 1
            sources_used.append("osm")
        
        # Record source rights decisions
        for source in sources_used:
            self._record_source_rights(source, venue_id)
        
        return {
            "status": "enriched",
            "venue_id": venue_id,
            "claims_added": claims_added,
            "sources_used": sources_used,
        }
    
    def enrich_chicago_venues(self, limit: int | None = None) -> dict[str, Any]:
        """Enrich all Chicago venues, prioritized by historical event count."""
        
        # Get Chicago venues
        venues = self.events_repo.conn.execute(
            """
            SELECT v.venue_id, v.venue_name, v.city, v.state, v.country,
                   COUNT(e.event_id) as event_count
            FROM events.venues v
            LEFT JOIN events.events e ON v.venue_id = e.venue_id
            WHERE LOWER(v.city) = 'chicago'
            AND v.superseded_by IS NULL
            GROUP BY v.venue_id, v.venue_name, v.city, v.state, v.country
            ORDER BY event_count DESC
            """
        ).fetchall()
        
        if limit:
            venues = venues[:limit]
        
        cols = [col[0] for col in self.events_repo.conn.description]
        venue_list = [dict(zip(cols, row)) for row in venues]
        
        results = []
        total_claims = 0
        
        for venue in venue_list:
            result = self.enrich_venue(
                venue["venue_id"],
                city=venue.get("city", "Chicago"),
                state=venue.get("state", "IL"),
            )
            results.append(result)
            total_claims += result.get("claims_added", 0)
        
        return {
            "status": "batch_enriched",
            "venues_processed": len(venue_list),
            "total_claims_added": total_claims,
            "results": results,
        }
    
    def _enrich_from_wikipedia(self, venue: dict[str, Any], venue_id: str) -> list[CapacityClaim]:
        """Enrich from Wikipedia infobox."""
        claims = []
        
        # Try known QID mapping first
        qid = get_qid_for_venue(venue_id)
        
        # Build search query
        query = venue.get("venue_name", "")
        market = f"{venue.get('city', '')}, {venue.get('state', '')}"
        
        request = AcquisitionRequest.new(
            entity_id=venue_id,
            entity_type="venue",
            platform="wikipedia",
            query=query,
            market_id=market,
            operation="search",
            max_cost_usd=0.0,
        )
        
        result = self.wikipedia_provider.acquire(request)
        
        if result.status == AcquisitionStatus.SUCCESS and result.records:
            for record in result.records:
                claim = claim_from_wikipedia_infobox(record, venue_id=venue_id)
                if claim:
                    claims.append(claim)
        
        return claims
    
    def _enrich_from_wikidata(self, venue: dict[str, Any], venue_id: str) -> list[CapacityClaim]:
        """Enrich from Wikidata P1083."""
        claims = []
        
        # Try known QID mapping first
        qid = get_qid_for_venue(venue_id)
        
        if not qid:
            # Search by venue name
            query = venue.get("venue_name", "")
            request = AcquisitionRequest.new(
                entity_id=venue_id,
                entity_type="venue",
                platform="wikidata",
                query=query,
                operation="search",
                max_cost_usd=0.0,
            )
            
            result = self.wikidata_provider.acquire(request)
            if result.status == AcquisitionStatus.SUCCESS and result.records:
                qid = result.records[0].get("wikidata_qid")
        
        if qid:
            # Fetch P1083 (capacity) property
            request = AcquisitionRequest.new(
                entity_id=venue_id,
                entity_type="venue",
                platform="wikidata",
                query=qid,
                operation="GET_ENTITY_CLAIMS",
                external_id=qid,
                max_cost_usd=0.0,
            )
            
            result = self.wikidata_provider.acquire(request)
            if result.status == AcquisitionStatus.SUCCESS and result.records:
                for record in result.records:
                    claim = claim_from_wikidata(record, venue_id=venue_id)
                    if claim:
                        claims.append(claim)
        
        return claims
    
    def _enrich_from_osm(self, venue: dict[str, Any], venue_id: str) -> list[CapacityClaim]:
        """Enrich from OpenStreetMap."""
        claims = []
        
        query = venue.get("venue_name", "")
        city = venue.get("city", "")
        
        # Try exact name match first
        request = AcquisitionRequest.new(
            entity_id=venue_id,
            entity_type="venue",
            platform="openstreetmap",
            query=query,
            market_id=city,
            operation="search",
            max_cost_usd=0.0,
        )
        
        result = self.osm_provider.acquire(request)
        
        if result.status == AcquisitionStatus.SUCCESS and result.records:
            for record in result.records:
                osm_claims = claims_from_osm(record, venue_id=venue_id)
                claims.extend(osm_claims)
        
        return claims
    
    def _get_venue(self, venue_id: str) -> dict[str, Any] | None:
        """Get venue by ID."""
        row = self.events_repo.conn.execute(
            "SELECT * FROM events.venues WHERE venue_id = ?",
            [venue_id],
        ).fetchone()
        if not row:
            return None
        cols = [col[0] for col in self.events_repo.conn.description]
        return dict(zip(cols, row))
    
    def _record_source_rights(self, source: str, venue_id: str) -> None:
        """Record source rights decision."""
        # Map sources to rights decisions
        rights_map = {
            "wikipedia": RIGHTS_OPEN_WITH_ATTRIBUTION,
            "wikidata": RIGHTS_OPEN_COMMERCIAL_OK,
            "osm": RIGHTS_OPEN_WITH_ATTRIBUTION,
        }
        
        rights_decision = rights_map.get(source, RIGHTS_UNKNOWN)
        
        # Check if already recorded
        existing = self.economics_repo.conn.execute(
            """
            SELECT decision_id FROM economics.source_rights_decisions
            WHERE source_name = ? AND source_url LIKE ?
            """,
            [source, f"%{venue_id}%"],
        ).fetchone()
        
        if existing:
            return
        
        # Record new decision
        decision_id = str(uuid4())
        self.economics_repo.conn.execute(
            """
            INSERT INTO economics.source_rights_decisions
                (decision_id, source_url, source_name, rights_decision,
                 decision_rationale, decided_at, decided_by, knowledge_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                decision_id,
                f"venue://{venue_id}",
                source,
                rights_decision,
                f"Capacity enrichment for venue {venue_id}",
                utc_now().isoformat(),
                "forward_market_history_v1",
                utc_now().isoformat(),
            ],
        )
        self.economics_repo.conn.commit()
