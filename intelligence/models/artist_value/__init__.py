"""
Artist Value Model - Booking Value Index calculation.
Combines multiple signals to estimate an artist's live entertainment value.
"""

import polars as pl
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import numpy as np


@dataclass
class BookingValueFeatures:
    """Features for Booking Value Index calculation"""
    youtube_growth_score: float
    wiki_growth_score: float
    news_volume_score: float
    live_performance_frequency: float
    venue_progression_score: float
    festival_billing_history_score: float
    headliner_frequency_score: float
    market_diversity_score: float
    release_recency_score: float
    genre_momentum_score: float
    competition_score: float
    local_affinity_score: float


class BookingValueIndexModel:
    """
    Calculates Booking Value Index (0-100 percentile) for artists.
    
    The BVI represents an artist's expected live-entertainment value relative
    to comparable artists, based on multiple public signals.
    """
    
    def __init__(self):
        # Feature weights (can be tuned based on backtesting)
        self.feature_weights = {
            "youtube_growth_score": 0.15,
            "wiki_growth_score": 0.10,
            "news_volume_score": 0.10,
            "live_performance_frequency": 0.12,
            "venue_progression_score": 0.10,
            "festival_billing_history_score": 0.15,
            "headliner_frequency_score": 0.08,
            "market_diversity_score": 0.05,
            "release_recency_score": 0.05,
            "genre_momentum_score": 0.05,
            "competition_score": 0.03,
            "local_affinity_score": 0.02,
        }
    
    def calculate_bvi(self, features: BookingValueFeatures) -> float:
        """
        Calculate Booking Value Index from features.
        
        Args:
            features: Feature values for the artist
        
        Returns:
            Booking Value Index (0-100)
        """
        weighted_sum = 0.0
        total_weight = 0.0
        
        for feature_name, weight in self.feature_weights.items():
            feature_value = getattr(features, feature_name, 0)
            weighted_sum += feature_value * weight
            total_weight += weight
        
        # Normalize to 0-100 range
        bvi = weighted_sum / total_weight if total_weight > 0 else 0
        return min(max(bvi, 0), 100)
    
    def predict_billing_tier(self, bvi: float) -> str:
        """
        Predict billing tier from BVI.
        
        Args:
            bvi: Booking Value Index
        
        Returns:
            Predicted billing tier
        """
        if bvi >= 90:
            return "headliner"
        elif bvi >= 75:
            return "sub_headliner"
        elif bvi >= 60:
            return "main_stage"
        elif bvi >= 40:
            return "secondary"
        else:
            return "emerging"
    
    def calculate_residual(
        self,
        bvi: float,
        observed_billing_tier: Optional[str],
    ) -> Optional[float]:
        """
        Calculate momentum-to-billing residual.
        
        A positive residual indicates an artist may be underbooked relative to their momentum.
        
        Args:
            bvi: Booking Value Index
            observed_billing_tier: Most recent observed billing tier
        
        Returns:
            Residual score or None if no observed tier
        """
        if not observed_billing_tier:
            return None
        
        # Map billing tiers to approximate BVI values
        tier_to_bvi = {
            "headliner": 90,
            "sub_headliner": 75,
            "main_stage": 60,
            "secondary": 40,
            "emerging": 20,
        }
        
        expected_bvi = tier_to_bvi.get(observed_billing_tier, 50)
        residual = bvi - expected_bvi
        
        return residual
    
    def calculate_bvi_batch(
        self,
        features_list: List[BookingValueFeatures],
    ) -> List[float]:
        """
        Calculate BVI for multiple artists.
        
        Args:
            features_list: List of feature objects
        
        Returns:
            List of BVI scores
        """
        return [self.calculate_bvi(features) for features in features_list]


def calculate_momentum_percentile(bvi: float, all_bvis: List[float]) -> float:
    """
    Calculate percentile rank of BVI among all artists.
    
    Args:
        bvi: Artist's BVI
        all_bvis: List of all BVI scores
    
    Returns:
        Percentile rank (0-100)
    """
    if not all_bvis:
        return 50.0
    
    sorted_bvis = sorted(all_bvis)
    rank = sorted_bvis.index(bvi) if bvi in sorted_bvis else len(sorted_bvis)
    percentile = (rank / len(sorted_bvis)) * 100
    
    return percentile
