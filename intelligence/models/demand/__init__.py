"""
Demand Forecasting Model - Predicts audience demand for artists and festivals.
Uses time-series analysis and momentum indicators.
"""

import polars as pl
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class DemandFeatures:
    """Features for demand forecasting"""
    momentum_score: float
    momentum_change_30d: float
    momentum_change_90d: float
    youtube_momentum: float
    wiki_momentum: float
    news_momentum: float
    booking_value_index: float
    tour_probability_90d: float
    festival_appearance_probability: float
    market_affinity_score: float
    genre_momentum_score: float
    recent_release_recency: float


class DemandForecastModel:
    """
    Forecasts audience demand for artists and festivals.
    
    Combines momentum signals with market factors to estimate
    expected demand levels.
    """
    
    def __init__(self):
        # Feature weights for demand calculation
        self.feature_weights = {
            "momentum_score": 0.25,
            "momentum_change_30d": 0.15,
            "momentum_change_90d": 0.10,
            "youtube_momentum": 0.10,
            "wiki_momentum": 0.08,
            "news_momentum": 0.07,
            "booking_value_index": 0.10,
            "tour_probability_90d": 0.05,
            "festival_appearance_probability": 0.05,
            "market_affinity_score": 0.03,
            "genre_momentum_score": 0.02,
        }
    
    def calculate_demand_score(self, features: DemandFeatures) -> float:
        """
        Calculate overall demand score (0-100).
        
        Args:
            features: Demand features for the artist
        
        Returns:
            Demand score (0-100)
        """
        weighted_sum = 0.0
        total_weight = 0.0
        
        for feature_name, weight in self.feature_weights.items():
            feature_value = getattr(features, feature_name, 0)
            weighted_sum += feature_value * weight
            total_weight += weight
        
        demand_score = weighted_sum / total_weight if total_weight > 0 else 0
        return min(max(demand_score, 0), 100)
    
    def forecast_demand_trend(
        self,
        current_demand: float,
        momentum_change_30d: float,
        momentum_change_90d: float,
    ) -> Dict[str, float]:
        """
        Forecast demand trend over time.
        
        Args:
            current_demand: Current demand score
            momentum_change_30d: 30-day momentum change
            momentum_change_90d: 90-day momentum change
        
        Returns:
            Dictionary with forecasted demand at different horizons
        """
        # Use momentum changes to project future demand
        # Simplified linear projection with decay
        
        trend_30d = current_demand * (1 + momentum_change_30d * 0.5)
        trend_90d = current_demand * (1 + momentum_change_90d * 0.3)
        trend_180d = current_demand * (1 + momentum_change_90d * 0.15)
        
        return {
            "current_demand": current_demand,
            "demand_30d_forecast": min(max(trend_30d, 0), 100),
            "demand_90d_forecast": min(max(trend_90d, 0), 100),
            "demand_180d_forecast": min(max(trend_180d, 0), 100),
        }
    
    def calculate_market_demand(
        self,
        lineup_data: pl.DataFrame,
        artist_demand_scores: Dict[str, float],
    ) -> float:
        """
        Calculate aggregate market demand for a festival lineup.
        
        Args:
            lineup_data: DataFrame with artist_id, billing_tier
            artist_demand_scores: Dict mapping artist_id to demand score
        
        Returns:
            Aggregate market demand score (0-100)
        """
        if len(lineup_data) == 0:
            return 0.0
        
        billing_weights = {
            "headliner": 1.0,
            "sub_headliner": 0.75,
            "main_stage": 0.5,
            "secondary": 0.25,
            "emerging": 0.1,
        }
        
        weighted_demand = 0.0
        total_weight = 0.0
        
        for row in lineup_data.iter_rows(named=True):
            artist_id = row["artist_id"]
            billing_tier = row["billing_tier"]
            
            weight = billing_weights.get(billing_tier, 0.25)
            demand = artist_demand_scores.get(artist_id, 50)
            
            weighted_demand += demand * weight
            total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        return weighted_demand / total_weight
    
    def calculate_incremental_demand(
        self,
        baseline_demand: float,
        new_artist_demand: float,
        billing_tier: str,
    ) -> float:
        """
        Calculate incremental demand from adding an artist to lineup.
        
        Args:
            baseline_demand: Current aggregate demand
            new_artist_demand: Demand score of new artist
            billing_tier: Billing tier of new artist
        
        Returns:
            Incremental demand contribution
        """
        billing_weights = {
            "headliner": 1.0,
            "sub_headliner": 0.75,
            "main_stage": 0.5,
            "secondary": 0.25,
            "emerging": 0.1,
        }
        
        weight = billing_weights.get(billing_tier, 0.25)
        
        # Incremental demand with diminishing returns
        # Higher baseline = less incremental impact
        diminishing_factor = max(0, 1 - baseline_demand / 150)
        incremental = new_artist_demand * weight * diminishing_factor
        
        return incremental


def calculate_demand_percentile(demand_score: float, all_demand_scores: List[float]) -> float:
    """
    Calculate percentile rank of demand score among all artists.
    
    Args:
        demand_score: Artist's demand score
        all_demand_scores: List of all demand scores
    
    Returns:
        Percentile rank (0-100)
    """
    if not all_demand_scores:
        return 50.0
    
    sorted_scores = sorted(all_demand_scores)
    rank = sorted_scores.index(demand_score) if demand_score in sorted_scores else len(sorted_scores)
    percentile = (rank / len(sorted_scores)) * 100
    
    return percentile
