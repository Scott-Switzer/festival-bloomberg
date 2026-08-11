"""
GDELT Project API pipeline for news sentiment and volume metrics.
"""

import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field


class GDELTClient(BaseModel):
    """GDELT API client"""
    base_url: str = "https://api.gdeltproject.org/api/v2/"
    
    def search_articles(
        self,
        keyword: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Search for news articles mentioning a keyword.
        
        Args:
            keyword: Search keyword (e.g., "The Weeknd")
            start_date: Start date (YYYYMMDD format)
            end_date: End date (YYYYMMDD format)
            limit: Maximum number of results
        
        Returns:
            List of article dictionaries or None
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        
        url = f"{self.base_url}doc/docapi"
        params = {
            "query": keyword,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": limit,
            "startdatetime": start_date,
            "enddatetime": end_date,
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("articles", [])
        except requests.RequestException as e:
            print(f"Error searching GDELT for {keyword}: {e}")
            return []
    
    def get_tone_analysis(self, keyword: str, days: int = 30) -> Dict[str, float]:
        """
        Get tone analysis for articles mentioning a keyword.
        
        Args:
            keyword: Search keyword
            days: Number of days to analyze
        
        Returns:
            Dictionary with tone metrics
        """
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        end_date = datetime.now().strftime("%Y%m%d")
        
        articles = self.search_articles(keyword, start_date, end_date, limit=500)
        
        if not articles:
            return {
                "article_count": 0,
                "avg_tone": 0,
                "positive_count": 0,
                "negative_count": 0,
                "neutral_count": 0,
            }
        
        tones = []
        positive_count = 0
        negative_count = 0
        neutral_count = 0
        
        for article in articles:
            tone = article.get("tone", 0)
            tones.append(tone)
            
            if tone > 5:
                positive_count += 1
            elif tone < -5:
                negative_count += 1
            else:
                neutral_count += 1
        
        avg_tone = sum(tones) / len(tones) if tones else 0
        
        return {
            "article_count": len(articles),
            "avg_tone": avg_tone,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "neutral_count": neutral_count,
            "positive_ratio": positive_count / len(articles) if articles else 0,
            "negative_ratio": negative_count / len(articles) if articles else 0,
        }


def calculate_news_momentum(tone_data: Dict[str, float]) -> Dict[str, float]:
    """
    Calculate news momentum metrics from GDELT tone data.
    
    Args:
        tone_data: Tone analysis from GDELT
    
    Returns:
        Dictionary with momentum metrics
    """
    article_count = tone_data.get("article_count", 0)
    avg_tone = tone_data.get("avg_tone", 0)
    positive_ratio = tone_data.get("positive_ratio", 0)
    negative_ratio = tone_data.get("negative_ratio", 0)
    
    # News volume score (normalized)
    volume_score = min(article_count / 100, 100)  # Cap at 100
    
    # Sentiment score
    sentiment_score = (avg_tone + 10) / 20 * 100  # Normalize -10 to 10 range to 0-100
    
    return {
        "news_volume_score": volume_score,
        "sentiment_score": sentiment_score,
        "article_count": article_count,
        "positive_ratio": positive_ratio,
        "negative_ratio": negative_ratio,
    }
