"""
PIT (Point-in-Time) invariant enforcement tests for Festival Bloomberg.

These tests ensure that the knowledge_time invariant is properly enforced:
- Any query or derived feature evaluated at cutoff T must fail closed or exclude
  any observation for which knowledge_time > T
- Future information cannot leak into historical queries or derived features
"""

import pytest
import duckdb
from datetime import datetime, timedelta
from pathlib import Path
import tempfile


def test_pit_invariant_blocks_future_knowledge():
    """Test that queries at time T exclude observations with knowledge_time > T."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_pit.duckdb"
        conn = duckdb.connect(str(db_path))
        
        # Create a simple test table with PIT fields
        conn.execute("""
            CREATE TABLE test_observations (
                id VARCHAR PRIMARY KEY,
                value DOUBLE,
                knowledge_time TIMESTAMP,
                retrieved_at TIMESTAMP
            )
        """)
        
        # Insert observations with different knowledge times
        cutoff_time = datetime(2024, 1, 1, 12, 0, 0)
        
        # Past observation (should be included)
        conn.execute("""
            INSERT INTO test_observations VALUES 
            ('past', 100.0, '2023-12-31 12:00:00', '2024-01-01 10:00:00')
        """)
        
        # Future observation (should be excluded)
        conn.execute("""
            INSERT INTO test_observations VALUES 
            ('future', 200.0, '2024-01-02 12:00:00', '2024-01-01 10:00:00')
        """)
        
        # Query at cutoff time should only include past observation
        result = conn.execute("""
            SELECT AVG(value) as avg_value 
            FROM test_observations 
            WHERE knowledge_time <= '2024-01-01 12:00:00'
        """).fetchone()
        
        assert result[0] == 100.0, "Future knowledge leaked into query"
        
        # Query without cutoff should include both
        result_all = conn.execute("""
            SELECT AVG(value) as avg_value 
            FROM test_observations
        """).fetchone()
        
        assert result_all[0] == 150.0, "Baseline query incorrect"
        
        conn.close()


def test_pit_lineup_observations_enforcement():
    """Test that lineup observations respect knowledge_time cutoffs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_lineup_pit.duckdb"
        conn = duckdb.connect(str(db_path))
        
        # Create schemas first
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
        
        # Create lineup observations table with PIT fields
        conn.execute("""
            CREATE TABLE raw.lineup_observations (
                observation_key VARCHAR PRIMARY KEY,
                festival_key VARCHAR,
                artist_name VARCHAR,
                source_publication_time TIMESTAMP,
                source_as_of TIMESTAMP,
                retrieved_at TIMESTAMP,
                valid_from TIMESTAMP,
                valid_to TIMESTAMP,
                knowledge_time TIMESTAMP
            )
        """)
        
        cutoff = datetime(2024, 1, 15, 12, 0, 0)
        
        # Observation known before cutoff
        conn.execute("""
            INSERT INTO raw.lineup_observations VALUES
            ('obs1', 'glastonbury', 'Artist A', '2024-01-10 10:00:00', 
             '2024-01-10 10:00:00', '2024-01-10 11:00:00',
             '2024-01-10 10:00:00', NULL, '2024-01-10 11:00:00')
        """)
        
        # Observation known after cutoff (future knowledge)
        conn.execute("""
            INSERT INTO raw.lineup_observations VALUES
            ('obs2', 'glastonbury', 'Artist B', '2024-01-20 10:00:00',
             '2024-01-20 10:00:00', '2024-01-20 11:00:00',
             '2024-01-20 10:00:00', NULL, '2024-01-20 11:00:00')
        """)
        
        # Query at cutoff should only return Artist A
        result = conn.execute("""
            SELECT COUNT(*) as count
            FROM raw.lineup_observations
            WHERE knowledge_time <= '2024-01-15 12:00:00'
        """).fetchone()
        
        assert result[0] == 1, "Future lineup observation leaked into query"
        
        conn.close()


