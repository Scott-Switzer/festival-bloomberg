"""
MusicBrainz API pipeline for canonical artist identity and metadata.
MusicBrainz is the primary source for artist identity resolution.
"""

import requests
import time
from typing import Optional, Dict, List, Any
from datetime import datetime
from pydantic import BaseModel, Field


class MusicBrainzClient(BaseModel):
    """MusicBrainz API client with rate limiting"""
    base_url: str = "https://musicbrainz.org/ws/2/"
    user_agent: str = Field(..., description="User-Agent string (required by MusicBrainz)")
    rate_limit_delay: float = Field(default=1.0, ge=0.5, description="Delay between requests in seconds")
    
    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch artist data by MusicBrainz ID.
        
        Args:
            artist_id: MusicBrainz artist ID (e.g., "f7d31c5f-c712-4603-8eb4-3b0b846c4f3c")
        
        Returns:
            Artist data dictionary or None if not found
        """
        url = f"{self.base_url}artist/{artist_id}"
        params = {"fmt": "json", "inc": "aliases+releases+tags"}
        headers = {"User-Agent": self.user_agent}
        
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching artist {artist_id}: {e}")
            return None
    
    def search_artist(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for artists by name.
        
        Args:
            name: Artist name to search
            limit: Maximum number of results
        
        Returns:
            List of artist search results
        """
        url = f"{self.base_url}artist/"
        params = {
            "query": f'artist:"{name}"',
            "fmt": "json",
            "limit": limit
        }
        headers = {"User-Agent": self.user_agent}
        
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json().get("artists", [])
        except requests.RequestException as e:
            print(f"Error searching for artist {name}: {e}")
            return []
    
    def get_artist_releases(self, artist_id: str, release_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch releases for an artist.
        
        Args:
            artist_id: MusicBrainz artist ID
            release_type: Filter by release type (album, single, ep, etc.)
        
        Returns:
            List of release dictionaries
        """
        url = f"{self.base_url}release"
        params = {
            "query": f'arid:{artist_id}',
            "fmt": "json",
            "limit": 100
        }
        if release_type:
            params["type"] = release_type
        
        headers = {"User-Agent": self.user_agent}
        
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json().get("releases", [])
        except requests.RequestException as e:
            print(f"Error fetching releases for artist {artist_id}: {e}")
            return []
    
    def get_artist_tags(self, artist_id: str) -> List[Dict[str, Any]]:
        """
        Fetch genre tags for an artist.
        
        Args:
            artist_id: MusicBrainz artist ID
        
        Returns:
            List of tag dictionaries with count
        """
        url = f"{self.base_url}artist/{artist_id}/tags"
        params = {"fmt": "json"}
        headers = {"User-Agent": self.user_agent}
        
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json().get("tags", [])
        except requests.RequestException as e:
            print(f"Error fetching tags for artist {artist_id}: {e}")
            return []


def normalize_artist_name(name: str) -> str:
    """
    Normalize artist name for comparison.
    
    Args:
        name: Raw artist name
    
    Returns:
        Normalized name (lowercase, stripped, special chars removed)
    """
    import re
    # Convert to lowercase
    normalized = name.lower().strip()
    # Remove special characters except apostrophes and hyphens
    normalized = re.sub(r"[^a-z0-9'\- ]", "", normalized)
    # Remove extra spaces
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def extract_artist_data(mb_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract relevant artist data from MusicBrainz response.
    
    Args:
        mb_data: Raw MusicBrainz artist data
    
    Returns:
        Dictionary with extracted artist fields
    """
    return {
        "musicbrainz_id": mb_data.get("id"),
        "normalized_name": normalize_artist_name(mb_data.get("name", "")),
        "name": mb_data.get("name"),
        "aliases": [alias.get("name") for alias in mb_data.get("aliases", [])],
        "country": mb_data.get("country"),
        "type": mb_data.get("type"),
        "gender": mb_data.get("gender"),
        "begin_area": mb_data.get("begin-area", {}).get("name") if mb_data.get("begin-area") else None,
        "area": mb_data.get("area", {}).get("name") if mb_data.get("area") else None,
        "life_span_begin": mb_data.get("life-span", {}).get("begin"),
        "life_span_end": mb_data.get("life-span", {}).get("end"),
        "is_single_artist": mb_data.get("type") == "Person",
        "is_group": mb_data.get("type") == "Group",
    }
