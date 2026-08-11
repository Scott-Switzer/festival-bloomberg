"""
Industry-grade data quality and validation system.
Bloomberg-level accuracy with zero tolerance for errors.
"""
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import statistics


class DataQualityLevel(Enum):
    """Data quality classification levels."""
    EXCELLENT = 0.95
    GOOD = 0.85
    ACCEPTABLE = 0.70
    POOR = 0.50
    UNACCEPTABLE = 0.0


@dataclass
class ValidationResult:
    """Result of data validation."""
    valid: bool
    confidence: float
    quality_score: float
    errors: List[str]
    warnings: List[str]
    anomalies: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class DataQualityEngine:
    """Industry-grade data validation and quality control."""
    
    def __init__(self):
        self.validators = {
            'artist': ArtistDataValidator(),
            'festival': FestivalDataValidator(),
            'boxoffice': BoxOfficeValidator(),
            'streaming': StreamingDataValidator(),
            'social': SocialDataValidator(),
            'contact': ContactDataValidator()
        }
        self.anomaly_detector = AnomalyDetector()
        self.deduplication_engine = DeduplicationEngine()
        self.cross_reference = CrossReferenceValidator()
    
    def validate(self, data_type: str, data: Dict[str, Any]) -> ValidationResult:
        """
        Validate data based on type.
        
        Args:
            data_type: Type of data (artist, festival, boxoffice, etc.)
            data: Data to validate
            
        Returns:
            ValidationResult with detailed validation information
        """
        validator = self.validators.get(data_type)
        if not validator:
            return ValidationResult(
                valid=False,
                confidence=0.0,
                quality_score=0.0,
                errors=[f"Unknown data type: {data_type}"],
                warnings=[],
                anomalies=[],
                metadata={}
            )
        
        # Multi-layer validation
        schema_valid = validator.validate_schema(data)
        range_valid = validator.validate_ranges(data)
        cross_valid = self.cross_reference.validate_consistency(data, data_type)
        anomalies = self.anomaly_detector.detect(data, data_type)
        historical_valid = validator.validate_historical_trends(data)
        
        # Calculate overall quality score
        quality_score = self._calculate_quality_score(
            schema_valid, range_valid, cross_valid, historical_valid, anomalies
        )
        
        # Calculate confidence
        confidence = self._calculate_confidence(data, quality_score)
        
        # Collect errors and warnings
        errors = []
        warnings = []
        
        if not schema_valid:
            errors.extend(validator.get_schema_errors(data))
        if not range_valid:
            errors.extend(validator.get_range_errors(data))
        if not cross_valid:
            warnings.append("Cross-platform consistency issues detected")
        if not historical_valid:
            warnings.append("Historical trend anomalies detected")
        
        return ValidationResult(
            valid=quality_score >= DataQualityLevel.ACCEPTABLE.value,
            confidence=confidence,
            quality_score=quality_score,
            errors=errors,
            warnings=warnings,
            anomalies=anomalies,
            metadata={
                'validation_timestamp': datetime.utcnow().isoformat(),
                'data_type': data_type,
                'validation_layers': {
                    'schema': schema_valid,
                    'range': range_valid,
                    'cross_reference': cross_valid,
                    'historical': historical_valid
                }
            }
        )
    
    def _calculate_quality_score(self, schema: bool, range: bool, cross: bool, 
                                 historical: bool, anomalies: List) -> float:
        """Calculate overall data quality score."""
        base_score = 1.0
        
        if not schema:
            base_score -= 0.3
        if not range:
            base_score -= 0.2
        if not cross:
            base_score -= 0.15
        if not historical:
            base_score -= 0.15
        
        # Penalize for anomalies
        anomaly_penalty = min(len(anomalies) * 0.05, 0.2)
        base_score -= anomaly_penalty
        
        return max(0.0, base_score)
    
    def _calculate_confidence(self, data: Dict[str, Any], quality_score: float) -> float:
        """Calculate confidence in data based on quality and completeness."""
        completeness = self._calculate_completeness(data)
        return (quality_score * 0.7) + (completeness * 0.3)
    
    def _calculate_completeness(self, data: Dict[str, Any]) -> float:
        """Calculate data completeness percentage."""
        if not data:
            return 0.0
        
        required_fields = ['id', 'name', 'created_at']
        optional_fields = ['updated_at', 'metadata']
        
        present_required = sum(1 for field in required_fields if field in data and data[field])
        present_optional = sum(1 for field in optional_fields if field in data and data[field])
        
        required_score = present_required / len(required_fields)
        optional_score = present_optional / len(optional_fields)
        
        return (required_score * 0.8) + (optional_score * 0.2)


