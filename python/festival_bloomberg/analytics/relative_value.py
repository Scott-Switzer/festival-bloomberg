"""
Relative value calculation for Festival Bloomberg.

This module provides relative value analysis to identify under/overvalued artists:
- Compares current billing vs expected billing
- Analyzes momentum vs billing position
- Evaluates audience fit vs billing tier
- Provides peer group comparisons
- Generates value categories (UNDERVALUED, FAIR_VALUE, OVERVALUED)
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
import logging
import statistics

logger = logging.getLogger(__name__)


class ValueCategory(Enum):
    """Relative value categories."""
    UNDERVALUED = "UNDERVALUED"
    FAIR_VALUE = "FAIR_VALUE"
    OVERVALUED = "OVERVALUED"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class RelativeValueResult:
    """Relative value analysis result."""
    artist_key: str
    relative_value_score: float
    value_category: ValueCategory
    value_percentile: float
    
    # Component breakdown
    current_billing_tier: Optional[str] = None
    expected_billing_tier: Optional[str] = None
    billing_gap: Optional[float] = None
    momentum_vs_billing: Optional[float] = None
    audience_vs_billing: Optional[float] = None
    
    # Peer comparison
    peer_group: Optional[str] = None
    peer_comparison: Dict[str, float] = field(default_factory=dict)
    market_position: Optional[str] = None
    
    # Metadata
    model_version: str = "v1.0"
    calculated_at: datetime = field(default_factory=datetime.utcnow)
    feature_date: Optional[date] = None
    confidence: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            'artist_key': self.artist_key,
            'relative_value_score': self.relative_value_score,
            'value_category': self.value_category.value,
            'value_percentile': self.value_percentile,
            'current_billing_tier': self.current_billing_tier,
            'expected_billing_tier': self.expected_billing_tier,
            'billing_gap': self.billing_gap,
            'momentum_vs_billing': self.momentum_vs_billing,
            'audience_vs_billing': self.audience_vs_billing,
            'peer_group': self.peer_group,
            'peer_comparison': self.peer_comparison,
            'market_position': self.market_position,
            'model_version': self.model_version,
            'calculated_at': self.calculated_at.isoformat(),
            'feature_date': self.feature_date.isoformat() if self.feature_date else None,
            'confidence': self.confidence,
        }


class RelativeValueCalculator:
    """Calculate relative value for artists."""
    
    def __init__(self, peer_groups: Optional[Dict[str, List[str]]] = None):
        self.peer_groups = peer_groups or {}
        
        # Tier value mappings for comparison
        self.tier_values = {
            'HEADLINER': 100,
            'SUB_HEADLINER': 75,
            'SUPPORTING': 50,
            'EARLY_DAY': 25,
            'DJ_ONLY': 15,
        }
    
    def calculate_relative_value(
        self,
        artist_key: str,
        current_billing: Optional[Dict[str, Any]] = None,
        expected_billing: Optional[Dict[str, Any]] = None,
        artist_factors: Optional[Dict[str, float]] = None,
        peer_group_data: Optional[List[Dict[str, Any]]] = None,
    ) -> RelativeValueResult:
        """Calculate relative value for an artist."""
        
        components = {}
        
        # Billing gap analysis
        if current_billing and expected_billing:
            billing_gap = self._calculate_billing_gap(
                current_billing.get('tier'),
                expected_billing.get('tier')
            )
            components['billing_gap'] = billing_gap
        else:
            billing_gap = None
        
        # Momentum vs billing analysis
        if artist_factors and current_billing:
            momentum_vs_billing = self._calculate_momentum_vs_billing(
                artist_factors.get('momentum_score', 0),
                current_billing.get('tier')
            )
            components['momentum_vs_billing'] = momentum_vs_billing
        else:
            momentum_vs_billing = None
        
        # Audience fit vs billing analysis
        if artist_factors and current_billing:
            audience_vs_billing = self._calculate_audience_vs_billing(
                artist_factors.get('audience_fit_score', 0),
                current_billing.get('tier')
            )
            components['audience_vs_billing'] = audience_vs_billing
        else:
            audience_vs_billing = None
        
        # Peer comparison
        peer_comparison = {}
        if peer_group_data and artist_factors:
            peer_comparison = self._compare_with_peers(
                artist_factors,
                peer_group_data
            )
            components['peer_comparison'] = statistics.mean(peer_comparison.values()) if peer_comparison else 0
        
        # Calculate overall relative value score
        relative_value_score = self._calculate_overall_score(components)
        
        # Determine value category
        value_category = self._determine_category(relative_value_score, components)
        
        # Calculate percentile (if peer data available)
        value_percentile = self._calculate_percentile(
            relative_value_score,
            peer_group_data
        ) if peer_group_data else 50.0
        
        # Determine market position
        market_position = self._determine_market_position(value_category, relative_value_score)
        
        # Determine peer group
        peer_group = self._determine_peer_group(artist_factors) if artist_factors else None
        
        return RelativeValueResult(
            artist_key=artist_key,
            relative_value_score=relative_value_score,
            value_category=value_category,
            value_percentile=value_percentile,
            current_billing_tier=current_billing.get('tier') if current_billing else None,
            expected_billing_tier=expected_billing.get('tier') if expected_billing else None,
            billing_gap=billing_gap,
            momentum_vs_billing=momentum_vs_billing,
            audience_vs_billing=audience_vs_billing,
            peer_group=peer_group,
            peer_comparison=peer_comparison,
            market_position=market_position,
        )
    
    def _calculate_billing_gap(self, current_tier: Optional[str], expected_tier: Optional[str]) -> float:
        """Calculate gap between current and expected billing."""
        
        if not current_tier or not expected_tier:
            return 0.0
        
        current_value = self.tier_values.get(current_tier.upper(), 50)
        expected_value = self.tier_values.get(expected_tier.upper(), 50)
        
        # Positive gap = undervalued (current < expected)
        # Negative gap = overvalued (current > expected)
        gap = expected_value - current_value
        
        # Normalize to -1 to 1 range
        normalized_gap = gap / 100
        
        return round(normalized_gap, 2)
    
    def _calculate_momentum_vs_billing(self, momentum_score: float, billing_tier: Optional[str]) -> float:
        """Calculate whether momentum justifies current billing."""
        
        if not billing_tier:
            return 0.0
        
        tier_value = self.tier_values.get(billing_tier.upper(), 50)
        
        # Expected momentum for this tier
        expected_momentum = tier_value * 0.8  # 80% of tier value in momentum
        
        # Calculate difference
        momentum_gap = momentum_score - expected_momentum
        
        # Normalize to -1 to 1 range
        normalized_gap = momentum_gap / 100
        
        return round(normalized_gap, 2)
    
    def _calculate_audience_vs_billing(self, audience_score: float, billing_tier: Optional[str]) -> float:
        """Calculate whether audience fit justifies current billing."""
        
        if not billing_tier:
            return 0.0
        
        tier_value = self.tier_values.get(billing_tier.upper(), 50)
        
        # Expected audience fit for this tier
        expected_audience = tier_value * 0.7  # 70% of tier value in audience fit
        
        # Calculate difference
        audience_gap = audience_score - expected_audience
        
        # Normalize to -1 to 1 range
        normalized_gap = audience_gap / 100
        
        return round(normalized_gap, 2)
    
    def _compare_with_peers(
        self,
        artist_factors: Dict[str, float],
        peer_group_data: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Compare artist factors with peer group."""
        
        if not peer_group_data:
            return {}
        
        comparisons = {}
        
        # Extract peer factors
        peer_momentum = [p.get('momentum_score', 0) for p in peer_group_data]
        peer_relevance = [p.get('relevance_score', 0) for p in peer_group_data]
        peer_audience = [p.get('audience_fit_score', 0) for p in peer_group_data]
        peer_value = [p.get('value_proposition_score', 0) for p in peer_group_data]
        
        # Calculate percentiles
        if peer_momentum:
            artist_momentum = artist_factors.get('momentum_score', 0)
            momentum_percentile = self._calculate_percentile_value(artist_momentum, peer_momentum)
            comparisons['momentum_percentile'] = momentum_percentile
        
        if peer_relevance:
            artist_relevance = artist_factors.get('relevance_score', 0)
            relevance_percentile = self._calculate_percentile_value(artist_relevance, peer_relevance)
            comparisons['relevance_percentile'] = relevance_percentile
        
        if peer_audience:
            artist_audience = artist_factors.get('audience_fit_score', 0)
            audience_percentile = self._calculate_percentile_value(artist_audience, peer_audience)
            comparisons['audience_percentile'] = audience_percentile
        
        if peer_value:
            artist_value = artist_factors.get('value_proposition_score', 0)
            value_percentile = self._calculate_percentile_value(artist_value, peer_value)
            comparisons['value_percentile'] = value_percentile
        
        return comparisons
    
    def _calculate_percentile_value(self, value: float, peer_values: List[float]) -> float:
        """Calculate percentile of value within peer group."""
        
        if not peer_values:
            return 50.0
        
        count = len(peer_values)
        lower_count = sum(1 for v in peer_values if v < value)
        equal_count = sum(1 for v in peer_values if v == value)
        
        percentile = (lower_count + 0.5 * equal_count) / count * 100
        
        return round(percentile, 2)
    
    def _calculate_overall_score(self, components: Dict[str, float]) -> float:
        """Calculate overall relative value score from components."""
        
        if not components:
            return 0.0
        
        weights = {
            'billing_gap': 0.40,
            'momentum_vs_billing': 0.30,
            'audience_vs_billing': 0.20,
            'peer_comparison': 0.10,
        }
        
        weighted_sum = 0.0
        total_weight = 0.0
        
        for component, value in components.items():
            weight = weights.get(component, 0)
            weighted_sum += value * weight
            total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        # Convert to 0-100 scale (from -1 to 1)
        normalized_score = (weighted_sum / total_weight + 1) * 50
        
        return round(normalized_score, 2)
    
    def _determine_category(self, score: float, components: Dict[str, float]) -> ValueCategory:
        """Determine value category from score and components."""
        
        # If insufficient data, mark as uncertain
        if len(components) < 2:
            return ValueCategory.UNCERTAIN
        
        # Score-based categorization
        if score >= 65:
            return ValueCategory.UNDERVALUED
        elif score >= 45:
            return ValueCategory.FAIR_VALUE
        elif score >= 30:
            return ValueCategory.OVERVALUED
        else:
            return ValueCategory.UNCERTAIN
    
    def _calculate_percentile(self, score: float, peer_group_data: List[Dict[str, Any]]) -> float:
        """Calculate percentile within peer group."""
        
        if not peer_group_data:
            return 50.0
        
        # Extract peer scores (simplified - in production would calculate properly)
        peer_scores = [p.get('relative_value_score', 50) for p in peer_group_data]
        
        return self._calculate_percentile_value(score, peer_scores)
    
    def _determine_market_position(self, category: ValueCategory, score: float) -> str:
        """Determine market position description."""
        
        positions = {
            ValueCategory.UNDERVALUED: "Strong Buy",
            ValueCategory.FAIR_VALUE: "Hold",
            ValueCategory.OVERVALUED: "Sell",
            ValueCategory.UNCERTAIN: "Data Insufficient",
        }
        
        base_position = positions.get(category, "Unknown")
        
        # Add nuance based on score
        if category == ValueCategory.UNDERVALUED:
            if score >= 80:
                return "Strong Buy - Significant Upside"
            else:
                return "Buy - Moderate Upside"
        elif category == ValueCategory.OVERVALUED:
            if score <= 20:
                return "Strong Sell - Significant Downside"
            else:
                return "Sell - Moderate Downside"
        
        return base_position
    
    def _determine_peer_group(self, artist_factors: Dict[str, float]) -> Optional[str]:
        """Determine appropriate peer group for artist."""
        
        # Simple peer group assignment based on overall score
        overall = artist_factors.get('momentum_score', 0) + artist_factors.get('relevance_score', 0)
        
        if overall >= 140:
            return "top_tier"
        elif overall >= 100:
            return "upper_mid_tier"
        elif overall >= 60:
            return "mid_tier"
        else:
            return "developing"
    
    def batch_calculate(
        self,
        artists: List[Dict[str, Any]],
        peer_group_mapping: Optional[Dict[str, List[str]]] = None,
    ) -> List[RelativeValueResult]:
        """Calculate relative value for multiple artists."""
        
        results = []
        
        # Build peer groups if mapping provided
        peer_groups = {}
        if peer_group_mapping:
            for group_name, artist_keys in peer_group_mapping.items():
                peer_groups[group_name] = [
                    a for a in artists if a.get('artist_key') in artist_keys
                ]
        
        for artist in artists:
            artist_key = artist.get('artist_key')
            current_billing = artist.get('current_billing')
            expected_billing = artist.get('expected_billing')
            factors = artist.get('factors')
            
            # Get peer group data
            peer_group_name = self._determine_peer_group(factors) if factors else None
            peer_data = peer_groups.get(peer_group_name, []) if peer_group_name else []
            
            result = self.calculate_relative_value(
                artist_key=artist_key,
                current_billing=current_billing,
                expected_billing=expected_billing,
                artist_factors=factors,
                peer_group_data=peer_data,
            )
            
            results.append(result)
        
        return results