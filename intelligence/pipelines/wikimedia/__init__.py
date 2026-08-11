"""
Wikimedia Pageviews API pipeline for attention metrics.
"""

import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field


class WikimediaClient(BaseModel):
    """Wikimedia Pageviews API client"""
    base_url: str = "https://pageviews.wmflabs.org/api/"
    project: str = Field(default="en.wikipedia", description="Wikimedia project")
    access: str = Field(default="all-access", description="Access type")
    agent: str = Field(default="user", description="Agent type")
    
    def get_pageviews(
        self,
        page_title: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        granularity: str = "daily",
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch pageview data for a specific page.
        
        Args:
            page_title: Page title (e.g., "The_Weeknd")
            start_date: Start date (YYYY-MM-DD format)
            end_date: End date (YYYY-MM-DD format)
            granularity: daily or monthly
        
        Returns:
            Pageview data dictionary or None
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
        
        url = f"{self.base_url}per_article/{self.project}/{self.access}/{self.agent}/{page_title}/{start_date}/{end_date}/{granularity}"
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching pageviews for {page_title}: {e}")
            return None
    
    def get_pageviews_for_artist(self, artist_name: str, days: int = 365) -> List[Dict[str, Any]]:
        """
        Fetch pageviews for an artist (converting name to Wikipedia title format).
        
        Args:
            artist_name: Artist name
            days: Number of days of history
        
        Returns:
            List of daily pageview records
        """
        # Convert artist name to Wikipedia title format (spaces to underscores)
        page_title = artist_name.replace(" ", "_")
        
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        
        data = self.get_pageviews(page_title, start_date, end_date, granularity="daily")
        
        if data and "items" in data:
            return data["items"]
        return []


def calculate_momentum_from_pageviews(pageviews: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Calculate momentum metrics from pageview data.
    
    Args:
        pageviews: List of pageview records from Wikimedia API
    
    Returns:
        Dictionary with momentum metrics
    """
    if not pageviews:
        return {
            "total_views": 0,
            "avg_daily_views": 0,
            "recent_7d_avg": 0,
            "recent_30d_avg": 0,
            "momentum_7d": 0,
            "momentum_30d": 0,
        }
    
    # Sort by date
    sorted_views = sorted(pageviews, key=lambda x: x["timestamp"])
    
    # Extract views
    views = [item["views"] for item in sorted_views]
    
    # Calculate metrics
    total_views = sum(views)
    avg_daily_views = total_views / len(views)
    
    # Recent averages
    recent_7d = views[-7:] if len(views) >= 7 else views
    recent_30d = views[-30:] if len(views) >= 30 else views
    
    recent_7d_avg = sum(recent_7d) / len(recent_7d)
    recent_30d_avg = sum(recent_30d) / len(recent_30d)
    
    # Momentum (ratio of recent to historical average)
    momentum_7d = (recent_7d_avg / avg_daily_views) if avg_daily_views > 0 else 0
    momentum_30d = (recent_30d_avg / avg_daily_views) if avg_daily_views > 0 else 0
    
    return {
        "total_views": total_views,
        "avg_daily_views": avg_daily_views,
        "recent_7d_avg": recent_7d_avg,
        "recent_30d_avg": recent_30d_avg,
        "momentum_7d": momentum_7d,
        "momentum_30d": momentum_30d,
    }
