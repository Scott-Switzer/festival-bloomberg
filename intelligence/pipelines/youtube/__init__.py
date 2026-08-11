"""
YouTube Data API pipeline for video attention metrics.
"""

import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field


class YouTubeClient(BaseModel):
    """YouTube Data API client"""
    base_url: str = "https://www.googleapis.com/youtube/v3/"
    api_key: str = Field(..., description="YouTube API key")
    
    def get_channel_stats(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch channel statistics.
        
        Args:
            channel_id: YouTube channel ID
        
        Returns:
            Channel statistics or None
        """
        url = f"{self.base_url}channels"
        params = {
            "part": "statistics,snippet",
            "id": channel_id,
            "key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("items"):
                return data["items"][0]
            return None
        except requests.RequestException as e:
            print(f"Error fetching channel stats for {channel_id}: {e}")
            return None
    
    def search_channel(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for an artist's YouTube channel.
        
        Args:
            artist_name: Artist name to search
        
        Returns:
            Channel data or None
        """
        url = f"{self.base_url}search"
        params = {
            "part": "snippet",
            "q": artist_name,
            "type": "channel",
            "maxResults": 1,
            "key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("items"):
                channel_id = data["items"][0]["snippet"]["channelId"]
                return self.get_channel_stats(channel_id)
            return None
        except requests.RequestException as e:
            print(f"Error searching channel for {artist_name}: {e}")
            return None
    
    def get_video_stats(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch video statistics.
        
        Args:
            video_id: YouTube video ID
        
        Returns:
            Video statistics or None
        """
        url = f"{self.base_url}videos"
        params = {
            "part": "statistics,snippet",
            "id": video_id,
            "key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("items"):
                return data["items"][0]
            return None
        except requests.RequestException as e:
            print(f"Error fetching video stats for {video_id}: {e}")
            return None


def calculate_youtube_momentum(channel_stats: Dict[str, Any], historical_data: Optional[List[Dict]] = None) -> Dict[str, float]:
    """
    Calculate YouTube momentum metrics from channel statistics.
    
    Args:
        channel_stats: Channel statistics from YouTube API
        historical_data: Optional historical data for trend calculation
    
    Returns:
        Dictionary with momentum metrics
    """
    statistics = channel_stats.get("statistics", {})
    
    subscriber_count = int(statistics.get("subscriberCount", 0))
    view_count = int(statistics.get("viewCount", 0))
    video_count = int(statistics.get("videoCount", 0))
    
    # Calculate views per video
    views_per_video = view_count / video_count if video_count > 0 else 0
    
    # Calculate subscriber growth rate (if historical data available)
    subscriber_growth_rate = 0.0
    if historical_data and len(historical_data) > 1:
        recent_subscribers = historical_data[-1].get("subscribers", 0)
        previous_subscribers = historical_data[0].get("subscribers", 0)
        if previous_subscribers > 0:
            subscriber_growth_rate = (recent_subscribers - previous_subscribers) / previous_subscribers
    
    return {
        "subscriber_count": subscriber_count,
        "view_count": view_count,
        "video_count": video_count,
        "views_per_video": views_per_video,
        "subscriber_growth_rate": subscriber_growth_rate,
    }
