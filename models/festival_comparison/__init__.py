"""
Festival Comparison Model - Analyzes lineup overlap, genre diversity, and competitive positioning.
"""

import polars as pl
import numpy as np
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from collections import Counter
from scipy.stats import entropy


@dataclass
class FestivalMetrics:
    """Calculated festival comparison metrics"""
    lineup_strength_index: float
    headliner_dependency: float
    genre_entropy: float
    emerging_artist_share: float
    lineup_uniqueness: float
    competitive_overlap: float
    average_artist_momentum: float
    market_fit_score: float


class FestivalComparisonModel:
    """
    Analyzes festival lineups for strength, diversity, and competitive positioning.
    
    Treats festivals as portfolios where artists are holdings, billing position is weight,
    and genre concentration is factor exposure.
    """
    
    def __init__(self):
        self.billing_weights = {
            "headliner": 1.0,
            "sub_headliner": 0.75,
            "main_stage": 0.5,
            "secondary": 0.25,
            "emerging": 0.1,
        }
    
    def calculate_lineup_strength(
        self,
        lineup_data: pl.DataFrame,
        artist_momentum: Dict[str, float],
    ) -> float:
        """
        Calculate lineup strength index.
        
        Args:
            lineup_data: DataFrame with artist_id, billing_tier
            artist_momentum: Dict mapping artist_id to momentum score
        
        Returns:
            Lineup strength index (0-100)
        """
        if len(lineup_data) == 0:
            return 0.0
        
        weighted_momentum = 0.0
        total_weight = 0.0
        
        for row in lineup_data.iter_rows(named=True):
            artist_id = row["artist_id"]
            billing_tier = row["billing_tier"]
            
            weight = self.billing_weights.get(billing_tier, 0.25)
            momentum = artist_momentum.get(artist_id, 50)
            
            weighted_momentum += momentum * weight
            total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        return weighted_momentum / total_weight
    
    def calculate_headliner_dependency(
        self,
        lineup_data: pl.DataFrame,
    ) -> float:
        """
        Calculate dependency on headliners.
        
        Args:
            lineup_data: DataFrame with billing_tier
        
        Returns:
            Headliner dependency ratio (0-1)
        """
        if len(lineup_data) == 0:
            return 0.0
        
        headliner_count = len(lineup_data.filter(pl.col("billing_tier") == "headliner"))
        total_count = len(lineup_data)
        
        return headliner_count / total_count
    
    def calculate_genre_entropy(
        self,
        lineup_data: pl.DataFrame,
        artist_genres: Dict[str, List[str]],
    ) -> float:
        """
        Calculate genre diversity using entropy.
        
        Args:
            lineup_data: DataFrame with artist_id
            artist_genres: Dict mapping artist_id to list of genres
        
        Returns:
            Genre entropy (higher = more diverse)
        """
        if len(lineup_data) == 0:
            return 0.0
        
        # Count genre occurrences
        genre_counts = Counter()
        
        for row in lineup_data.iter_rows(named=True):
            artist_id = row["artist_id"]
            genres = artist_genres.get(artist_id, [])
            
            # Use primary genre if available, else first genre
            if genres:
                primary_genre = genres[0] if isinstance(genres, list) else genres
                genre_counts[primary_genre] += 1
        
        if not genre_counts:
            return 0.0
        
        # Calculate entropy
        counts = np.array(list(genre_counts.values()))
        probabilities = counts / counts.sum()
        
        genre_entropy = entropy(probabilities)
        
        # Normalize by max possible entropy (log of number of genres)
        max_entropy = np.log(len(genre_counts)) if len(genre_counts) > 1 else 1
        normalized_entropy = genre_entropy / max_entropy if max_entropy > 0 else 0
        
        return normalized_entropy
    
    def calculate_emerging_artist_share(
        self,
        lineup_data: pl.DataFrame,
    ) -> float:
        """
        Calculate share of emerging artists.
        
        Args:
            lineup_data: DataFrame with billing_tier
        
        Returns:
            Emerging artist share (0-1)
        """
        if len(lineup_data) == 0:
            return 0.0
        
        emerging_count = len(lineup_data.filter(pl.col("billing_tier") == "emerging"))
        total_count = len(lineup_data)
        
        return emerging_count / total_count
    
    def calculate_lineup_uniqueness(
        self,
        festival_lineup: Set[str],
        competing_lineups: List[Set[str]],
    ) -> float:
        """
        Calculate how unique a festival's lineup is compared to competitors.
        
        Args:
            festival_lineup: Set of artist IDs for the festival
            competing_lineups: List of sets for competing festivals
        
        Returns:
            Lineup uniqueness score (0-1)
        """
        if not festival_lineup:
            return 0.0
        
        # Combine all competing lineups
        competing_artists = set()
        for lineup in competing_lineups:
            competing_artists.update(lineup)
        
        if not competing_artists:
            return 1.0  # Completely unique if no competitors
        
        # Calculate overlap
        overlap = festival_lineup & competing_artists
        uniqueness = 1.0 - (len(overlap) / len(festival_lineup))
        
        return uniqueness
    
    def calculate_competitive_overlap(
        self,
        festival_lineup: Set[str],
        competing_lineups: List[Set[str]],
    ) -> float:
        """
        Calculate competitive overlap with competitors.
        
        Args:
            festival_lineup: Set of artist IDs for the festival
            competing_lineups: List of sets for competing festivals
        
        Returns:
            Competitive overlap score (0-1)
        """
        if not festival_lineup or not competing_lineups:
            return 0.0
        
        overlaps = []
        
        for lineup in competing_lineups:
            if not lineup:
                continue
            
            overlap = festival_lineup & lineup
            overlap_ratio = len(overlap) / len(festival_lineup)
            overlaps.append(overlap_ratio)
        
        if not overlaps:
            return 0.0
        
        return np.mean(overlaps)
    
    def calculate_average_artist_momentum(
        self,
        lineup_data: pl.DataFrame,
        artist_momentum: Dict[str, float],
    ) -> float:
        """
        Calculate average artist momentum in lineup.
        
        Args:
            lineup_data: DataFrame with artist_id
            artist_momentum: Dict mapping artist_id to momentum score
        
        Returns:
            Average momentum (0-100)
        """
        if len(lineup_data) == 0:
            return 0.0
        
        momentums = []
        for row in lineup_data.iter_rows(named=True):
            artist_id = row["artist_id"]
            momentum = artist_momentum.get(artist_id, 50)
            momentums.append(momentum)
        
        return np.mean(momentums) if momentums else 0.0
    
    def calculate_market_fit_score(
        self,
        lineup_data: pl.DataFrame,
        local_market_genres: Dict[str, float],
        artist_genres: Dict[str, List[str]],
    ) -> float:
        """
        Calculate how well lineup fits local market preferences.
        
        Args:
            lineup_data: DataFrame with artist_id
            local_market_genres: Dict mapping genre to market preference score
            artist_genres: Dict mapping artist_id to list of genres
        
        Returns:
            Market fit score (0-100)
        """
        if len(lineup_data) == 0 or not local_market_genres:
            return 50.0
        
        fit_scores = []
        
        for row in lineup_data.iter_rows(named=True):
            artist_id = row["artist_id"]
            genres = artist_genres.get(artist_id, [])
            
            if not genres:
                fit_scores.append(50.0)
                continue
            
            # Use primary genre
            primary_genre = genres[0] if isinstance(genres, list) else genres
            market_preference = local_market_genres.get(primary_genre, 50)
            
            fit_scores.append(market_preference)
        
        return np.mean(fit_scores) if fit_scores else 50.0
    
    def calculate_all_metrics(
        self,
        festival_id: str,
        lineup_data: pl.DataFrame,
        artist_momentum: Dict[str, float],
        artist_genres: Dict[str, List[str]],
        competing_lineups: Optional[List[Set[str]]] = None,
        local_market_genres: Optional[Dict[str, float]] = None,
    ) -> FestivalMetrics:
        """
        Calculate all festival comparison metrics.
        
        Args:
            festival_id: Festival identifier
            lineup_data: DataFrame with artist_id, billing_tier
            artist_momentum: Dict mapping artist_id to momentum score
            artist_genres: Dict mapping artist_id to list of genres
            competing_lineups: Optional list of competing festival lineups
            local_market_genres: Optional local market genre preferences
        
        Returns:
            FestivalMetrics object with all calculated metrics
        """
        festival_artist_set = set(lineup_data["artist_id"].to_list())
        
        metrics = FestivalMetrics(
            lineup_strength_index=self.calculate_lineup_strength(
                lineup_data, artist_momentum
            ),
            headliner_dependency=self.calculate_headliner_dependency(lineup_data),
            genre_entropy=self.calculate_genre_entropy(lineup_data, artist_genres),
            emerging_artist_share=self.calculate_emerging_artist_share(lineup_data),
            lineup_uniqueness=self.calculate_lineup_uniqueness(
                festival_artist_set,
                competing_lineups or [],
            ),
            competitive_overlap=self.calculate_competitive_overlap(
                festival_artist_set,
                competing_lineups or [],
            ),
            average_artist_momentum=self.calculate_average_artist_momentum(
                lineup_data, artist_momentum
            ),
            market_fit_score=self.calculate_market_fit_score(
                lineup_data,
                local_market_genres or {},
                artist_genres,
            ),
        )
        
        return metrics
    
    def compare_festivals(
        self,
        festival_data: Dict[str, pl.DataFrame],
        artist_momentum: Dict[str, float],
        artist_genres: Dict[str, List[str]],
        local_market_genres: Optional[Dict[str, float]] = None,
    ) -> Dict[str, FestivalMetrics]:
        """
        Compare multiple festivals.
        
        Args:
            festival_data: Dict mapping festival_id to lineup DataFrame
            artist_momentum: Dict mapping artist_id to momentum score
            artist_genres: Dict mapping artist_id to list of genres
            local_market_genres: Optional local market genre preferences
        
        Returns:
            Dict mapping festival_id to FestivalMetrics
        """
        # Prepare competing lineups for each festival
        festival_artist_sets = {
            fid: set(df["artist_id"].to_list())
            for fid, df in festival_data.items()
        }
        
        results = {}
        
        for festival_id, lineup_data in festival_data.items():
            competing_lineups = [
                artist_set
                for fid, artist_set in festival_artist_sets.items()
                if fid != festival_id
            ]
            
            metrics = self.calculate_all_metrics(
                festival_id,
                lineup_data,
                artist_momentum,
                artist_genres,
                competing_lineups,
                local_market_genres,
            )
            
            results[festival_id] = metrics
        
        return results
