"""Wikipedia provider for venue capacity extraction.

Uses MediaWiki API to fetch venue pages and extract capacity from infoboxes.
Source: Wikipedia (CC BY-SA 3.0) - OPEN_WITH_ATTRIBUTION.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from ..base import BaseProvider
from ..contracts import AcquisitionRequest, AcquisitionStatus, AcquisitionResult


WIKIPEDIA_API_BASE = "https://en.wikipedia.org/w/api.php"


def extract_capacity_from_infobox(wikitext: str) -> list[dict[str, Any]]:
    """Extract capacity values from Wikipedia infobox wikitext.
    
    Looks for patterns like:
    | capacity = 23,500
    | seating_capacity = 20,917
    | capacity = 23,500 (concert)
    """
    claims = []
    
    # Common capacity field patterns
    patterns = [
        r'\|\s*capacity\s*=\s*([0-9,]+)(?:\s*\([^)]+\))?',
        r'\|\s*seating_capacity\s*=\s*([0-9,]+)(?:\s*\([^)]+\))?',
        r'\|\s*seats\s*=\s*([0-9,]+)(?:\s*\([^)]+\))?',
        r'\|\s*concert_capacity\s*=\s*([0-9,]+)(?:\s*\([^)]+\))?',
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, wikitext, re.IGNORECASE)
        for match in matches:
            value_str = match.group(1).replace(',', '')
            try:
                value = float(value_str)
                claims.append({
                    "capacity_value": value,
                    "capacity_kind": "UNKNOWN",
                    "source_field": match.group(0).split('=')[0].strip(),
                })
            except (ValueError, IndexError):
                continue
    
    # Try to determine capacity kind from context
    for claim in claims:
        field = claim.get("source_field", "").lower()
        if "seating" in field or "seats" in field:
            claim["capacity_kind"] = "SEATED"
        elif "concert" in field:
            claim["capacity_kind"] = "CONCERT"
        elif "standing" in field:
            claim["capacity_kind"] = "STANDING"
        else:
            claim["capacity_kind"] = "MAX_PERSONS"
    
    return claims


class WikipediaProvider(BaseProvider):
    """Wikipedia provider for venue capacity data."""
    
    def configured(self) -> bool:
        """Wikipedia requires no authentication."""
        return True
    
    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        """Fetch venue page from Wikipedia and extract capacity."""
        
        started_at = self._now()
        
        if request.entity_type != "venue":
            return AcquisitionResult(
                request_id=request.request_id,
                provider="wikipedia_mediawiki_api",
                provider_endpoint=None,
                status=AcquisitionStatus.PROVIDER_ERROR,
                started_at=started_at,
                completed_at=self._now(),
                records=[],
                provider_metadata={"rationale": "Wikipedia provider only supports venue entity_type"},
                cost_usd=0.0,
            )
        
        # Search for venue page
        search_query = f"{request.query} {request.market_id}" if request.market_id else request.query
        search_url = self._build_search_url(search_query)
        
        try:
            search_response = self._http_get(search_url)
            if search_response.get("error"):
                return AcquisitionResult(
                    request_id=request.request_id,
                    provider="wikipedia_mediawiki_api",
                    provider_endpoint=search_url,
                    status=AcquisitionStatus.PROVIDER_ERROR,
                    started_at=started_at,
                    completed_at=self._now(),
                    records=[],
                    provider_metadata={"error": search_response.get("error")},
                    cost_usd=0.0,
                )
            
            # Get page title from search results
            page_title = self._extract_page_title(search_response)
            if not page_title:
                return AcquisitionResult(
                    request_id=request.request_id,
                    provider="wikipedia_mediawiki_api",
                    provider_endpoint=search_url,
                    status=AcquisitionStatus.NO_RESULTS,
                    started_at=started_at,
                    completed_at=self._now(),
                    records=[],
                    provider_metadata={"rationale": "No Wikipedia page found"},
                    cost_usd=0.0,
                )
            
            # Fetch page content
            page_url = self._build_page_url(page_title)
            page_response = self._http_get(page_url)
            
            if page_response.get("error"):
                return AcquisitionResult(
                    request_id=request.request_id,
                    provider="wikipedia_mediawiki_api",
                    provider_endpoint=page_url,
                    status=AcquisitionStatus.PROVIDER_ERROR,
                    started_at=started_at,
                    completed_at=self._now(),
                    records=[],
                    provider_metadata={"error": page_response.get("error")},
                    cost_usd=0.0,
                )
            
            # Extract capacity from infobox
            wikitext = self._extract_wikitext(page_response)
            capacity_claims = extract_capacity_from_infobox(wikitext)
            
            # Get Wikidata QID if available
            wikidata_qid = self._extract_wikibase_item(page_response)
            
            records = []
            for claim in capacity_claims:
                record = {
                    "entity_id": request.entity_id,
                    "entity_type": "venue",
                    "platform": "wikipedia",
                    "capacity_value": claim["capacity_value"],
                    "capacity_kind": claim["capacity_kind"],
                    "source_field": claim["source_field"],
                    "source_url": f"https://en.wikipedia.org/wiki/{quote(page_title, safe='')}",
                    "wikidata_qid": wikidata_qid,
                    "page_title": page_title,
                    "retrieved_at": request.knowledge_cutoff or self._now(),
                }
                records.append(record)
            
            return AcquisitionResult(
                request_id=request.request_id,
                provider="wikipedia_mediawiki_api",
                provider_endpoint=page_url,
                status=AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS,
                started_at=started_at,
                completed_at=self._now(),
                record_count=len(records),
                records=tuple(records),
                provider_metadata={
                    "page_title": page_title,
                    "wikidata_qid": wikidata_qid,
                    "claims_found": len(records),
                },
                cost_usd=0.0,
            )
            
        except Exception as e:
            return AcquisitionResult(
                request_id=request.request_id,
                provider="wikipedia_mediawiki_api",
                provider_endpoint=search_url,
                status=AcquisitionStatus.PROVIDER_ERROR,
                started_at=started_at,
                completed_at=self._now(),
                records=[],
                provider_metadata={"error": str(e)},
                cost_usd=0.0,
            )
    
    def _build_search_url(self, query: str) -> str:
        """Build Wikipedia search API URL.

        Quotes the leading venue name so the search is phrase-anchored rather
        than a free keyword query ("United Center Chicago" must not rank the
        city of Chicago first).
        """
        parts = query.split()
        if parts:
            phrase = "\"" + parts[0] + "\""
            if len(parts) > 1:
                phrase += " " + " ".join(parts[1:])
        else:
            phrase = query
        params = {
            "action": "query",
            "list": "search",
            "srsearch": phrase,
            "format": "json",
            "srlimit": "5",
        }
        param_str = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        return f"{WIKIPEDIA_API_BASE}?{param_str}"
    
    def _build_page_url(self, page_title: str) -> str:
        """Build Wikipedia page API URL."""
        params = {
            "action": "query",
            "prop": "revisions|pageprops",
            "rvprop": "content",
            "rvslots": "main",
            "ppprop": "wikibase_item",
            "format": "json",
            "titles": page_title,
        }
        param_str = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        return f"{WIKIPEDIA_API_BASE}?{param_str}"
    
    def _http_get(self, url: str) -> dict[str, Any]:
        """HTTP GET through the canonical injected transport.

        Uses the same :class:`HttpTransport` as every other provider so the
        Wikipedia capacity path is testable with a fake transport and fails
        closed on network errors without a second networking stack.
        """
        try:
            response = self.transport.request(
                "GET",
                url,
                headers={"User-Agent": "FestivalBloomberg/0.1 (research; venue-capacity-claims)"},
                timeout_seconds=30.0,
            )
        except Exception as exc:  # TransportError and friends
            return {"error": f"network failure: {exc}"}
        if response.status != 200:
            return {"error": f"http {response.status}"}
        try:
            return response.json()
        except (ValueError, TypeError):
            return {"error": "response not json"}
    
    def _extract_page_title(self, search_response: dict[str, Any]) -> str | None:
        """Extract page title from search results."""
        query = search_response.get("query", {})
        search = query.get("search", [])
        if search:
            return search[0].get("title")
        return None

    @staticmethod
    def _extract_wikitext(page_response: dict[str, Any]) -> str:
        """Extract wikitext from an action=query revisions response."""
        pages = (page_response.get("query") or {}).get("pages") or {}
        first = next(iter(pages.values()), {}) if isinstance(pages, dict) else (pages[0] if pages else {})
        revisions = first.get("revisions") or []
        revision = revisions[0] if revisions else {}
        slots = revision.get("slots") or {}
        main = slots.get("main") or {}
        return str(main.get("content") or "")

    @staticmethod
    def _extract_wikibase_item(page_response: dict[str, Any]) -> str | None:
        """Extract the wikibase item (QID) from pageprops if present."""
        pages = (page_response.get("query") or {}).get("pages") or {}
        first = next(iter(pages.values()), {}) if isinstance(pages, dict) else (pages[0] if pages else {})
        props = first.get("pageprops") or {}
        return props.get("wikibase_item")
    
    def _now(self) -> str:
        """Current timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


# Known Chicago venue QID mappings for exact canonical resolution.
# Verified against live Wikidata wbsearchentities (2026-08-14):
#   United Center -> Q639975, Wrigley Field -> Q1052807, Soldier Field -> Q1132413
CHICAGO_VENUE_QIDS = {
    "venue_united_center_chicago": "Q639975",
    "venue_soldier_field_chicago": "Q1132413",
    "venue_wrigley_field_chicago": "Q1052807",
    "venue_metro_chicago": "Q6389880",
    "chicago_theatre": "Q5587495",
    "venue_aragon_ballroom": "Q5586376",
    "venue_riviera_theatre": "Q5587380",
}


def get_qid_for_venue(canonical_venue_id: str) -> str | None:
    """Get Wikidata QID for a canonical venue ID."""
    return CHICAGO_VENUE_QIDS.get(canonical_venue_id)
