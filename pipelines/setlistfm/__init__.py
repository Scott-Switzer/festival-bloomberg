"""
setlist.fm API pipeline for concert history and setlist data.
"""

import requests
import time
from typing import Optional, Dict, List, Any
from datetime import datetime
from pydantic import BaseModel, Field


class SetlistFMClient(BaseModel):
    """setlist.fm API client"""
    base_url: str = "https://api.setlist.fm/rest/1.0/"
    api_key: str = Field(..., description="setlist.fm API key")
    rate_limit_delay: float = Field(default=1.0, ge=0.5, description="Delay between requests")
    
    def get_artist_setlists(self, artist_mbid: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Fetch setlists for an artist by MusicBrainz ID.
        
        Args:
            artist_mbid: MusicBrainz artist ID
            limit: Maximum number of setlists to fetch
        
        Returns:
            List of setlist dictionaries
        """
        url = f"{self.base_url}artist/{artist_mbid}/setlists"
        headers = {
            "Accept": "application/json",
            "x-api-key": self.api_key
        }
        params = {"p": 1}  # Page 1
        
        all_setlists = []
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            data = response.json()
            
            setlists = data.get("setlist", [])
            all_setlists.extend(setlists[:limit])
            
            return all_setlists
        except requests.RequestException as e:
            print(f"Error fetching setlists for artist {artist_mbid}: {e}")
            return []
    
    def get_artist_info(self, artist_mbid: str) -> Optional[Dict[str, Any]]:
        """
        Fetch artist information from setlist.fm.
        
        Args:
            artist_mbid: MusicBrainz artist ID
        
        Returns:
            Artist info dictionary or None
        """
        url = f"{self.base_url}artist/{artist_mbid}"
        headers = {
            "Accept": "application/json",
            "x-api-key": self.api_key
        }
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching artist info for {artist_mbid}: {e}")
            return None
    
    def search_artist(self, name: str) -> List[Dict[str, Any]]:
        """
        Search for artists by name.
        
        Args:
            name: Artist name to search
        
        Returns:
            List of artist search results
        """
        url = f"{self.base_url}search/artists"
        headers = {
            "Accept": "application/json",
            "x-api-key": self.api_key
        }
        params = {"query": name}
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            data = response.json()
            return data.get("artist", [])
        except requests.RequestException as e:
            print(f"Error searching for artist {name}: {e}")
            return []


def extract_concert_data(setlist_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract concert data from setlist.fm response.
    
    Args:
        setlist_data: Raw setlist.fm setlist data
    
    Returns:
        Dictionary with extracted concert fields
    """
    artist = setlist_data.get("artist", {})
    venue = setlist_data.get("venue", {})
    event_date = setlist_data.get("eventDate")
    
    # Parse event date (format: DD-MM-YYYY)
    parsed_date = None
    if event_date:
        try:
            parsed_date = datetime.strptime(event_date, "%d-%m-%Y")
        except ValueError:
            pass
    
    return {
        "setlistfm_id": setlist_data.get("id"),
        "artist_mbid": artist.get("mbid"),
        "artist_name": artist.get("name"),
        "event_date": parsed_date,
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city", {}).get("name") if venue.get("city") else None,
        "venue_state": venue.get("city", {}).get("state") if venue.get("city") else None,
        "venue_country": venue.get("city", {}).get("country", {}).get("code") if venue.get("city") else None,
        "tour_name": setlist_data.get("tour"),
        "set_length": len(setlist_data.get("sets", {}).get("set", [])),
        "songs": extract_songs(setlist_data),
    }


def extract_songs(setlist_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract song information from setlist.
    
    Args:
        setlist_data: Raw setlist.fm setlist data
    
    Returns:
        List of song dictionaries
    """
    songs = []
    sets = setlist_data.get("sets", {}).get("set", [])
    
    for set_data in sets:
        for song in set_data.get("song", []):
            songs.append({
                "name": song.get("name"),
                "cover": song.get("cover"),
                "tape": song.get("tape"),
            })
    
    return songs
