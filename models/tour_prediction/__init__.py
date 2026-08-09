"""
Tour Prediction Model - Predicts likelihood of artist touring and festival appearances.
Uses logistic regression and gradient-boosted trees with rolling historical backtests.
"""

import polars as pl
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.calibration import calibration_curve


@dataclass
class TourFeatures:
    """Features for tour prediction"""
    days_since_last_tour: float
    days_since_last_festival: float
    recent_release_days: float
    tour_frequency_365d: float
    festival_frequency_365d: float
    typical_tour_season_match: float
    prior_festival_participation: float
    open_dates_around_festival: float
    distance_from_preceding_date: float
    distance_from_following_date: float
    home_region_proximity: float
    similar_artist_routing_score: float
    venue_size_progression: float
    festival_exclusivity_conflict: float


class TourPredictionModel:
    """
    Predicts whether an artist will tour or appear at festivals.
    
    Uses interpretable baseline (logistic regression) and higher-performance
    model (gradient boosting) with rolling historical backtests.
    """
    
    def __init__(self, model_type: str = "logistic"):
        """
        Initialize the model.
        
        Args:
            model_type: "logistic" or "gradient_boosting"
        """
        self.model_type = model_type
        
        if model_type == "logistic":
            self.model = LogisticRegression(
                random_state=42,
                max_iter=1000,
                class_weight="balanced",
            )
        elif model_type == "gradient_boosting":
            self.model = GradientBoostingClassifier(
                random_state=42,
                n_estimators=100,
                learning_rate=0.1,
                max_depth=3,
            )
        else:
            raise ValueError(f"Unknown model type: {model_type}")
        
        self.feature_names = [
            "days_since_last_tour",
            "days_since_last_festival",
            "recent_release_days",
            "tour_frequency_365d",
            "festival_frequency_365d",
            "typical_tour_season_match",
            "prior_festival_participation",
            "open_dates_around_festival",
            "distance_from_preceding_date",
            "distance_from_following_date",
            "home_region_proximity",
            "similar_artist_routing_score",
            "venue_size_progression",
            "festival_exclusivity_conflict",
        ]
        
        self.is_fitted = False
    
    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """
        Train the model.
        
        Args:
            X: Feature matrix (n_samples, n_features)
            y: Binary labels (1 = will tour, 0 = will not tour)
        """
        self.model.fit(X, y)
        self.is_fitted = True
    
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        Predict probability of touring.
        
        Args:
            X: Feature matrix
        
        Returns:
            Probability array (n_samples,)
        """
        if not self.is_fitted:
            raise RuntimeError("Model must be fitted before prediction")
        
        proba = self.model.predict_proba(X)[:, 1]
        return proba
    
    def predict(self, X: np.ndarray, threshold: float = 0.5) -> np.ndarray:
        """
        Predict binary touring outcome.
        
        Args:
            X: Feature matrix
            threshold: Probability threshold
        
        Returns:
            Binary predictions (n_samples,)
        """
        proba = self.predict_proba(X)
        return (proba >= threshold).astype(int)
    
    def evaluate(self, X: np.ndarray, y: np.ndarray) -> Dict[str, float]:
        """
        Evaluate model performance.
        
        Args:
            X: Feature matrix
            y: True labels
        
        Returns:
            Dictionary of metrics
        """
        proba = self.predict_proba(X)
        pred = self.predict(proba)
        
        metrics = {
            "auc": roc_auc_score(y, proba),
            "brier_score": brier_score_loss(y, proba),
        }
        
        # Calibration metrics
        prob_true, prob_pred = calibration_curve(y, proba, n_bins=10)
        calibration_error = np.mean(np.abs(prob_true - prob_pred))
        metrics["calibration_error"] = calibration_error
        
        return metrics


class RollingBacktester:
    """
    Performs rolling historical backtests for tour prediction.
    
    Trains on historical data and predicts future periods, simulating
    real-world deployment scenarios.
    """
    
    def __init__(self, model: TourPredictionModel):
        """
        Initialize backtester.
        
        Args:
            model: TourPredictionModel instance
        """
        self.model = model
        self.backtest_results = []
    
    def run_backtest(
        self,
        data: pl.DataFrame,
        train_end_dates: List[datetime],
        prediction_horizons: List[int] = [90, 180, 365],
    ) -> List[Dict[str, any]]:
        """
        Run rolling backtest.
        
        Args:
            data: DataFrame with features and labels
            train_end_dates: List of dates to end training
            prediction_horizons: Days to predict into future
        
        Returns:
            List of backtest results
        """
        results = []
        
        for train_end in train_end_dates:
            for horizon in prediction_horizons:
                # Split data
                train_data = data.filter(pl.col("date") <= train_end)
                test_data = data.filter(
                    (pl.col("date") > train_end) &
                    (pl.col("date") <= train_end + timedelta(days=horizon))
                )
                
                if len(train_data) == 0 or len(test_data) == 0:
                    continue
                
                # Extract features and labels
                feature_cols = self.model.feature_names
                X_train = train_data.select(feature_cols).to_numpy()
                y_train = train_data.select("label").to_numpy().ravel()
                X_test = test_data.select(feature_cols).to_numpy()
                y_test = test_data.select("label").to_numpy().ravel()
                
                # Train and predict
                self.model.fit(X_train, y_train)
                proba = self.model.predict_proba(X_test)
                
                # Evaluate
                metrics = self.model.evaluate(X_test, y_test)
                
                results.append({
                    "train_end_date": train_end,
                    "prediction_horizon_days": horizon,
                    "train_size": len(train_data),
                    "test_size": len(test_data),
                    **metrics,
                })
        
        self.backtest_results = results
        return results
    
    def summarize_backtest(self) -> Dict[str, float]:
        """
        Summarize backtest results.
        
        Returns:
            Dictionary of summary statistics
        """
        if not self.backtest_results:
            return {}
        
        aucs = [r["auc"] for r in self.backtest_results]
        brier_scores = [r["brier_score"] for r in self.backtest_results]
        calibration_errors = [r["calibration_error"] for r in self.backtest_results]
        
        return {
            "mean_auc": np.mean(aucs),
            "std_auc": np.std(aucs),
            "mean_brier_score": np.mean(brier_scores),
            "mean_calibration_error": np.mean(calibration_errors),
            "num_backtests": len(self.backtest_results),
        }


def calculate_tour_probability(
    features: TourFeatures,
    model: TourPredictionModel,
) -> Dict[str, float]:
    """
    Calculate tour probabilities for different horizons.
    
    Args:
        features: Tour features for the artist
        model: Fitted TourPredictionModel
    
    Returns:
        Dictionary with probabilities for 90, 180, 365 days
    """
    if not model.is_fitted:
        raise RuntimeError("Model must be fitted before prediction")
    
    # Convert features to array
    feature_array = np.array([
        features.days_since_last_tour,
        features.days_since_last_festival,
        features.recent_release_days,
        features.tour_frequency_365d,
        features.festival_frequency_365d,
        features.typical_tour_season_match,
        features.prior_festival_participation,
        features.open_dates_around_festival,
        features.distance_from_preceding_date,
        features.distance_from_following_date,
        features.home_region_proximity,
        features.similar_artist_routing_score,
        features.venue_size_progression,
        features.festival_exclusivity_conflict,
    ]).reshape(1, -1)
    
    # Get base probability
    base_proba = model.predict_proba(feature_array)[0]
    
    # Adjust for different horizons (simplified approach)
    # Longer horizons = higher probability
    prob_90d = base_proba * 0.7
    prob_180d = base_proba * 0.85
    prob_365d = base_proba
    
    return {
        "tour_probability_90d": min(max(prob_90d, 0), 1),
        "tour_probability_180d": min(max(prob_180d, 0), 1),
        "tour_probability_365d": min(max(prob_365d, 0), 1),
    }
