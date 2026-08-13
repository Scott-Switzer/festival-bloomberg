"""
Expected billing baseline model for Festival Bloomberg.

This module provides artist billing prediction based on:
- Artist factor scores (momentum, relevance, etc.)
- Historical billing patterns
- Market benchmarks
- Festival context and constraints
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class BillingTier(Enum):
    """Standardized billing tiers."""
    HEADLINER = "HEADLINER"
    SUB_HEADLINER = "SUB_HEADLINER"
    SUPPORTING = "SUPPORTING"
    EARLY_DAY = "EARLY_DAY"
    DJ_ONLY = "DJ_ONLY"


@dataclass
class BillingPrediction:
    """Expected billing prediction for an artist."""
    artist_key: str
    expected_tier: BillingTier
    expected_order: int
    confidence: float
    
    # Detailed breakdown
    booking_probability: float
    expected_day: Optional[int] = None
    expected_stage: Optional[str] = None
    reasoning: str = ""
    factors: Dict[str, float] = field(default_factory=dict)
    
    # Metadata
    model_version: str = "v1.0"
    training_period: str = "2020-2025"
    calculated_at: datetime = field(default_factory=datetime.utcnow)
    feature_date: Optional[date] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            'artist_key': self.artist_key,
            'expected_tier': self.expected_tier.value,
            'expected_order': self.expected_order,
            'confidence': self.confidence,
            'booking_probability': self.booking_probability,
            'expected_day': self.expected_day,
            'expected_stage': self.expected_stage,
            'reasoning': self.reasoning,
            'factors': self.factors,
            'model_version': self.model_version,
            'training_period': self.training_period,
            'calculated_at': self.calculated_at.isoformat(),
            'feature_date': self.feature_date.isoformat() if self.feature_date else None,
        }


class BillingBaselineCalculator:
    """Calculate expected billing baseline from artist factors."""
    
    def __init__(self, festival_context: Optional[Dict[str, Any]] = None):
        self.festival_context = festival_context or {}
        
        # Tier thresholds (based on factor scores)
        self.tier_thresholds = {
            BillingTier.HEADLINER: {
                'min_momentum': 75,
                'min_relevance': 70,
                'min_overall': 70,
            },
            BillingTier.SUB_HEADLINER: {
                'min_momentum': 60,
                'min_relevance': 55,
                'min_overall': 55,
            },
            BillingTier.SUPPORTING: {
                'min_momentum': 40,
                'min_relevance': 35,
                'min_overall': 35,
            },
            BillingTier.EARLY_DAY: {
                'min_momentum': 20,
                'min_relevance': 15,
                'min_overall': 15,
            },
            BillingTier.DJ_ONLY: {
                'min_momentum': 10,
                'min_relevance': 10,
                'min_overall': 10,
            },
        }
    
    def calculate_billing(
        self,
        artist_key: str,
        artist_factors: Dict[str, float],
        historical_billing: Optional[Dict[str, Any]] = None,
        festival_constraints: Optional[Dict[str, Any]] = None,
    ) -> BillingPrediction:
        """Calculate expected billing prediction."""
        
        momentum = artist_factors.get('momentum_score', 0)
        relevance = artist_factors.get('relevance_score', 0)
        audience_fit = artist_factors.get('audience_fit_score', 0)
        value_prop = artist_factors.get('value_proposition_score', 0)
        complexity = artist_factors.get('booking_complexity_score', 0)
        risk = artist_factors.get('risk_score', 0)
        
        # Calculate overall score
        overall_score = (
            momentum * 0.25 +
            relevance * 0.20 +
            audience_fit * 0.20 +
            value_prop * 0.15 +
            complexity * 0.10 +
            risk * 0.10
        )
        
        # Determine tier
        predicted_tier = self._determine_tier(momentum, relevance, overall_score)
        
        # Adjust based on historical billing
        if historical_billing:
            predicted_tier = self._adjust_for_historical(
                predicted_tier,
                historical_billing,
                overall_score
            )
        
        # Calculate expected order within tier
        expected_order = self._calculate_order(predicted_tier, overall_score)
        
        # Calculate booking probability
        booking_probability = self._calculate_booking_probability(
            overall_score,
            complexity,
            risk,
            festival_constraints
        )
        
        # Generate reasoning
        reasoning = self._generate_reasoning(
            predicted_tier,
            momentum,
            relevance,
            overall_score,
            historical_billing
        )
        
        # Factor breakdown
        factors = {
            'momentum': momentum,
            'relevance': relevance,
            'audience_fit': audience_fit,
            'value_proposition': value_prop,
            'complexity': complexity,
            'risk': risk,
            'overall': overall_score,
        }
        
        return BillingPrediction(
            artist_key=artist_key,
            expected_tier=predicted_tier,
            expected_order=expected_order,
            confidence=self._calculate_confidence(factors, historical_billing),
            booking_probability=booking_probability,
            reasoning=reasoning,
            factors=factors,
        )
    
    def _determine_tier(
        self,
        momentum: float,
        relevance: float,
        overall: float,
    ) -> BillingTier:
        """Determine billing tier based on factor scores."""
        
        # Check tiers from highest to lowest
        for tier in [
            BillingTier.HEADLINER,
            BillingTier.SUB_HEADLINER,
            BillingTier.SUPPORTING,
            BillingTier.EARLY_DAY,
            BillingTier.DJ_ONLY,
        ]:
            thresholds = self.tier_thresholds[tier]
            
            if (momentum >= thresholds['min_momentum'] and
                relevance >= thresholds['min_relevance'] and
                overall >= thresholds['min_overall']):
                return tier
        
        return BillingTier.DJ_ONLY
    
    def _adjust_for_historical(
        self,
        predicted_tier: BillingTier,
        historical_billing: Dict[str, Any],
        overall_score: float,
    ) -> BillingTier:
        """Adjust predicted tier based on historical billing patterns."""
        
        historical_tier = historical_billing.get('typical_tier')
        if not historical_tier:
            return predicted_tier
        
        try:
            historical_tier_enum = BillingTier(historical_tier.upper())
        except ValueError:
            return predicted_tier
        
        # If recent historical billing is significantly different, adjust
        tier_order = {
            BillingTier.HEADLINER: 5,
            BillingTier.SUB_HEADLINER: 4,
            BillingTier.SUPPORTING: 3,
            BillingTier.EARLY_DAY: 2,
            BillingTier.DJ_ONLY: 1,
        }
        
        predicted_order = tier_order[predicted_tier]
        historical_order = tier_order[historical_tier_enum]
        
        # If current factors suggest significant change, allow some movement
        if abs(predicted_order - historical_order) <= 1:
            # Small movement is acceptable
            return predicted_tier
        elif predicted_order > historical_order:
            # Predicting upgrade - requires strong signals
            if overall_score > 80:
                return predicted_tier
            else:
                return historical_tier_enum
        else:
            # Predicting downgrade - requires strong signals
            if overall_score < 30:
                return predicted_tier
            else:
                return historical_tier_enum
    
    def _calculate_order(self, tier: BillingTier, overall_score: float) -> int:
        """Calculate expected order within tier (1 = highest in tier)."""
        
        # Base order depends on tier
        tier_base_orders = {
            BillingTier.HEADLINER: 1,
            BillingTier.SUB_HEADLINER: 3,
            BillingTier.SUPPORTING: 8,
            BillingTier.EARLY_DAY: 15,
            BillingTier.DJ_ONLY: 20,
        }
        
        base_order = tier_base_orders[tier]
        
        # Adjust based on overall score within tier
        score_adjustment = int((100 - overall_score) / 10)
        
        return max(1, base_order + score_adjustment)
    
    def _calculate_booking_probability(
        self,
        overall_score: float,
        complexity: float,
        risk: float,
        festival_constraints: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Calculate probability of successful booking."""
        
        constraints = festival_constraints or {}
        
        # Base probability from overall score
        base_probability = overall_score / 100
        
        # Reduce based on complexity (lower complexity score = harder to book)
        complexity_factor = complexity / 100
        
        # Reduce based on risk (lower risk score = higher risk)
        risk_factor = risk / 100
        
        # Adjust for budget constraints
        budget_factor = 1.0
        if constraints.get('budget_limited'):
            budget_factor = 0.8
        
        # Adjust for schedule conflicts
        schedule_factor = 1.0
        if constraints.get('schedule_conflicts'):
            schedule_factor = 0.7
        
        # Combine factors
        probability = (
            base_probability * 0.4 +
            complexity_factor * 0.3 +
            risk_factor * 0.2 +
            budget_factor * 0.05 +
            schedule_factor * 0.05
        )
        
        return round(min(1.0, max(0.0, probability)), 2)
    
    def _calculate_confidence(
        self,
        factors: Dict[str, float],
        historical_billing: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Calculate confidence in prediction."""
        
        # Base confidence from factor spread
        factor_values = list(factors.values())
        factor_spread = max(factor_values) - min(factor_values)
        
        # High spread = clearer signal = higher confidence
        spread_confidence = min(1.0, factor_spread / 50)
        
        # Historical data increases confidence
        historical_confidence = 0.3 if historical_billing else 0.0
        
        # Overall score quality
        overall_score = factors.get('overall', 0)
        score_confidence = overall_score / 100
        
        # Combine
        confidence = (
            spread_confidence * 0.4 +
            historical_confidence * 0.3 +
            score_confidence * 0.3
        )
        
        return round(min(1.0, max(0.1, confidence)), 2)
    
    def _generate_reasoning(
        self,
        tier: BillingTier,
        momentum: float,
        relevance: float,
        overall: float,
        historical_billing: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate human-readable reasoning for prediction."""
        
        reasoning_parts = []
        
        # Overall assessment
        reasoning_parts.append(f"Overall score: {overall:.1f}/100")
        
        # Key factors
        if momentum >= 70:
            reasoning_parts.append("Strong momentum")
        elif momentum >= 40:
            reasoning_parts.append("Moderate momentum")
        else:
            reasoning_parts.append("Limited momentum")
        
        if relevance >= 70:
            reasoning_parts.append("High festival relevance")
        elif relevance >= 40:
            reasoning_parts.append("Moderate festival relevance")
        else:
            reasoning_parts.append("Limited festival relevance")
        
        # Historical context
        if historical_billing:
            typical_tier = historical_billing.get('typical_tier', 'unknown')
            reasoning_parts.append(f"Typical billing: {typical_tier}")
        
        # Tier justification
        tier_justification = {
            BillingTier.HEADLINER: "Headliner status due to strong overall performance",
            BillingTier.SUB_HEADLINER: "Sub-headliner status with good momentum",
            BillingTier.SUPPORTING: "Supporting act with moderate appeal",
            BillingTier.EARLY_DAY: "Early-day placement with developing profile",
            BillingTier.DJ_ONLY: "DJ/production role with specialized appeal",
        }
        
        reasoning_parts.append(tier_justification.get(tier, "Tier based on factor analysis"))
        
        return ". ".join(reasoning_parts) + "."


class ExpectedBillingModel:
    """Main model for expected billing predictions."""
    
    def __init__(self, festival_context: Optional[Dict[str, Any]] = None):
        self.festival_context = festival_context or {}
        self.calculator = BillingBaselineCalculator(festival_context)
    
    def predict_billing(
        self,
        artist_key: str,
        artist_factors: Dict[str, float],
        historical_billing: Optional[Dict[str, Any]] = None,
        festival_constraints: Optional[Dict[str, Any]] = None,
    ) -> BillingPrediction:
        """Generate billing prediction for an artist."""
        
        return self.calculator.calculate_billing(
            artist_key=artist_key,
            artist_factors=artist_factors,
            historical_billing=historical_billing,
            festival_constraints=festival_constraints,
        )
    
    def batch_predict(
        self,
        artists: List[Dict[str, Any]],
        festival_constraints: Optional[Dict[str, Any]] = None,
    ) -> List[BillingPrediction]:
        """Generate billing predictions for multiple artists."""
        
        predictions = []
        for artist in artists:
            artist_key = artist.get('artist_key')
            factors = artist.get('factors', {})
            historical = artist.get('historical_billing')
            
            prediction = self.predict_billing(
                artist_key=artist_key,
                artist_factors=factors,
                historical_billing=historical,
                festival_constraints=festival_constraints,
            )
            
            predictions.append(prediction)
        
        return predictions
    
    def optimize_lineup(
        self,
        predictions: List[BillingPrediction],
        budget: Optional[float] = None,
        target_tier_distribution: Optional[Dict[str, int]] = None,
    ) -> List[BillingPrediction]:
        """Optimize lineup based on predictions and constraints."""
        
        # Sort by overall factor score (descending)
        sorted_predictions = sorted(
            predictions,
            key=lambda p: p.factors.get('overall', 0),
            reverse=True
        )
        
        # Apply budget constraints if provided
        if budget:
            # This is a simplified budget optimization
            # In production, this would use more sophisticated optimization
            pass
        
        # Apply tier distribution targets if provided
        if target_tier_distribution:
            # This is a simplified tier distribution optimization
            # In production, this would use more sophisticated optimization
            pass
        
        return sorted_predictions