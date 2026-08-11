"""
Historical Backtest System
Implements point-in-time feature construction and booking arbitrage detection per Festival Bloomberg spec
"""
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, date, timedelta
from enum import Enum
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class BacktestPeriod(Enum):
    """Backtest period types"""
    HISTORICAL = "historical"
    CURRENT = "current"
    FUTURE = "future"


class ArbitrageType(Enum):
    """Booking arbitrage types"""
    UNDERPRICED = "underpriced"
    OVERPRICED = "overpriced"
    MOMENTUM_MISMATCH = "momentum_mismatch"
    AUDIENCE_FIT_OPPORTUNITY = "audience_fit_opportunity"


class PlacementScore(Enum):
    """Placement scoring tiers"""
    HEADLINER = "headliner"
    SUB_HEADLINER = "sub_headliner"
    SUPPORTING = "supporting"
    EARLY_DAY = "early_day"


@dataclass
class PointInTimeFeatures:
    """Point-in-time feature set for an artist at a specific date"""
    artist_id: str
    artist_name: str
    as_of_date: date
    # Momentum features
    lastfm_listeners: Optional[int] = None
    lastfm_rank: Optional[int] = None
    rym_rating: Optional[float] = None
    rym_rank: Optional[int] = None
    wikipedia_pageviews: Optional[int] = None
    # Genre features
    primary_genre: Optional[str] = None
    genre_confidence: Optional[float] = None
    # Career stage
    career_stage: Optional[str] = None
    years_active: Optional[int] = None
    # Touring activity
    recent_tour_dates: Optional[int] = None
    # Metadata
    data_quality_score: float = 0.8
    feature_version: str = "v1.0"