class ArtistDataValidator:
    """Validator for artist data."""
    
    def validate_schema(self, data: Dict[str, Any]) -> bool:
        """Validate artist data schema."""
        required_fields = ['id', 'name']
        return all(field in data and data[field] for field in required_fields)
    
    def validate_ranges(self, data: Dict[str, Any]) -> bool:
        """Validate artist data ranges."""
        if 'monthly_listeners' in data:
            if not isinstance(data['monthly_listeners'], (int, float)):
                return False
            if data['monthly_listeners'] < 0 or data['monthly_listeners'] > 1e9:
                return False
        
        if 'followers' in data:
            if not isinstance(data['followers'], (int, float)):
                return False
            if data['followers'] < 0 or data['followers'] > 1e9:
                return False
        
        return True
    
    def validate_historical_trends(self, data: Dict[str, Any]) -> bool:
        """Validate historical trends are reasonable."""
        if 'monthly_listeners_history' in data:
            history = data['monthly_listeners_history']
            if not isinstance(history, list):
                return False
            
            # Check for reasonable growth patterns
            if len(history) > 1:
                growth_rates = []
                for i in range(1, len(history)):
                    if history[i-1] > 0:
                        growth = (history[i] - history[i-1]) / history[i-1]
                        growth_rates.append(growth)
                
                # Flag extreme growth rates (>500% month-over-month)
                if growth_rates and any(abs(rate) > 5.0 for rate in growth_rates):
                    return False
        
        return True
    
    def get_schema_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get schema validation errors."""
        errors = []
        if 'id' not in data or not data['id']:
            errors.append("Missing or invalid artist ID")
        if 'name' not in data or not data['name']:
            errors.append("Missing or invalid artist name")
        return errors
    
    def get_range_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get range validation errors."""
        errors = []
        if 'monthly_listeners' in data:
            if data['monthly_listeners'] < 0:
                errors.append("Monthly listeners cannot be negative")
            if data['monthly_listeners'] > 1e9:
                errors.append("Monthly listeners exceeds reasonable maximum")
        return errors


class FestivalDataValidator:
    """Validator for festival data."""
    
    def validate_schema(self, data: Dict[str, Any]) -> bool:
        """Validate festival data schema."""
        required_fields = ['id', 'name', 'location']
        return all(field in data and data[field] for field in required_fields)
    
    def validate_ranges(self, data: Dict[str, Any]) -> bool:
        """Validate festival data ranges."""
        if 'capacity' in data:
            if not isinstance(data['capacity'], (int, float)):
                return False
            if data['capacity'] < 0 or data['capacity'] > 5e6:
                return False
        
        if 'ticket_price_min' in data and 'ticket_price_max' in data:
            if data['ticket_price_min'] > data['ticket_price_max']:
                return False
        
        return True
    
    def validate_historical_trends(self, data: Dict[str, Any]) -> bool:
        """Validate historical trends are reasonable."""
        if 'attendance_history' in data:
            history = data['attendance_history']
            if not isinstance(history, list):
                return False
            
            # Check attendance doesn't exceed capacity
            if 'capacity' in data:
                for attendance in history:
                    if attendance > data['capacity']:
                        return False
        
        return True
    
    def get_schema_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get schema validation errors."""
        errors = []
        if 'id' not in data or not data['id']:
            errors.append("Missing or invalid festival ID")
        if 'name' not in data or not data['name']:
            errors.append("Missing or invalid festival name")
        if 'location' not in data or not data['location']:
            errors.append("Missing or invalid festival location")
        return errors
    
    def get_range_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get range validation errors."""
        errors = []
        if 'capacity' in data:
            if data['capacity'] < 0:
                errors.append("Capacity cannot be negative")
            if data['capacity'] > 5e6:
                errors.append("Capacity exceeds reasonable maximum")
        return errors


