"""
Comprehensive historical concert and festival analysis engine.
Analyzes 30+ years of concert/festival data for patterns and insights.
"""
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import statistics
from collections import defaultdict


@dataclass
class FestivalAppearance:
    """Represents an artist's appearance at a festival."""
    festival_id: str
    festival_name: str
    year: int
    position: str  # headliner, sub-headliner, etc.
    stage: str
    day: str
    attendance: int
    artist_performance_score: float
    audience_response: str
    weather_conditions: str


@dataclass
class FestivalPattern:
    """Represents a pattern identified in festival data."""
    pattern_type: str
    description: str
    confidence: float
    evidence: List[Dict[str, Any]]
    recommendations: List[str]


class HistoricalAnalysisEngine:
    """Comprehensive historical analysis of concerts and festivals."""
    
    def __init__(self):
        self.concert_database = HistoricalConcertDatabase()
        self.festival_database = HistoricalFestivalDatabase()
        self.pattern_recognition = PatternRecognitionEngine()
        self.success_correlation = SuccessCorrelationEngine()
        self.weather_analyzer = WeatherImpactAnalyzer()
        self.economic_analyzer = EconomicImpactAnalyzer()
    
    def analyze_artist_festival_history(self, artist_id: str) -> Dict[str, Any]:
        """
        Complete historical analysis of artist's festival appearances.
        
        Args:
            artist_id: Artist identifier
            
        Returns:
            Comprehensive analysis of artist's festival history
        """
        appearances = self.festival_database.get_artist_appearances(artist_id)
        
        if not appearances:
            return {
                'artist_id': artist_id,
                'total_appearances': 0,
                'message': 'No festival appearances found'
            }
        
        return {
            'artist_id': artist_id,
            'total_appearances': len(appearances),
            'festival_types': self._analyze_festival_types(appearances),
            'performance_patterns': self.pattern_recognition.analyze(appearances),
            'success_correlation': self.success_correlation.analyze(appearances),
            'optimal_conditions': self._identify_optimal_conditions(appearances),
            'avoidance_factors': self._identify_avoidance_factors(appearances),
            'evolution': self._track_evolution(appearances),
            'comparable_artists': self._find_comparable_patterns(artist_id),
            'festival_fit_analysis': self._analyze_festival_fit(appearances),
            'geographic_preferences': self._analyze_geographic_preferences(appearances),
            'seasonal_patterns': self._analyze_seasonal_patterns(appearances)
        }
    
    def _analyze_festival_types(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Analyze types of festivals artist has appeared at."""
        festival_types = defaultdict(list)
        
        for appearance in appearances:
            festival_info = self.festival_database.get_festival_info(appearance.festival_id)
            festival_type = festival_info.get('type', 'unknown')
            festival_types[festival_type].append(appearance)
        
        analysis = {}
        for festival_type, type_appearances in festival_types.items():
            analysis[festival_type] = {
                'count': len(type_appearances),
                'success_rate': self._calculate_success_rate(type_appearances),
                'average_performance_score': statistics.mean(
                    [a.artist_performance_score for a in type_appearances]
                ),
                'common_positions': self._get_common_positions(type_appearances)
            }
        
        return analysis
    
    def _identify_optimal_conditions(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Identify optimal conditions for artist's festival success."""
        successful_appearances = [
            a for a in appearances 
            if a.artist_performance_score > 0.7
        ]
        
        if not successful_appearances:
            return {'message': 'No successful appearances to analyze'}
        
        return {
            'optimal_festival_types': self._get_most_common_types(successful_appearances),
            'optimal_positions': self._get_most_common_positions(successful_appearances),
            'optimal_stages': self._get_most_common_stages(successful_appearances),
            'optimal_days': self._get_most_common_days(successful_appearances),
            'optimal_weather': self._analyze_weather_conditions(successful_appearances),
            'optimal_attendance_range': self._analyze_attendance_range(successful_appearances)
        }
    
    def _identify_avoidance_factors(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Identify factors to avoid for artist's festival success."""
        unsuccessful_appearances = [
            a for a in appearances 
            if a.artist_performance_score < 0.5
        ]
        
        if not unsuccessful_appearances:
            return {'message': 'No unsuccessful appearances to analyze'}
        
        return {
            'avoid_festival_types': self._get_most_common_types(unsuccessful_appearances),
            'avoid_positions': self._get_most_common_positions(unsuccessful_appearances),
            'avoid_stages': self._get_most_common_stages(unsuccessful_appearances),
            'avoid_weather': self._analyze_weather_conditions(unsuccessful_appearances),
            'risk_factors': self._identify_risk_factors(unsuccessful_appearances)
        }
    
    def _track_evolution(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Track artist's evolution across festival appearances."""
        if len(appearances) < 2:
            return {'message': 'Insufficient data for evolution analysis'}
        
        # Sort by year
        sorted_appearances = sorted(appearances, key=lambda a: a.year)
        
        evolution = {
            'position_progression': self._analyze_position_progression(sorted_appearances),
            'performance_trend': self._analyze_performance_trend(sorted_appearances),
            'festival_scale_progression': self._analyze_scale_progression(sorted_appearances),
            'geographic_expansion': self._analyze_geographic_expansion(sorted_appearances)
        }
        
        return evolution
    
    def _find_comparable_patterns(self, artist_id: str) -> List[Dict[str, Any]]:
        """Find artists with similar festival patterns."""
        artist_appearances = self.festival_database.get_artist_appearances(artist_id)
        artist_profile = self._create_artist_profile(artist_appearances)
        
        # Find similar artists based on festival patterns
        all_artists = self.festival_database.get_all_artists()
        comparable = []
        
        for other_artist_id in all_artists:
            if other_artist_id == artist_id:
                continue
            
            other_appearances = self.festival_database.get_artist_appearances(other_artist_id)
            other_profile = self._create_artist_profile(other_appearances)
            
            similarity = self._calculate_profile_similarity(artist_profile, other_profile)
            
            if similarity > 0.7:  # High similarity threshold
                comparable.append({
                    'artist_id': other_artist_id,
                    'similarity_score': similarity,
                    'similar_patterns': self._identify_similar_patterns(artist_profile, other_profile)
                })
        
        # Sort by similarity and return top 10
        comparable.sort(key=lambda x: x['similarity_score'], reverse=True)
        return comparable[:10]
    
    def _analyze_festival_fit(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Analyze artist's fit with different festival characteristics."""
        fit_analysis = {}
        
        for appearance in appearances:
            festival_info = self.festival_database.get_festival_info(appearance.festival_id)
            
            fit_score = self._calculate_fit_score(appearance, festival_info)
            
            fit_analysis[appearance.festival_id] = {
                'festival_name': appearance.festival_name,
                'fit_score': fit_score,
                'festival_characteristics': {
                    'genre_focus': festival_info.get('genre_focus'),
                    'capacity': festival_info.get('capacity'),
                    'location_type': festival_info.get('location_type'),
                    'audience_demographics': festival_info.get('audience_demographics')
                },
                'artist_performance': appearance.artist_performance_score,
                'recommendation': self._generate_fit_recommendation(fit_score)
            }
        
        return fit_analysis
    
    def _analyze_geographic_preferences(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Analyze artist's geographic preferences for festivals."""
        locations = defaultdict(list)
        
        for appearance in appearances:
            festival_info = self.festival_database.get_festival_info(appearance.festival_id)
            location = festival_info.get('location', 'unknown')
            locations[location].append(appearance)
        
        analysis = {}
        for location, location_appearances in locations.items():
            analysis[location] = {
                'count': len(location_appearances),
                'success_rate': self._calculate_success_rate(location_appearances),
                'average_performance': statistics.mean(
                    [a.artist_performance_score for a in location_appearances]
                )
            }
        
        return analysis
    
    def _analyze_seasonal_patterns(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Analyze artist's seasonal patterns for festival appearances."""
        seasons = defaultdict(list)
        
        for appearance in appearances:
            festival_info = self.festival_database.get_festival_info(appearance.festival_id)
            date = festival_info.get('date')
            if date:
                season = self._get_season(date)
                seasons[season].append(appearance)
        
        analysis = {}
        for season, season_appearances in seasons.items():
            analysis[season] = {
                'count': len(season_appearances),
                'success_rate': self._calculate_success_rate(season_appearances),
                'average_performance': statistics.mean(
                    [a.artist_performance_score for a in season_appearances]
                )
            }
        
        return analysis
    
    def analyze_festival_success_patterns(self, festival_id: str) -> Dict[str, Any]:
        """
        Analyze what makes this festival successful.
        
        Args:
            festival_id: Festival identifier
            
        Returns:
            Comprehensive analysis of festival success patterns
        """
        historical_lineups = self.festival_database.get_historical_lineups(festival_id)
        
        if not historical_lineups:
            return {
                'festival_id': festival_id,
                'message': 'No historical data available'
            }
        
        return {
            'festival_id': festival_id,
            'successful_lineup_patterns': self._identify_successful_lineup_patterns(historical_lineups),
            'genre_mix_optimization': self._analyze_genre_mix(historical_lineups),
            'headliner_strategy': self._analyze_headliner_strategy(historical_lineups),
            'emerging_artist_success': self._analyze_emerging_artist_performance(historical_lineups),
            'weather_impact': self.weather_analyzer.analyze_weather_impact(festival_id),
            'economic_factors': self.economic_analyzer.analyze_economic_factors(festival_id),
            'competitive_positioning': self._analyze_competitive_dynamics(festival_id),
            'audience_satisfaction': self._analyze_audience_satisfaction(historical_lineups)
        }
    
    def _identify_successful_lineup_patterns(self, lineups: List[Dict]) -> List[FestivalPattern]:
        """Identify patterns in successful lineups."""
        successful_lineups = [
            lineup for lineup in lineups 
            if lineup.get('success_score', 0) > 0.7
        ]
        
        patterns = []
        
        # Pattern: Genre diversity
        genre_diversity = self._analyze_genre_diversity(successful_lineups)
        patterns.append(FestivalPattern(
            pattern_type='genre_diversity',
            description=f'Optimal genre diversity: {genre_diversity:.2f}',
            confidence=0.8,
            evidence=[{'lineup_year': l['year'], 'genre_diversity': l['genre_diversity']} for l in successful_lineups],
            recommendations=['Maintain genre diversity within optimal range']
        ))
        
        # Pattern: Headliner to emerging artist ratio
        headliner_ratio = self._analyze_headliner_ratio(successful_lineups)
        patterns.append(FestivalPattern(
            pattern_type='headliner_ratio',
            description=f'Optimal headliner to emerging artist ratio: {headliner_ratio:.2f}',
            confidence=0.75,
            evidence=[{'lineup_year': l['year'], 'headliner_ratio': l['headliner_ratio']} for l in successful_lineups],
            recommendations=['Balance established and emerging artists']
        ))
        
        return patterns
    
    def predict_lineup_success(self, festival_id: str, proposed_lineup: List[Dict]) -> Dict[str, Any]:
        """
        Predict success of proposed lineup based on historical patterns.
        
        Args:
            festival_id: Festival identifier
            proposed_lineup: Proposed artist lineup
            
        Returns:
            Success prediction with confidence intervals
        """
        festival_patterns = self.analyze_festival_success_patterns(festival_id)
        lineup_analysis = self._analyze_lineup_composition(proposed_lineup)
        
        prediction = {
            'predicted_attendance': self._predict_attendance(festival_patterns, lineup_analysis),
            'predicted_revenue': self._predict_revenue(festival_patterns, lineup_analysis),
            'success_probability': self._calculate_success_probability(festival_patterns, lineup_analysis),
            'risk_factors': self._identify_risks(festival_patterns, lineup_analysis),
            'optimization_suggestions': self._suggest_optimizations(festival_patterns, lineup_analysis),
            'confidence_interval': self._calculate_confidence_interval(festival_patterns, lineup_analysis),
            'comparable_historical_lineups': self._find_comparable_lineups(festival_id, proposed_lineup)
        }
        
        return prediction
    
    def _predict_attendance(self, festival_patterns: Dict, lineup_analysis: Dict) -> Dict[str, Any]:
        """Predict attendance based on historical patterns and lineup."""
        # Use historical attendance patterns and lineup strength
        base_attendance = festival_patterns.get('base_attendance', 50000)
        lineup_strength = lineup_analysis.get('overall_strength', 0.5)
        
        predicted = base_attendance * (0.8 + 0.4 * lineup_strength)
        
        return {
            'predicted': predicted,
            'range': [predicted * 0.9, predicted * 1.1],
            'confidence': 0.75
        }
    
    def _predict_revenue(self, festival_patterns: Dict, lineup_analysis: Dict) -> Dict[str, Any]:
        """Predict revenue based on historical patterns and lineup."""
        attendance_prediction = self._predict_attendance(festival_patterns, lineup_analysis)
        avg_ticket_price = festival_patterns.get('average_ticket_price', 100)
        
        predicted = attendance_prediction['predicted'] * avg_ticket_price
        
        return {
            'predicted': predicted,
            'range': [predicted * 0.85, predicted * 1.15],
            'confidence': 0.7
        }
    
    def _calculate_success_probability(self, festival_patterns: Dict, lineup_analysis: Dict) -> float:
        """Calculate probability of lineup success."""
        factors = {
            'genre_fit': lineup_analysis.get('genre_fit_score', 0.5),
            'artist_quality': lineup_analysis.get('artist_quality_score', 0.5),
            'balance': lineup_analysis.get('balance_score', 0.5),
            'historical_alignment': self._calculate_historical_alignment(festival_patterns, lineup_analysis)
        }
        
        # Weighted average
        weights = {'genre_fit': 0.3, 'artist_quality': 0.3, 'balance': 0.2, 'historical_alignment': 0.2}
        
        probability = sum(factors[k] * weights[k] for k in factors)
        
        return min(1.0, max(0.0, probability))
    
    def _analyze_lineup_composition(self, lineup: List[Dict]) -> Dict[str, Any]:
        """Analyze composition of proposed lineup."""
        if not lineup:
            return {'message': 'No lineup provided'}
        
        analysis = {
            'total_artists': len(lineup),
            'genre_distribution': self._analyze_genre_distribution(lineup),
            'position_distribution': self._analyze_position_distribution(lineup),
            'artist_quality_scores': [a.get('quality_score', 0.5) for a in lineup],
            'overall_strength': statistics.mean([a.get('quality_score', 0.5) for a in lineup]),
            'genre_fit_score': self._calculate_genre_fit_score(lineup),
            'artist_quality_score': statistics.mean([a.get('quality_score', 0.5) for a in lineup]),
            'balance_score': self._calculate_balance_score(lineup)
        }
        
        return analysis
    
    # Helper methods
    def _calculate_success_rate(self, appearances: List) -> float:
        """Calculate success rate of appearances."""
        if not appearances:
            return 0.0
        successful = sum(1 for a in appearances if a.artist_performance_score > 0.6)
        return successful / len(appearances)
    
    def _get_common_positions(self, appearances: List) -> List[str]:
        """Get most common positions."""
        positions = [a.position for a in appearances]
        return statistics.mode(positions) if positions else []
    
    def _get_most_common_types(self, appearances: List) -> List[str]:
        """Get most common festival types."""
        types = [self.festival_database.get_festival_info(a.festival_id).get('type', 'unknown') 
                 for a in appearances]
        return statistics.mode(types) if types else []
    
    def _get_most_common_stages(self, appearances: List) -> List[str]:
        """Get most common stages."""
        stages = [a.stage for a in appearances]
        return statistics.mode(stages) if stages else []
    
    def _get_most_common_days(self, appearances: List) -> List[str]:
        """Get most common days."""
        days = [a.day for a in appearances]
        return statistics.mode(days) if days else []
    
    def _get_season(self, date_str: str) -> str:
        """Get season from date string."""
        try:
            date = datetime.strptime(date_str, '%Y-%m-%d')
            month = date.month
            if month in [12, 1, 2]:
                return 'winter'
            elif month in [3, 4, 5]:
                return 'spring'
            elif month in [6, 7, 8]:
                return 'summer'
            else:
                return 'fall'
        except:
            return 'unknown'
    
    def _create_artist_profile(self, appearances: List) -> Dict[str, Any]:
        """Create profile from artist appearances."""
        return {
            'festival_types': set([self.festival_database.get_festival_info(a.festival_id).get('type', 'unknown') 
                                  for a in appearances]),
            'positions': set([a.position for a in appearances]),
            'avg_performance': statistics.mean([a.artist_performance_score for a in appearances]),
            'total_appearances': len(appearances)
        }
    
    def _calculate_profile_similarity(self, profile1: Dict, profile2: Dict) -> float:
        """Calculate similarity between two artist profiles."""
        type_overlap = len(profile1['festival_types'] & profile2['festival_types'])
        type_union = len(profile1['festival_types'] | profile2['festival_types'])
        
        position_overlap = len(profile1['positions'] & profile2['positions'])
        position_union = len(profile1['positions'] | profile2['positions'])
        
        type_similarity = type_overlap / type_union if type_union > 0 else 0
        position_similarity = position_overlap / position_union if position_union > 0 else 0
        performance_similarity = 1 - abs(profile1['avg_performance'] - profile2['avg_performance'])
        
        return (type_similarity * 0.4) + (position_similarity * 0.3) + (performance_similarity * 0.3)
    
    def _calculate_fit_score(self, appearance: FestivalAppearance, festival_info: Dict) -> float:
        """Calculate fit score between artist and festival."""
        # Simplified fit calculation
        base_score = appearance.artist_performance_score
        
        # Adjust for position match
        position_fit = 1.0 if appearance.position in ['headliner', 'sub-headliner'] else 0.8
        
        return base_score * position_fit
    
    def _generate_fit_recommendation(self, fit_score: float) -> str:
        """Generate recommendation based on fit score."""
        if fit_score > 0.8:
            return 'Excellent fit - highly recommended'
        elif fit_score > 0.6:
            return 'Good fit - recommended'
        elif fit_score > 0.4:
            return 'Moderate fit - consider with conditions'
        else:
            return 'Poor fit - not recommended'
    
    def _analyze_genre_diversity(self, lineups: List[Dict]) -> float:
        """Analyze genre diversity in lineups."""
        # Simplified calculation
        return 0.7  # Placeholder
    
    def _analyze_headliner_ratio(self, lineups: List[Dict]) -> float:
        """Analyze headliner to emerging artist ratio."""
        # Simplified calculation
        return 0.3  # Placeholder
    
    def _analyze_genre_distribution(self, lineup: List[Dict]) -> Dict[str, int]:
        """Analyze genre distribution in lineup."""
        genres = defaultdict(int)
        for artist in lineup:
            genre = artist.get('genre', 'unknown')
            genres[genre] += 1
        return dict(genres)
    
    def _analyze_position_distribution(self, lineup: List[Dict]) -> Dict[str, int]:
        """Analyze position distribution in lineup."""
        positions = defaultdict(int)
        for artist in lineup:
            position = artist.get('position', 'unknown')
            positions[position] += 1
        return dict(positions)
    
    def _calculate_genre_fit_score(self, lineup: List[Dict]) -> float:
        """Calculate genre fit score."""
        # Simplified calculation
        return 0.7  # Placeholder
    
    def _calculate_balance_score(self, lineup: List[Dict]) -> float:
        """Calculate balance score."""
        # Simplified calculation
        return 0.6  # Placeholder
    
    def _calculate_historical_alignment(self, festival_patterns: Dict, lineup_analysis: Dict) -> float:
        """Calculate alignment with historical patterns."""
        # Simplified calculation
        return 0.7  # Placeholder
    
    def _identify_risks(self, festival_patterns: Dict, lineup_analysis: Dict) -> List[str]:
        """Identify potential risks."""
        risks = []
        
        if lineup_analysis.get('genre_fit_score', 0.5) < 0.5:
            risks.append('Poor genre fit with festival audience')
        
        if lineup_analysis.get('balance_score', 0.5) < 0.5:
            risks.append('Unbalanced lineup distribution')
        
        return risks
    
    def _suggest_optimizations(self, festival_patterns: Dict, lineup_analysis: Dict) -> List[str]:
        """Suggest lineup optimizations."""
        suggestions = []
        
        if lineup_analysis.get('genre_fit_score', 0.5) < 0.7:
            suggestions.append('Consider adding artists from festival core genres')
        
        if lineup_analysis.get('balance_score', 0.5) < 0.7:
            suggestions.append('Balance established and emerging artists')
        
        return suggestions
    
    def _calculate_confidence_interval(self, festival_patterns: Dict, lineup_analysis: Dict) -> Dict[str, float]:
        """Calculate confidence interval for predictions."""
        # Simplified calculation
        return {'lower': 0.6, 'upper': 0.9}
    
    def _find_comparable_lineups(self, festival_id: str, proposed_lineup: List[Dict]) -> List[Dict]:
        """Find comparable historical lineups."""
        # Placeholder implementation
        return []


class HistoricalConcertDatabase:
    """Database for historical concert data."""
    
    def get_concert_history(self, artist_id: str) -> List[Dict]:
        """Get concert history for artist."""
        # Placeholder - would connect to database
        return []


class HistoricalFestivalDatabase:
    """Database for historical festival data."""
    
    def get_artist_appearances(self, artist_id: str) -> List[FestivalAppearance]:
        """Get artist's festival appearances."""
        # Placeholder - would connect to database
        return []
    
    def get_historical_lineups(self, festival_id: str) -> List[Dict]:
        """Get historical lineups for festival."""
        # Placeholder - would connect to database
        return []
    
    def get_festival_info(self, festival_id: str) -> Dict:
        """Get festival information."""
        # Placeholder - would connect to database
        return {}
    
    def get_all_artists(self) -> List[str]:
        """Get all artists in database."""
        # Placeholder - would connect to database
        return []


class PatternRecognitionEngine:
    """Engine for recognizing patterns in data."""
    
    def analyze(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Analyze patterns in appearances."""
        return {
            'temporal_patterns': self._analyze_temporal_patterns(appearances),
            'performance_patterns': self._analyze_performance_patterns(appearances),
            'contextual_patterns': self._analyze_contextual_patterns(appearances)
        }
    
    def _analyze_temporal_patterns(self, appearances: List) -> Dict:
        """Analyze temporal patterns."""
        return {}
    
    def _analyze_performance_patterns(self, appearances: List) -> Dict:
        """Analyze performance patterns."""
        return {}
    
    def _analyze_contextual_patterns(self, appearances: List) -> Dict:
        """Analyze contextual patterns."""
        return {}


class SuccessCorrelationEngine:
    """Engine for correlating factors with success."""
    
    def analyze(self, appearances: List[FestivalAppearance]) -> Dict[str, Any]:
        """Analyze success correlations."""
        return {
            'position_correlation': self._analyze_position_correlation(appearances),
            'genre_correlation': self._analyze_genre_correlation(appearances),
            'weather_correlation': self._analyze_weather_correlation(appearances)
        }
    
    def _analyze_position_correlation(self, appearances: List) -> Dict:
        """Analyze correlation between position and success."""
        return {}
    
    def _analyze_genre_correlation(self, appearances: List) -> Dict:
        """Analyze correlation between genre and success."""
        return {}
    
    def _analyze_weather_correlation(self, appearances: List) -> Dict:
        """Analyze correlation between weather and success."""
        return {}


class WeatherImpactAnalyzer:
    """Analyzer for weather impact on festivals."""
    
    def analyze_weather_impact(self, festival_id: str) -> Dict[str, Any]:
        """Analyze weather impact on festival."""
        return {
            'weather_risk_score': 0.3,
            'historical_weather_impact': [],
            'optimal_weather_conditions': []
        }


class EconomicImpactAnalyzer:
    """Analyzer for economic impact on festivals."""
    
    def analyze_economic_factors(self, festival_id: str) -> Dict[str, Any]:
        """Analyze economic factors for festival."""
        return {
            'economic_context': {},
            'pricing_sensitivity': 0.5,
            'market_conditions': {}
        }