@dataclass
class BacktestArtist:
    """Artist in backtest context"""
    artist_id: str
    artist_name: str
    mbid: Optional[str] = None
    qid: Optional[str] = None
    genres: List[str] = field(default_factory=list)
    country: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestLineup:
    """Historical lineup for backtesting"""
    festival_id: str
    festival_name: str
    year: int
    artists: List[BacktestArtist]
    announcement_date: Optional[date] = None
    lineup_cutoff_date: Optional[date] = None
    format_profile: str = "poster_grid"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArbitrageOpportunity:
    """Detected booking arbitrage opportunity"""
    artist_id: str
    artist_name: str
    festival_id: str
    festival_year: int
    arbitrage_type: ArbitrageType
    as_of_date: date
    # Metrics
    momentum_score: float
    placement_score: float
    # Arbitrage metrics
    expected_value: float
    confidence: float
    booking_quote: Optional[float] = None
    currency: str = "USD"
    expected_roi: float = 0.0
    # Context
    actual_placement: Optional[str] = None
    actual_fee: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Result of backtest run"""
    festival_id: str
    festival_name: str
    year: int
    run_id: str
    started_at: datetime
    finished_at: datetime
    # Results
    total_artists: int
    arbitrage_opportunities: List[ArbitrageOpportunity]
    # Performance metrics
    hit_rate: float  # Correct predictions
    precision: float
    recall: float
    f1_score: float
    # Cost analysis
    total_cost: float
    cost_per_prediction: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class PointInTimeFeatureStore:
    """
    Point-in-time feature construction
    Ensures features are constructed using only data available at the prediction time
    """
    
    def __init__(self):
        self._feature_cache: Dict[Tuple[str, date], PointInTimeFeatures] = {}
        logger.info("Point-in-time feature store initialized")
    
    def get_features(self, artist_id: str, as_of_date: date) -> Optional[PointInTimeFeatures]:
        """
        Get point-in-time features for an artist
        
        Args:
            artist_id: Artist identifier
            as_of_date: As-of date for features
            
        Returns:
            PointInTimeFeatures or None
        """
        cache_key = (artist_id, as_of_date)
        
        if cache_key in self._feature_cache:
            return self._feature_cache[cache_key]
        
        # In production, this would query the warehouse with point-in-time logic
        # For now, return None to indicate not implemented
        return None
    
    def construct_features(self, 
                         artist_id: str,
                         artist_name: str,
                         as_of_date: date,
                         raw_data: Dict[str, Any]) -> PointInTimeFeatures:
        """
        Construct point-in-time features from raw data
        
        Args:
            artist_id: Artist identifier
            artist_name: Artist name
            as_of_date: As-of date
            raw_data: Raw data from sources
            
        Returns:
            PointInTimeFeatures
        """
        # Filter data to only include observations before as_of_date
        filtered_data = self._filter_point_in_time(raw_data, as_of_date)
        
        # Construct features
        features = PointInTimeFeatures(
            artist_id=artist_id,
            artist_name=artist_name,
            as_of_date=as_of_date,
            lastfm_listeners=filtered_data.get('lastfm_listeners'),
            lastfm_rank=filtered_data.get('lastfm_rank'),
            rym_rating=filtered_data.get('rym_rating'),
            rym_rank=filtered_data.get('rym_rank'),
            wikipedia_pageviews=filtered_data.get('wikipedia_pageviews'),
            primary_genre=filtered_data.get('primary_genre'),
            genre_confidence=filtered_data.get('genre_confidence'),
            career_stage=filtered_data.get('career_stage'),
            years_active=filtered_data.get('years_active'),
            recent_tour_dates=filtered_data.get('recent_tour_dates'),
            data_quality_score=filtered_data.get('data_quality_score', 0.8),
            feature_version="v1.0"
        )
        
        # Cache features
        cache_key = (artist_id, as_of_date)
        self._feature_cache[cache_key] = features
        
        return features
    
    def _filter_point_in_time(self, raw_data: Dict[str, Any], as_of_date: date) -> Dict[str, Any]:
        """
        Filter raw data to only include observations before as_of_date
        
        Args:
            raw_data: Raw data with timestamps
            as_of_date: As-of date
            
        Returns:
            Filtered data
        """
        filtered = {}
        
        for key, value in raw_data.items():
            if isinstance(value, dict) and 'observed_at' in value:
                observed_date = value['observed_at']
                if isinstance(observed_date, str):
                    observed_date = datetime.fromisoformat(observed_date).date()
                
                if observed_date <= as_of_date:
                    filtered[key] = value
            else:
                filtered[key] = value
        
        return filtered
    
    def clear_cache(self):
        """Clear feature cache"""
        self._feature_cache.clear()
        logger.info("Feature cache cleared")


class MomentumScorer:
    """
    Momentum scoring for artists
    Implements Festival Bloomberg momentum scoring from Last.fm, RYM, Wikipedia
    """
    
    def __init__(self):
        self._scoring_weights = {
            'lastfm_listeners': 0.4,
            'lastfm_rank': 0.2,
            'rym_rating': 0.2,
            'rym_rank': 0.1,
            'wikipedia_pageviews': 0.1
        }
        logger.info("Momentum scorer initialized")
    
    def calculate_score(self, features: PointInTimeFeatures) -> float:
        """
        Calculate momentum score from features
        
        Args:
            features: Point-in-time features
            
        Returns:
            Momentum score (0-100)
        """
        score = 0.0
        components = 0
        
        # Last.fm listeners (normalized)
        if features.lastfm_listeners:
            # Log-normalize listeners
            normalized = np.log1p(features.lastfm_listeners) / np.log1p(10_000_000)
            score += normalized * self._scoring_weights['lastfm_listeners'] * 100
            components += self._scoring_weights['lastfm_listeners']
        
        # Last.fm rank (inverse)
        if features.lastfm_rank:
            normalized = 1 - (features.lastfm_rank / 10_000)  # Assume max rank 10K
            score += max(0, normalized) * self._scoring_weights['lastfm_rank'] * 100
            components += self._scoring_weights['lastfm_rank']
        
        # RYM rating
        if features.rym_rating:
            normalized = (features.rym_rating - 1) / 4  # Normalize 1-5 to 0-1
            score += normalized * self._scoring_weights['rym_rating'] * 100
            components += self._scoring_weights['rym_rating']
        
        # RYM rank (inverse)
        if features.rym_rank:
            normalized = 1 - (features.rym_rank / 5_000)  # Assume max rank 5K
            score += max(0, normalized) * self._scoring_weights['rym_rank'] * 100
            components += self._scoring_weights['rym_rank']
        
        # Wikipedia pageviews (normalized)
        if features.wikipedia_pageviews:
            normalized = np.log1p(features.wikipedia_pageviews) / np.log1p(1_000_000)
            score += normalized * self._scoring_weights['wikipedia_pageviews'] * 100
            components += self._scoring_weights['wikipedia_pageviews']
        
        # Normalize by components used
        if components > 0:
            score = score / components
        
        return min(100, max(0, score))
    
    def calculate_trend(self, features_history: List[PointInTimeFeatures]) -> str:
        """
        Calculate momentum trend from feature history
        
        Args:
            features_history: Historical features
            
        Returns:
            Trend: 'rising', 'falling', 'stable'
        """
        if len(features_history) < 2:
            return 'stable'
        
        scores = [self.calculate_score(f) for f in features_history]
        
        # Calculate trend
        recent_avg = np.mean(scores[-3:]) if len(scores) >= 3 else scores[-1]
        earlier_avg = np.mean(scores[:-3]) if len(scores) >= 6 else scores[0]
        
        if recent_avg > earlier_avg * 1.1:
            return 'rising'
        elif recent_avg < earlier_avg * 0.9:
            return 'falling'
        else:
            return 'stable'


class PlacementScorer:
    """
    Placement scoring for festival lineups
    Implements Festival Bloomberg placement scoring with format-specific logic
    """
    
    def __init__(self, format_profile: str = "poster_grid"):
        self.format_profile = format_profile
        logger.info(f"Placement scorer initialized for format: {format_profile}")
    
    def calculate_score(self, 
                       momentum_score: float,
                       features: PointInTimeFeatures,
                       festival_capacity: int) -> Tuple[float, PlacementScore]:
        """
        Calculate placement score and tier
        
        Args:
            momentum_score: Momentum score (0-100)
            features: Point-in-time features
            festival_capacity: Festival capacity
            
        Returns:
            Tuple of (placement_score, placement_tier)
        """
        # Base score from momentum
        placement_score = momentum_score
        
        # Adjust for festival capacity (larger festivals require higher scores)
        capacity_factor = min(1.5, festival_capacity / 100_000)
        placement_score = placement_score / capacity_factor
        
        # Adjust for genre fit (if festival genres known)
        if features.primary_genre:
            # This would be enhanced with festival-specific genre matching
            placement_score *= 1.1
        
        # Format-specific adjustments
        if self.format_profile == "poster_grid":
            # Poster grids emphasize visual hierarchy
            placement_score *= 1.0
        elif self.format_profile == "day_stage_schedule":
            # Schedule formats allow more granular placement
            placement_score *= 1.05
        
        # Determine placement tier
        if placement_score >= 80:
            tier = PlacementScore.HEADLINER
        elif placement_score >= 60:
            tier = PlacementScore.SUB_HEADLINER
        elif placement_score >= 40:
            tier = PlacementScore.SUPPORTING
        else:
            tier = PlacementScore.EARLY_DAY
        
        return min(100, placement_score), tier
    
    def predict_placement(self, 
                        momentum_score: float,
                        features: PointInTimeFeatures,
                        festival_capacity: int,
                        current_lineup: List[str]) -> Tuple[float, PlacementScore]:
        """
        Predict placement considering current lineup state
        
        Args:
            momentum_score: Momentum score
            features: Point-in-time features
            festival_capacity: Festival capacity
            current_lineup: Current lineup artist IDs
            
        Returns:
            Tuple of (placement_score, placement_tier)
        """
        base_score, tier = self.calculate_score(momentum_score, features, festival_capacity)
        
        # Adjust for lineup balance (avoid genre saturation)
        if features.primary_genre:
            genre_count = sum(1 for artist in current_lineup if artist == features.primary_genre)
            if genre_count > 5:  # Too many of same genre
                base_score *= 0.9
        
        return base_score, tier


class ArbitrageDetector:
    """
    Booking arbitrage detection
    Implements Festival Bloomberg arbitrage detection logic
    """
    
    def __init__(self):
        self._momentum_scorer = MomentumScorer()
        logger.info("Arbitrage detector initialized")
    
    def detect_opportunities(self,
                           lineup: BacktestLineup,
                           feature_store: PointInTimeFeatureStore,
                           cutoff_date: date,
                           placement_scorer: PlacementScorer) -> List[ArbitrageOpportunity]:
        """
        Detect arbitrage opportunities in a lineup
        
        Args:
            lineup: Historical lineup
            feature_store: Point-in-time feature store
            cutoff_date: Lineup cutoff date
            placement_scorer: Placement scorer
            
        Returns:
            List of arbitrage opportunities
        """
        opportunities = []
        
        for artist in lineup.artists:
            # Get point-in-time features
            features = feature_store.get_features(artist.artist_id, cutoff_date)
            
            if not features:
                logger.warning(f"No features available for artist {artist.artist_name}")
                continue
            
            # Calculate momentum score
            momentum_score = self._momentum_scorer.calculate_score(features)
            
            # Calculate placement score
            placement_score, placement_tier = placement_scorer.calculate_score(
                momentum_score, features, 100_000  # Default capacity
            )
            
            # Detect underpriced artists (high momentum, low expected placement)
            if momentum_score > 70 and placement_score < 50:
                opportunity = ArbitrageOpportunity(
                    artist_id=artist.artist_id,
                    artist_name=artist.artist_name,
                    festival_id=lineup.festival_id,
                    festival_year=lineup.year,
                    arbitrage_type=ArbitrageType.UNDERPRICED,
                    as_of_date=cutoff_date,
                    momentum_score=momentum_score,
                    placement_score=placement_score,
                    expected_value=momentum_score - placement_score,
                    confidence=0.8,
                    expected_roi=0.2,
                    metadata={
                        'placement_tier': placement_tier.value,
                        'genres': artist.genres
                    }
                )
                opportunities.append(opportunity)
            
            # Detect momentum mismatch (rising artists not in headliner spots)
            if momentum_score > 60 and placement_tier in [PlacementScore.SUPPORTING, PlacementScore.EARLY_DAY]:
                opportunity = ArbitrageOpportunity(
                    artist_id=artist.artist_id,
                    artist_name=artist.artist_name,
                    festival_id=lineup.festival_id,
                    festival_year=lineup.year,
                    arbitrage_type=ArbitrageType.MOMENTUM_MISMATCH,
                    as_of_date=cutoff_date,
                    momentum_score=momentum_score,
                    placement_score=placement_score,
                    expected_value=momentum_score - placement_score,
                    confidence=0.7,
                    expected_roi=0.15,
                    metadata={
                        'placement_tier': placement_tier.value,
                        'genres': artist.genres
                    }
                )
                opportunities.append(opportunity)
        
        logger.info(f"Detected {len(opportunities)} arbitrage opportunities for {lineup.festival_name} {lineup.year}")
        return opportunities
    
    def evaluate_opportunities(self, 
                             opportunities: List[ArbitrageOpportunity],
                             actual_lineup: Dict[str, str]) -> Dict[str, Any]:
        """
        Evaluate arbitrage opportunities against actual outcomes
        
        Args:
            opportunities: Detected opportunities
            actual_lineup: Actual lineup with placements
            
        Returns:
            Evaluation metrics
        """
        true_positives = 0
        false_positives = 0
        false_negatives = 0
        
        for opp in opportunities:
            actual_placement = actual_lineup.get(opp.artist_id)
            
            if actual_placement:
                # Check if the artist was actually underpriced
                if opp.arbitrage_type == ArbitrageType.UNDERPRICED:
                    if actual_placement in ['headliner', 'sub_headliner']:
                        true_positives += 1
                    else:
                        false_positives += 1
            else:
                false_negatives += 1
        
        precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
        recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        
        return {
            'true_positives': true_positives,
            'false_positives': false_positives,
            'false_negatives': false_negatives,
            'precision': precision,
            'recall': recall,
            'f1_score': f1
        }


class HistoricalBacktester:
    """
    Main historical backtest engine
    Implements Festival Bloomberg backtest protocol
    """
    
    def __init__(self):
        self.feature_store = PointInTimeFeatureStore()
        self.momentum_scorer = MomentumScorer()
        self.arbitrage_detector = ArbitrageDetector()
        self._backtest_results: List[BacktestResult] = []
        
        logger.info("Historical backtester initialized")
    
    def run_backtest(self,
                    lineup: BacktestLineup,
                    cutoff_date: date,
                    placement_scorer: PlacementScorer,
                    actual_outcomes: Optional[Dict[str, str]] = None) -> BacktestResult:
        """
        Run backtest for a single festival edition
        
        Args:
            lineup: Historical lineup
            cutoff_date: Lineup cutoff date
            placement_scorer: Placement scorer
            actual_outcomes: Optional actual outcomes for evaluation
            
        Returns:
            BacktestResult
        """
        run_id = f"{lineup.festival_id}_{lineup.year}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        started_at = datetime.utcnow()
        
        logger.info(f"Starting backtest {run_id} for {lineup.festival_name} {lineup.year}")
        
        # Detect arbitrage opportunities
        opportunities = self.arbitrage_detector.detect_opportunities(
            lineup, self.feature_store, cutoff_date, placement_scorer
        )
        
        # Evaluate if actual outcomes provided
        hit_rate = 0.0
        precision = 0.0
        recall = 0.0
        f1_score = 0.0
        
        if actual_outcomes:
            evaluation = self.arbitrage_detector.evaluate_opportunities(opportunities, actual_outcomes)
            hit_rate = evaluation['true_positives'] / len(opportunities) if opportunities else 0
            precision = evaluation['precision']
            recall = evaluation['recall']
            f1_score = evaluation['f1_score']
        
        finished_at = datetime.utcnow()
        
        result = BacktestResult(
            festival_id=lineup.festival_id,
            festival_name=lineup.festival_name,
            year=lineup.year,
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            total_artists=len(lineup.artists),
            arbitrage_opportunities=opportunities,
            hit_rate=hit_rate,
            precision=precision,
            recall=recall,
            f1_score=f1_score,
            total_cost=0.0,  # Would be calculated from actual usage
            cost_per_prediction=0.0,
            metadata={
                'cutoff_date': cutoff_date.isoformat(),
                'format_profile': lineup.format_profile
            }
        )
        
        self._backtest_results.append(result)
        
        logger.info(f"Backtest {run_id} complete: {len(opportunities)} opportunities, F1={f1_score:.2f}")
        return result
    
    def run_rolling_origin_backtest(self,
                                   festival_id: str,
                                   start_year: int,
                                   end_year: int,
                                   placement_scorer: PlacementScorer) -> List[BacktestResult]:
        """
        Run rolling-origin backtest across multiple years
        
        Args:
            festival_id: Festival identifier
            start_year: Start year
            end_year: End year
            placement_scorer: Placement scorer
            
        Returns:
            List of backtest results
        """
        results = []
        
        for year in range(start_year, end_year + 1):
            # In production, this would load historical lineups
            # For now, skip with warning
            logger.warning(f"Rolling origin backtest for {festival_id} {year} not implemented")
        
        return results
    
    def get_backtest_summary(self) -> Dict[str, Any]:
        """Get summary of all backtest results"""
        if not self._backtest_results:
            return {}
        
        total_opportunities = sum(len(r.arbitrage_opportunities) for r in self._backtest_results)
        avg_hit_rate = np.mean([r.hit_rate for r in self._backtest_results])
        avg_f1 = np.mean([r.f1_score for r in self._backtest_results])
        
        return {
            'total_backtests': len(self._backtest_results),
            'total_opportunities': total_opportunities,
            'average_hit_rate': avg_hit_rate,
            'average_f1_score': avg_f1,
            'festivals_tested': len(set(r.festival_id for r in self._backtest_results))
        }
    
    def clear_results(self):
        """Clear backtest results"""
        self._backtest_results.clear()
        logger.info("Backtest results cleared")


def create_historical_backtester() -> HistoricalBacktester:
    """Factory function to create historical backtester"""
    return HistoricalBacktester()