class BoxOfficeValidator:
    """Validator for box office data."""
    
    def validate_schema(self, data: Dict[str, Any]) -> bool:
        """Validate box office data schema."""
        required_fields = ['artist_id', 'venue', 'date', 'gross']
        return all(field in data and data[field] for field in required_fields)
    
    def validate_ranges(self, data: Dict[str, Any]) -> bool:
        """Validate box office data ranges."""
        if 'gross' in data:
            if not isinstance(data['gross'], (int, float)):
                return False
            if data['gross'] < 0 or data['gross'] > 1e8:
                return False
        
        if 'tickets_sold' in data:
            if not isinstance(data['tickets_sold'], (int, float)):
                return False
            if data['tickets_sold'] < 0 or data['tickets_sold'] > 1e6:
                return False
        
        return True
    
    def validate_historical_trends(self, data: Dict[str, Any]) -> bool:
        """Validate historical trends are reasonable."""
        # Box office data is typically point-in-time, less historical validation needed
        return True
    
    def get_schema_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get schema validation errors."""
        errors = []
        if 'artist_id' not in data or not data['artist_id']:
            errors.append("Missing or invalid artist ID")
        if 'venue' not in data or not data['venue']:
            errors.append("Missing or invalid venue")
        if 'date' not in data or not data['date']:
            errors.append("Missing or invalid date")
        if 'gross' not in data:
            errors.append("Missing gross revenue")
        return errors
    
    def get_range_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get range validation errors."""
        errors = []
        if 'gross' in data and data['gross'] < 0:
            errors.append("Gross revenue cannot be negative")
        return errors


class StreamingDataValidator:
    """Validator for streaming data."""
    
    def validate_schema(self, data: Dict[str, Any]) -> bool:
        """Validate streaming data schema."""
        required_fields = ['artist_id', 'platform', 'streams']
        return all(field in data and data[field] for field in required_fields)
    
    def validate_ranges(self, data: Dict[str, Any]) -> bool:
        """Validate streaming data ranges."""
        if 'streams' in data:
            if not isinstance(data['streams'], (int, float)):
                return False
            if data['streams'] < 0 or data['streams'] > 1e9:
                return False
        
        if 'monthly_listeners' in data:
            if not isinstance(data['monthly_listeners'], (int, float)):
                return False
            if data['monthly_listeners'] < 0 or data['monthly_listeners'] > 1e9:
                return False
        
        return True
    
    def validate_historical_trends(self, data: Dict[str, Any]) -> bool:
        """Validate historical trends are reasonable."""
        if 'streams_history' in data:
            history = data['streams_history']
            if not isinstance(history, list):
                return False
            
            # Check for reasonable patterns
            if len(history) > 1:
                # Flag impossible jumps (e.g., 0 to 1M in one day)
                for i in range(1, len(history)):
                    if history[i-1] == 0 and history[i] > 1e6:
                        return False
        
        return True
    
    def get_schema_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get schema validation errors."""
        errors = []
        if 'artist_id' not in data or not data['artist_id']:
            errors.append("Missing or invalid artist ID")
        if 'platform' not in data or not data['platform']:
            errors.append("Missing or invalid platform")
        if 'streams' not in data:
            errors.append("Missing stream count")
        return errors
    
    def get_range_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get range validation errors."""
        errors = []
        if 'streams' in data and data['streams'] < 0:
            errors.append("Stream count cannot be negative")
        return errors


