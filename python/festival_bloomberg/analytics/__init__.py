"""
Festival Bloomberg analytics module.

This module provides comprehensive analytics for artist valuation, booking decisions,
and festival portfolio optimization. It includes:
- Artist factor calculation (momentum, relevance, audience fit, etc.)
- Expected billing baseline models
- Relative value calculations
- Festival portfolio analytics
- Ticket spread and arbitrage analysis
"""

from .factors import (
    ArtistFactorCalculator,
    ArtistFactors,
    MomentumCalculator,
    RelevanceCalculator,
    AudienceFitCalculator,
    ValuePropositionCalculator,
    BookingComplexityCalculator,
    RiskCalculator,
)

from .billing import (
    ExpectedBillingModel,
    BillingPrediction,
    BillingBaselineCalculator,
)

from .relative_value import (
    RelativeValueCalculator,
    RelativeValueResult,
)

from .portfolio import (
    FestivalPortfolioAnalyzer,
    PortfolioMetrics,
    PortfolioOptimization,
)

__all__ = [
    "ArtistFactorCalculator",
    "ArtistFactors",
    "MomentumCalculator",
    "RelevanceCalculator",
    "AudienceFitCalculator",
    "ValuePropositionCalculator",
    "BookingComplexityCalculator",
    "RiskCalculator",
    "ExpectedBillingModel",
    "BillingPrediction",
    "BillingBaselineCalculator",
    "RelativeValueCalculator",
    "RelativeValueResult",
    "FestivalPortfolioAnalyzer",
    "PortfolioMetrics",
    "PortfolioOptimization",
]