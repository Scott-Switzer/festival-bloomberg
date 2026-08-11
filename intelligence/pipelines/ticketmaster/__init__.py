"""
Ticketmaster Discovery API pipeline for future events and venue data.
"""

import requests
import time
from typing import Optional, Dict, List, Any
from datetime import datetime
from pydantic import BaseModel, Field


class TicketmasterClient(BaseModel):
    """Ticketmaster Discovery API client"""
    base_url: str = "https://app.ticketmaster.com/discovery/v2/"
    api_key: str = Field(..., description="Ticketmaster API key")
    rate_limit_delay: float = Field(default=0.2, ge=0.1, description="Delay between requests")
    
    def search_events(
        self,
        keyword: Optional[str] = None,
        attraction_id: Optional[str] = None,
        venue_id: Optional[str] = None,
        city: Optional[str] = None,
        start_date_time: Optional[str] = None,
        end_date_time: Optional[str] = None,
        size: int = 50,
    ) -> Optional[Dict[str, Any]]:
        """
        Search for events.
        
        Args:
            keyword: Search keyword
            attraction_id: Artist/attraction ID
            venue_id: Venue ID
            city: City name
            start_date_time: Start date (ISO format)
            end_date_time: End date (ISO format)
            size: Number of results
        
        Returns:
            Event search results or None
        """
        url = f"{self.base_url}events.json"
        params = {
            "apikey": self.api_key,
            "size": size,
        }
        
        if keyword:
            params["keyword"] = keyword
        if attraction_id:
            params["attractionId"] = attraction_id
        if venue_id:
            params["venueId"] = venue_id
        if city:
            params["city"] = city
        if start_date_time:
            params["startDateTime"] = start_date_time
        if end_date_time:
            params["endDateTime"] = end_date_time
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json()
        except requests.RequestException as e:
            print(f"Error searching events: {e}")
            return None
    
    def search_attractions(self, keyword: str, size: int = 20) -> Optional[Dict[str, Any]]:
        """
        Search for artists/attractions.
        
        Args:
            keyword: Search keyword
            size: Number of results
        
        Returns:
            Attraction search results or None
        """
        url = f"{self.base_url}attractions.json"
        params = {
            "apikey": self.api_key,
            "keyword": keyword,
            "size": size,
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json()
        except requests.RequestException as e:
            print(f"Error searching attractions: {e}")
            return None
    
    def get_venue(self, venue_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch venue details.
        
        Args:
            venue_id: Ticketmaster venue ID
        
        Returns:
            Venue details or None
        """
        url = f"{self.base_url}venues/{venue_id}.json"
        params = {"apikey": self.api_key}
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching venue {venue_id}: {e}")
            return None
    
    def search_venues(self, keyword: str, size: int = 20) -> Optional[Dict[str, Any]]:
        """
        Search for venues.
        
        Args:
            keyword: Search keyword
            size: Number of results
        
        Returns:
            Venue search results or None
        """
        url = f"{self.base_url}venues.json"
        params = {
            "apikey": self.api_key,
            "keyword": keyword,
            "size": size,
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            time.sleep(self.rate_limit_delay)
            return response.json()
        except requests.RequestException as e:
            print(f"Error searching venues: {e}")
            return None


def extract_event_data(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract event data from Ticketmaster response.
    
    Args:
        event_data: Raw Ticketmaster event data
    
    Returns:
        Dictionary with extracted event fields
    """
    # Extract attractions (artists)
    attractions = event_data.get("_embedded", {}).get("attractions", [])
    primary_attraction = attractions[0] if attractions else {}
    
    # Extract venue
    venues = event_data.get("_embedded", {}).get("venues", [])
    venue = venues[0] if venues else {}
    
    # Extract dates
    dates = event_data.get("dates", {})
    start = dates.get("start", {})
    event_date = start.get("dateTime")
    local_date = start.get("localDate")
    
    # Extract price ranges
    price_ranges = event_data.get("priceRanges", [])
    min_price = None
    max_price = None
    if price_ranges:
        min_price = price_ranges[0].get("min")
        max_price = price_ranges[0].get("max")
    
    return {
        "ticketmaster_id": event_data.get("id"),
        "name": event_data.get("name"),
        "artist_name": primary_attraction.get("name"),
        "artist_id": primary_attraction.get("id"),
        "venue_name": venue.get("name"),
        "venue_id": venue.get("id"),
        "city": venue.get("city", {}).get("name") if venue.get("city") else None,
        "state": venue.get("state", {}).get("name") if venue.get("state") else None,
        "country": venue.get("country", {}).get("name") if venue.get("country") else None,
        "event_date": event_date,
        "local_date": local_date,
        "min_price": min_price,
        "max_price": max_price,
        "url": event_data.get("url"),
    }


def extract_venue_data(venue_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract venue data from Ticketmaster response.
    
    Args:
        venue_data: Raw Ticketmaster venue data
    
    Returns:
        Dictionary with extracted venue fields
    """
    location = venue_data.get("location", {})
    city = venue_data.get("city", {})
    
    return {
        "ticketmaster_id": venue_data.get("id"),
        "name": venue_data.get("name"),
        "city": city.get("name") if city else None,
        "state": city.get("state", {}).get("name") if city.get("state") else None,
        "country": city.get("country", {}).get("name") if city.get("country") else None,
        "address": venue_data.get("address", {}).get("line1"),
        "postal_code": venue_data.get("postalCode"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "url": venue_data.get("url"),
    }