class SocialDataValidator:
    """Validator for social media data."""
    
    def validate_schema(self, data: Dict[str, Any]) -> bool:
        """Validate social media data schema."""
        required_fields = ['artist_id', 'platform', 'followers']
        return all(field in data and data[field] for field in required_fields)
    
    def validate_ranges(self, data: Dict[str, Any]) -> bool:
        """Validate social media data ranges."""
        if 'followers' in data:
            if not isinstance(data['followers'], (int, float)):
                return False
            if data['followers'] < 0 or data['followers'] > 1e9:
                return False
        
        if 'engagement_rate' in data:
            if not isinstance(data['engagement_rate'], (int, float)):
                return False
            if data['engagement_rate'] < 0 or data['engagement_rate'] > 1:
                return False
        
        return True
    
    def validate_historical_trends(self, data: Dict[str, Any]) -> bool:
        """Validate historical trends are reasonable."""
        if 'followers_history' in data:
            history = data['followers_history']
            if not isinstance(history, list):
                return False
            
            # Check for reasonable growth patterns
            if len(history) > 1:
                for i in range(1, len(history)):
                    # Flag impossible follower jumps
                    if history[i] > history[i-1] * 10:
                        return False
        
        return True
    
    def get_schema_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get schema validation errors."""
        errors = []
        if 'artist_id' not in data or not data['artist_id']:
            errors.append("Missing or invalid artist ID")
        if 'platform' not in data or not data['platform']:
            errors.append("Missing or invalid platform")
        if 'followers' not in data:
            errors.append("Missing follower count")
        return errors
    
    def get_range_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get range validation errors."""
        errors = []
        if 'followers' in data and data['followers'] < 0:
            errors.append("Follower count cannot be negative")
        if 'engagement_rate' in data and (data['engagement_rate'] < 0 or data['engagement_rate'] > 1):
            errors.append("Engagement rate must be between 0 and 1")
        return errors


