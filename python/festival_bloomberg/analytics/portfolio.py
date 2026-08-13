"""
Festival portfolio analytics for Festival Bloomberg.

This module provides comprehensive portfolio analysis for festival lineups:
- Portfolio composition analysis
- Budget allocation optimization
- Risk diversification metrics
- ROI and efficiency calculations
- Portfolio optimization recommendations
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, date
import logging
import statistics

logger = logging.getLogger(__name__)


@dataclass
class PortfolioMetrics:
    """Comprehensive festival portfolio metrics."""
    festival_key: str
    edition_key: str
    edition_year: int
    
    # Composition
    total_artists: int
    headliner_count: int
    sub_headliner_count: int
    supporting_count: int
    early_day_count: int
    
    # Factor averages
    portfolio_momentum_avg: float
    portfolio_momentum_median: float
    portfolio_risk_avg: float
    portfolio_value_avg: float
    portfolio_diversity_score: float
    
    # Budget allocation
    total_budget: Optional[float] = None
    headliner_budget: Optional[float] = None
    supporting_budget: Optional[float] = None
    budget_utilization: Optional[float] = None
    
    # Efficiency metrics
    cost_per_momentum: Optional[float] = None
    cost_per_attendance: Optional[float] = None
    roi_score: Optional[float] = None
    efficiency_score: Optional[float] = None
    
    # Metadata
    portfolio_version: str = "v1.0"
    optimization_method: str = "baseline"
    calculated_at: datetime = field(default_factory=datetime.utcnow)
    feature_date: Optional[date] = None
    confidence: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            'festival_key': self.festival_key,
            'edition_key': self.edition_key,
            'edition_year': self.edition_year,
            'total_artists': self.total_artists,
            'headliner_count': self.headliner_count,
            'sub_headliner_count': self.sub_headliner_count,
            'supporting_count': self.supporting_count,
            'early_day_count': self.early_day_count,
            'portfolio_momentum_avg': self.portfolio_momentum_avg,
            'portfolio_momentum_median': self.portfolio_momentum_median,
            'portfolio_risk_avg': self.portfolio_risk_avg,
            'portfolio_value_avg': self.portfolio_value_avg,
            'portfolio_diversity_score': self.portfolio_diversity_score,
            'total_budget': self.total_budget,
            'headliner_budget': self.headliner_budget,
            'supporting_budget': self.supporting_budget,
            'budget_utilization': self.budget_utilization,
            'cost_per_momentum': self.cost_per_momentum,
            'cost_per_attendance': self.cost_per_attendance,
            'roi_score': self.roi_score,
            'efficiency_score': self.efficiency_score,
            'portfolio_version': self.portfolio_version,
            'optimization_method': self.optimization_method,
            'calculated_at': self.calculated_at.isoformat(),
            'feature_date': self.feature_date.isoformat() if self.feature_date else None,
            'confidence': self.confidence,
        }


@dataclass
class PortfolioOptimization:
    """Portfolio optimization recommendations."""
    current_metrics: PortfolioMetrics
    optimized_metrics: PortfolioMetrics
    recommendations: List[str]
    budget_reallocation: Dict[str, float]
    artist_changes: List[Dict[str, Any]]
    expected_improvement: Dict[str, float]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            'current_metrics': self.current_metrics.to_dict(),
            'optimized_metrics': self.optimized_metrics.to_dict(),
            'recommendations': self.recommendations,
            'budget_reallocation': self.budget_reallocation,
            'artist_changes': self.artist_changes,
            'expected_improvement': self.expected_improvement,
        }


class FestivalPortfolioAnalyzer:
    """Analyze and optimize festival portfolios."""
    
    def __init__(self, festival_context: Optional[Dict[str, Any]] = None):
        self.festival_context = festival_context or {}
        
        # Target portfolio composition
        self.target_composition = {
            'headliner_ratio': 0.15,  # 15% headliners
            'sub_headliner_ratio': 0.20,  # 20% sub-headliners
            'supporting_ratio': 0.40,  # 40% supporting
            'early_day_ratio': 0.25,  # 25% early day
        }
        
        # Budget allocation targets
        self.budget_allocation = {
            'headliner_ratio': 0.50,  # 50% of budget to headliners
            'sub_headliner_ratio': 0.30,  # 30% to sub-headliners
            'supporting_ratio': 0.15,  # 15% to supporting
            'early_day_ratio': 0.05,  # 5% to early day
        }
    
    def analyze_portfolio(
        self,
        festival_key: str,
        edition_key: str,
        edition_year: int,
        artists: List[Dict[str, Any]],
        total_budget: Optional[float] = None,
    ) -> PortfolioMetrics:
        """Analyze current portfolio composition and metrics."""
        
        # Count artists by tier
        tier_counts = self._count_by_tier(artists)
        
        # Calculate factor averages
        factor_averages = self._calculate_factor_averages(artists)
        
        # Calculate diversity score
        diversity_score = self._calculate_diversity_score(artists)
        
        # Calculate budget allocation if budget provided
        budget_metrics = self._calculate_budget_metrics(artists, total_budget) if total_budget else {}
        
        # Calculate efficiency metrics
        efficiency_metrics = self._calculate_efficiency_metrics(
            artists,
            total_budget,
            factor_averages
        )
        
        return PortfolioMetrics(
            festival_key=festival_key,
            edition_key=edition_key,
            edition_year=edition_year,
            total_artists=len(artists),
            headliner_count=tier_counts.get('HEADLINER', 0),
            sub_headliner_count=tier_counts.get('SUB_HEADLINER', 0),
            supporting_count=tier_counts.get('SUPPORTING', 0),
            early_day_count=tier_counts.get('EARLY_DAY', 0),
            portfolio_momentum_avg=factor_averages.get('momentum_avg', 0),
            portfolio_momentum_median=factor_averages.get('momentum_median', 0),
            portfolio_risk_avg=factor_averages.get('risk_avg', 0),
            portfolio_value_avg=factor_averages.get('value_avg', 0),
            portfolio_diversity_score=diversity_score,
            **budget_metrics,
            **efficiency_metrics,
        )
    
    def _count_by_tier(self, artists: List[Dict[str, Any]]) -> Dict[str, int]:
        """Count artists by billing tier."""
        tier_counts = {
            'HEADLINER': 0,
            'SUB_HEADLINER': 0,
            'SUPPORTING': 0,
            'EARLY_DAY': 0,
            'DJ_ONLY': 0,
        }
        
        for artist in artists:
            tier = artist.get('billing_tier', 'UNKNOWN').upper()
            if tier in tier_counts:
                tier_counts[tier] += 1
        
        return tier_counts
    
    def _calculate_factor_averages(self, artists: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate average factor scores across portfolio."""
        
        momentum_scores = []
        risk_scores = []
        value_scores = []
        
        for artist in artists:
            factors = artist.get('factors', {})
            momentum_scores.append(factors.get('momentum_score', 0))
            risk_scores.append(factors.get('risk_score', 0))
            value_scores.append(factors.get('value_proposition_score', 0))
        
        return {
            'momentum_avg': statistics.mean(momentum_scores) if momentum_scores else 0,
            'momentum_median': statistics.median(momentum_scores) if momentum_scores else 0,
            'risk_avg': statistics.mean(risk_scores) if risk_scores else 0,
            'value_avg': statistics.mean(value_scores) if value_scores else 0,
        }
    
    def _calculate_diversity_score(self, artists: List[Dict[str, Any]]) -> float:
        """Calculate portfolio diversity score (0-100)."""
        
        if not artists:
            return 0.0
        
        # Genre diversity
        genres = set()
        for artist in artists:
            artist_genres = artist.get('genres', [])
            genres.update(artist_genres)
        
        genre_diversity = min(100, len(genres) * 5)  # 5 points per unique genre
        
        # Geographic diversity
        countries = set()
        for artist in artists:
            country = artist.get('country')
            if country:
                countries.add(country)
        
        geo_diversity = min(100, len(countries) * 10)  # 10 points per unique country
        
        # Tier diversity
        tier_counts = self._count_by_tier(artists)
        tier_variety = sum(1 for count in tier_counts.values() if count > 0)
        tier_diversity = min(100, tier_variety * 20)  # 20 points per tier with artists
        
        # Combine diversities
        overall_diversity = (
            genre_diversity * 0.4 +
            geo_diversity * 0.3 +
            tier_diversity * 0.3
        )
        
        return round(overall_diversity, 2)
    
    def _calculate_budget_metrics(
        self,
        artists: List[Dict[str, Any]],
        total_budget: float,
    ) -> Dict[str, float]:
        """Calculate budget allocation metrics."""
        
        tier_costs = {
            'HEADLINER': 0,
            'SUB_HEADLINER': 0,
            'SUPPORTING': 0,
            'EARLY_DAY': 0,
        }
        
        total_estimated_cost = 0
        
        for artist in artists:
            tier = artist.get('billing_tier', 'UNKNOWN').upper()
            cost = artist.get('estimated_cost', 0)
            
            if tier in tier_costs:
                tier_costs[tier] += cost
                total_estimated_cost += cost
        
        budget_utilization = (total_estimated_cost / total_budget * 100) if total_budget > 0 else 0
        
        return {
            'total_budget': total_budget,
            'headliner_budget': tier_costs.get('HEADLINER', 0),
            'supporting_budget': tier_costs.get('SUPPORTING', 0) + tier_costs.get('EARLY_DAY', 0),
            'budget_utilization': round(budget_utilization, 2),
        }
    
    def _calculate_efficiency_metrics(
        self,
        artists: List[Dict[str, Any]],
        total_budget: Optional[float],
        factor_averages: Dict[str, float],
    ) -> Dict[str, float]:
        """Calculate portfolio efficiency metrics."""
        
        metrics = {}
        
        if total_budget and total_budget > 0:
            # Cost per momentum point
            momentum_avg = factor_averages.get('momentum_avg', 0)
            if momentum_avg > 0:
                metrics['cost_per_momentum'] = round(total_budget / (momentum_avg * len(artists)), 2)
            
            # Cost per attendance (simplified)
            total_expected_attendance = sum(a.get('expected_attendance_impact', 0) for a in artists)
            if total_expected_attendance > 0:
                metrics['cost_per_attendance'] = round(total_budget / total_expected_attendance, 2)
        
        # ROI score (value / cost proxy)
        value_avg = factor_averages.get('value_avg', 0)
        risk_avg = factor_averages.get('risk_avg', 0)
        
        if value_avg > 0:
            roi_score = (value_avg / 100) * (risk_avg / 100) * 100
            metrics['roi_score'] = round(roi_score, 2)
        
        # Overall efficiency score
        efficiency_components = []
        
        if 'cost_per_momentum' in metrics:
            # Lower cost per momentum is better
            cost_efficiency = max(0, 100 - metrics['cost_per_momentum'] / 1000)
            efficiency_components.append(cost_efficiency)
        
        if 'roi_score' in metrics:
            efficiency_components.append(metrics['roi_score'])
        
        if efficiency_components:
            metrics['efficiency_score'] = round(statistics.mean(efficiency_components), 2)
        
        return metrics
    
    def optimize_portfolio(
        self,
        current_portfolio: PortfolioMetrics,
        artists: List[Dict[str, Any]],
        constraints: Optional[Dict[str, Any]] = None,
    ) -> PortfolioOptimization:
        """Generate portfolio optimization recommendations."""
        
        constraints = constraints or {}
        
        # Analyze current composition vs targets
        composition_analysis = self._analyze_composition(current_portfolio)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            composition_analysis,
            current_portfolio,
            constraints
        )
        
        # Calculate optimized metrics (simplified)
        optimized_metrics = self._calculate_optimized_metrics(
            current_portfolio,
            composition_analysis,
            constraints
        )
        
        # Budget reallocation suggestions
        budget_reallocation = self._suggest_budget_reallocation(
            current_portfolio,
            composition_analysis
        )
        
        # Artist change suggestions
        artist_changes = self._suggest_artist_changes(
            artists,
            composition_analysis,
            constraints
        )
        
        # Expected improvement
        expected_improvement = self._calculate_expected_improvement(
            current_portfolio,
            optimized_metrics
        )
        
        return PortfolioOptimization(
            current_metrics=current_portfolio,
            optimized_metrics=optimized_metrics,
            recommendations=recommendations,
            budget_reallocation=budget_reallocation,
            artist_changes=artist_changes,
            expected_improvement=expected_improvement,
        )
    
    def _analyze_composition(self, portfolio: PortfolioMetrics) -> Dict[str, Any]:
        """Analyze portfolio composition against targets."""
        
        total = portfolio.total_artists
        
        current_ratios = {
            'headliner': portfolio.headliner_count / total if total > 0 else 0,
            'sub_headliner': portfolio.sub_headliner_count / total if total > 0 else 0,
            'supporting': portfolio.supporting_count / total if total > 0 else 0,
            'early_day': portfolio.early_day_count / total if total > 0 else 0,
        }
        
        target_ratios = {
            'headliner': self.target_composition['headliner_ratio'],
            'sub_headliner': self.target_composition['sub_headliner_ratio'],
            'supporting': self.target_composition['supporting_ratio'],
            'early_day': self.target_composition['early_day_ratio'],
        }
        
        gaps = {
            tier: current - target
            for tier, (current, target) in zip(current_ratios.keys(), zip(current_ratios.values(), target_ratios.values()))
        }
        
        return {
            'current_ratios': current_ratios,
            'target_ratios': target_ratios,
            'gaps': gaps,
        }
    
    def _generate_recommendations(
        self,
        composition_analysis: Dict[str, Any],
        portfolio: PortfolioMetrics,
        constraints: Dict[str, Any],
    ) -> List[str]:
        """Generate portfolio optimization recommendations."""
        
        recommendations = []
        gaps = composition_analysis['gaps']
        
        # Tier composition recommendations
        if gaps['headliner'] < -0.05:
            recommendations.append("Consider adding 1-2 headliners to strengthen lineup")
        elif gaps['headliner'] > 0.05:
            recommendations.append("Headliner count is above target - consider optimizing costs")
        
        if gaps['supporting'] < -0.10:
            recommendations.append("Add more supporting acts to fill out the lineup")
        
        # Diversity recommendations
        if portfolio.portfolio_diversity_score < 50:
            recommendations.append("Increase genre and geographic diversity")
        
        # Budget recommendations
        if portfolio.budget_utilization and portfolio.budget_utilization > 95:
            recommendations.append("Budget utilization is high - consider cost optimization")
        elif portfolio.budget_utilization and portfolio.budget_utilization < 70:
            recommendations.append("Budget underutilized - consider adding higher-tier artists")
        
        # Efficiency recommendations
        if portfolio.efficiency_score and portfolio.efficiency_score < 50:
            recommendations.append("Improve cost efficiency through better artist selection")
        
        return recommendations
    
    def _calculate_optimized_metrics(
        self,
        current: PortfolioMetrics,
        composition_analysis: Dict[str, Any],
        constraints: Dict[str, Any],
    ) -> PortfolioMetrics:
        """Calculate optimized portfolio metrics (simplified)."""
        
        # This is a simplified optimization
        # In production, this would use proper optimization algorithms
        
        optimized = PortfolioMetrics(
            festival_key=current.festival_key,
            edition_key=current.edition_key,
            edition_year=current.edition_year,
            total_artists=current.total_artists,
            headliner_count=current.headliner_count,
            sub_headliner_count=current.sub_headliner_count,
            supporting_count=current.supporting_count,
            early_day_count=current.early_day_count,
            portfolio_momentum_avg=min(100, current.portfolio_momentum_avg * 1.05),  # 5% improvement
            portfolio_momentum_median=min(100, current.portfolio_momentum_median * 1.05),
            portfolio_risk_avg=min(100, current.portfolio_risk_avg * 1.03),  # 3% improvement
            portfolio_value_avg=min(100, current.portfolio_value_avg * 1.05),  # 5% improvement
            portfolio_diversity_score=min(100, current.portfolio_diversity_score * 1.10),  # 10% improvement
            total_budget=current.total_budget,
            headliner_budget=current.headliner_budget,
            supporting_budget=current.supporting_budget,
            budget_utilization=current.budget_utilization,
            cost_per_momentum=current.cost_per_momentum * 0.95 if current.cost_per_momentum else None,  # 5% improvement
            cost_per_attendance=current.cost_per_attendance * 0.95 if current.cost_per_attendance else None,  # 5% improvement
            roi_score=min(100, current.roi_score * 1.10) if current.roi_score else None,  # 10% improvement
            efficiency_score=min(100, current.efficiency_score * 1.10) if current.efficiency_score else None,  # 10% improvement
            optimization_method="heuristic",
        )
        
        return optimized
    
    def _suggest_budget_reallocation(
        self,
        current: PortfolioMetrics,
        composition_analysis: Dict[str, Any],
    ) -> Dict[str, float]:
        """Suggest budget reallocation."""
        
        reallocation = {}
        
        if current.total_budget:
            gaps = composition_analysis['gaps']
            
            # Reallocate based on composition gaps
            if gaps['headliner'] < 0:
                # Need more headliners - increase budget allocation
                reallocation['headliner_increase'] = current.total_budget * 0.05
            elif gaps['headliner'] > 0:
                # Too many headliners - decrease budget allocation
                reallocation['headliner_decrease'] = -current.total_budget * 0.03
        
        return reallocation
    
    def _suggest_artist_changes(
        self,
        artists: List[Dict[str, Any]],
        composition_analysis: Dict[str, Any],
        constraints: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Suggest specific artist changes."""
        
        changes = []
        gaps = composition_analysis['gaps']
        
        # Find lowest-value artists to potentially replace
        if gaps['supporting'] < 0:
            # Need more supporting acts
            low_value_artists = sorted(
                artists,
                key=lambda a: a.get('factors', {}).get('value_proposition_score', 0)
            )[:3]
            
            for artist in low_value_artists:
                changes.append({
                    'action': 'replace',
                    'artist_key': artist.get('artist_key'),
                    'reason': 'Low value proposition',
                    'suggested_replacement': 'Higher value supporting act',
                })
        
        return changes
    
    def _calculate_expected_improvement(
        self,
        current: PortfolioMetrics,
        optimized: PortfolioMetrics,
    ) -> Dict[str, float]:
        """Calculate expected improvements from optimization."""
        
        improvements = {}
        
        if current.portfolio_momentum_avg > 0:
            improvements['momentum_improvement'] = round(
                ((optimized.portfolio_momentum_avg - current.portfolio_momentum_avg) / current.portfolio_momentum_avg) * 100,
                2
            )
        
        if current.portfolio_diversity_score > 0:
            improvements['diversity_improvement'] = round(
                ((optimized.portfolio_diversity_score - current.portfolio_diversity_score) / current.portfolio_diversity_score) * 100,
                2
            )
        
        if current.efficiency_score:
            improvements['efficiency_improvement'] = round(
                ((optimized.efficiency_score - current.efficiency_score) / current.efficiency_score) * 100,
                2
            )
        
        return improvements