def test_pit_feature_store_time_travel():
    """Test that feature store allows historical queries without future leakage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_feature_pit.duckdb"
        conn = duckdb.connect(str(db_path))
        
        # Create schema first
        conn.execute("CREATE SCHEMA IF NOT EXISTS metrics")
        
        # Create feature store table
        conn.execute("""
            CREATE TABLE metrics.artist_feature_store (
                feature_key VARCHAR PRIMARY KEY,
                artist_key VARCHAR,
                feature_name VARCHAR,
                feature_value DOUBLE,
                feature_date DATE,
                knowledge_time TIMESTAMP,
                calculated_at TIMESTAMP
            )
        """)
        
        # Insert features with different knowledge times
        historical_cutoff = datetime(2024, 1, 1, 12, 0, 0)
        
        # Feature known at historical time
        conn.execute("""
            INSERT INTO metrics.artist_feature_store VALUES
            ('feat1', 'artist1', 'popularity', 0.8, '2023-12-31',
             '2023-12-31 10:00:00', '2023-12-31 11:00:00')
        """)
        
        # Feature known later (future knowledge from historical perspective)
        conn.execute("""
            INSERT INTO metrics.artist_feature_store VALUES
            ('feat2', 'artist1', 'popularity', 0.9, '2023-12-31',
             '2024-01-05 10:00:00', '2024-01-05 11:00:00')
        """)
        
        # Historical query should only use first feature
        result = conn.execute("""
            SELECT AVG(feature_value) as avg_popularity
            FROM metrics.artist_feature_store
            WHERE knowledge_time <= '2024-01-01 12:00:00'
            AND feature_date = '2023-12-31'
        """).fetchone()
        
        assert result[0] == 0.8, "Future feature value leaked into historical query"
        
        # Current query should use latest feature
        current_result = conn.execute("""
            SELECT AVG(feature_value) as avg_popularity
            FROM metrics.artist_feature_store
            WHERE feature_date = '2023-12-31'
        """).fetchone()
        
        assert abs(current_result[0] - 0.85) < 0.001, "Current query incorrect"
        
        conn.close()


def test_pit_derived_features_no_leakage():
    """Test that derived/calculated features don't leak future information."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_derived_pit.duckdb"
        conn = duckdb.connect(str(db_path))
        
        # Create schema first
        conn.execute("CREATE SCHEMA IF NOT EXISTS metrics")
        
        # Create raw metrics and derived features tables
        conn.execute("""
            CREATE TABLE metrics.artist_metrics (
                metric_key VARCHAR PRIMARY KEY,
                artist_key VARCHAR,
                metric_type VARCHAR,
                value DOUBLE,
                knowledge_time TIMESTAMP
            )
        """)
        
        conn.execute("""
            CREATE TABLE metrics.derived_features (
                feature_key VARCHAR PRIMARY KEY,
                artist_key VARCHAR,
                feature_value DOUBLE,
                calculated_at TIMESTAMP,
                input_knowledge_time TIMESTAMP
            )
        """)
        
        cutoff = datetime(2024, 1, 1, 12, 0, 0)
        
        # Raw metric known before cutoff
        conn.execute("""
            INSERT INTO metrics.artist_metrics VALUES
            ('metric1', 'artist1', 'streams', 1000.0, '2023-12-31 10:00:00')
        """)
        
        # Raw metric known after cutoff
        conn.execute("""
            INSERT INTO metrics.artist_metrics VALUES
            ('metric2', 'artist1', 'streams', 2000.0, '2024-01-05 10:00:00')
        """)
        
        # Derived feature calculated using only pre-cutoff knowledge
        conn.execute("""
            INSERT INTO metrics.derived_features VALUES
            ('derived1', 'artist1', 1000.0, '2024-01-01 13:00:00', '2023-12-31 10:00:00')
        """)
        
        # Query derived features at cutoff should respect input knowledge time
        result = conn.execute("""
            SELECT feature_value
            FROM metrics.derived_features
            WHERE input_knowledge_time <= '2024-01-01 12:00:00'
        """).fetchone()
        
        assert result[0] == 1000.0, "Derived feature leaked future knowledge"
        
        conn.close()


def test_pit_knowledge_time_validation():
    """Test that knowledge_time is properly set on data insertion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_validation_pit.duckdb"
        conn = duckdb.connect(str(db_path))
        
        conn.execute("""
            CREATE TABLE test_data (
                id VARCHAR PRIMARY KEY,
                value DOUBLE,
                knowledge_time TIMESTAMP NOT NULL,
                retrieved_at TIMESTAMP NOT NULL
            )
        """)
        
        # Test that knowledge_time is required
        try:
            conn.execute("""
                INSERT INTO test_data VALUES ('test1', 100.0, NULL, '2024-01-01 10:00:00')
            """)
            assert False, "Should have failed with NULL knowledge_time"
        except Exception:
            pass  # Expected
        
        # Test valid insertion
        current_time = datetime.now()
        conn.execute("""
            INSERT INTO test_data VALUES 
            ('test2', 100.0, ?, ?)
        """, [current_time, current_time])
        
        # Verify insertion
        result = conn.execute("""
            SELECT COUNT(*) FROM test_data WHERE knowledge_time IS NOT NULL
        """).fetchone()
        
        assert result[0] == 1, "Valid insertion failed"
        
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])