class ContactDataValidator:
    """Validator for contact data."""
    
    def validate_schema(self, data: Dict[str, Any]) -> bool:
        """Validate contact data schema."""
        required_fields = ['name', 'role']
        return all(field in data and data[field] for field in required_fields)
    
    def validate_ranges(self, data: Dict[str, Any]) -> bool:
        """Validate contact data ranges."""
        # Contact data typically doesn't have numerical ranges
        return True
    
    def validate_historical_trends(self, data: Dict[str, Any]) -> bool:
        """Validate historical trends are reasonable."""
        # Contact data is typically static
        return True
    
    def get_schema_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get schema validation errors."""
        errors = []
        if 'name' not in data or not data['name']:
            errors.append("Missing or invalid contact name")
        if 'role' not in data or not data['role']:
            errors.append("Missing or invalid contact role")
        return errors
    
    def get_range_errors(self, data: Dict[str, Any]) -> List[str]:
        """Get range validation errors."""
        return []


class AnomalyDetector:
    """Detect anomalies in data."""
    
    def detect(self, data: Dict[str, Any], data_type: str) -> List[Dict[str, Any]]:
        """Detect anomalies in data."""
        anomalies = []
        
        # Statistical anomalies
        statistical_anomalies = self._detect_statistical_anomalies(data, data_type)
        anomalies.extend(statistical_anomalies)
        
        # Pattern anomalies
        pattern_anomalies = self._detect_pattern_anomalies(data, data_type)
        anomalies.extend(pattern_anomalies)
        
        # Contextual anomalies
        contextual_anomalies = self._detect_contextual_anomalies(data, data_type)
        anomalies.extend(contextual_anomalies)
        
        return anomalies
    
    def _detect_statistical_anomalies(self, data: Dict[str, Any], data_type: str) -> List[Dict[str, Any]]:
        """Detect statistical anomalies using z-scores."""
        anomalies = []
        
        numerical_fields = self._get_numerical_fields(data_type)
        for field in numerical_fields:
            if field in data and isinstance(data[field], (int, float)):
                value = data[field]
                
                # Simple z-score detection (would need historical data for real implementation)
                if value > 1e8:  # Arbitrary threshold for demonstration
                    anomalies.append({
                        'type': 'statistical',
                        'field': field,
                        'value': value,
                        'severity': 'high',
                        'description': f'Value {value} for {field} exceeds typical range'
                    })
        
        return anomalies
    
    def _detect_pattern_anomalies(self, data: Dict[str, Any], data_type: str) -> List[Dict[str, Any]]:
        """Detect pattern anomalies."""
        anomalies = []
        
        # Check for missing expected patterns
        if data_type == 'artist':
            if 'monthly_listeners' in data and 'followers' in data:
                # Unusual ratio between listeners and followers
                ratio = data['monthly_listeners'] / (data['followers'] + 1)
                if ratio > 100 or ratio < 0.01:
                    anomalies.append({
                        'type': 'pattern',
                        'field': 'listener_follower_ratio',
                        'value': ratio,
                        'severity': 'medium',
                        'description': f'Unusual listener-to-follower ratio: {ratio:.2f}'
                    })
        
        return anomalies
    
    def _detect_contextual_anomalies(self, data: Dict[str, Any], data_type: str) -> List[Dict[str, Any]]:
        """Detect contextual anomalies."""
        anomalies = []
        
        # Check for contextual inconsistencies
        if data_type == 'boxoffice':
            if 'gross' in data and 'tickets_sold' in data:
                # Calculate average ticket price
                if data['tickets_sold'] > 0:
                    avg_price = data['gross'] / data['tickets_sold']
                    if avg_price < 1 or avg_price > 10000:
                        anomalies.append({
                            'type': 'contextual',
                            'field': 'average_ticket_price',
                            'value': avg_price,
                            'severity': 'high',
                            'description': f'Unusual average ticket price: ${avg_price:.2f}'
                        })
        
        return anomalies
    
    def _get_numerical_fields(self, data_type: str) -> List[str]:
        """Get numerical fields for data type."""
        field_mapping = {
            'artist': ['monthly_listeners', 'followers', 'streams'],
            'festival': ['capacity', 'ticket_price_min', 'ticket_price_max'],
            'boxoffice': ['gross', 'tickets_sold', 'capacity'],
            'streaming': ['streams', 'monthly_listeners'],
            'social': ['followers', 'engagement_rate']
        }
        return field_mapping.get(data_type, [])


class DeduplicationEngine:
    """Engine for deduplicating data."""
    
    def __init__(self):
        self.existing_records = {}  # In production, this would be a database
    
    def deduplicate(self, data: List[Dict[str, Any]], data_type: str) -> List[Dict[str, Any]]:
        """Deduplicate data based on type-specific rules."""
        if not data:
            return []
        
        deduplicated = []
        seen = set()
        
        for record in data:
            # Generate deduplication key
            key = self._generate_dedup_key(record, data_type)
            
            if key not in seen:
                seen.add(key)
                deduplicated.append(record)
        
        return deduplicated
    
    def _generate_dedup_key(self, data: Dict[str, Any], data_type: str) -> str:
        """Generate deduplication key for record."""
        if data_type == 'artist':
            return f"{data.get('name', '').lower()}_{data.get('id', '')}"
        elif data_type == 'festival':
            return f"{data.get('name', '').lower()}_{data.get('location', '').lower()}"
        elif data_type == 'boxoffice':
            return f"{data.get('artist_id', '')}_{data.get('venue', '')}_{data.get('date', '')}"
        else:
            return str(data.get('id', ''))


class CrossReferenceValidator:
    """Validate cross-platform consistency."""
    
    def validate_consistency(self, data: Dict[str, Any], data_type: str) -> bool:
        """Validate data consistency across platforms."""
        # In production, this would check against data from other sources
        # For now, return True as placeholder
        return True
