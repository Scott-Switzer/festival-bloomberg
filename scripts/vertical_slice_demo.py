"""
End-to-end vertical slice demonstration of Festival Bloomberg.

This script demonstrates the complete pipeline:
1. Data ingestion with point-in-time metadata
2. Entity resolution
3. Artist factor calculation
4. Expected billing prediction
5. Relative value analysis
6. Portfolio analytics
7. Point-in-time queries

This creates a complete working example of the MVP functionality.
"""

import sys
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
import json

# Add project paths
sys.path.insert(0, str(Path(__file__).parent.parent))

from intelligence.warehouse.repository import FestivalRepository, get_repository, DEFAULT_DB_PATH
from intelligence.pipelines.entity_resolution import create_test_resolver
from python.festival_bloomberg.analytics import (
    ArtistFactorCalculator,
    ExpectedBillingModel,
    RelativeValueCalculator,
    FestivalPortfolioAnalyzer,
)


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def demo_data_ingestion(repo):
    """Demonstrate data ingestion with point-in-time metadata."""
    print_section("1. DATA INGESTION WITH POINT-IN-TIME METADATA")
    
    # Sample artists with realistic data
    artists = [
        {
            "name": "The Weeknd",
            "normalized_name": "the weeknd",
            "musicbrainz_id": "f7d31c5f-c712-4603-8eb4-3b0b846c4f3c",
            "country": "Canada",
            "genres": ["R&B", "Pop", "Electronic"],
            "type": "Person",
            "spotify_popularity": 95,
            "monthly_listeners": 50000000,
        },
        {
            "name": "Taylor Swift",
            "normalized_name": "taylor swift",
            "musicbrainz_id": "1d79c3f2-6e01-4f73-994b-8a69b8c2b9e0",
            "country": "USA",
            "genres": ["Pop", "Country"],
            "type": "Person",
            "spotify_popularity": 98,
            "monthly_listeners": 70000000,
        },
        {
            "name": "Drake",
            "normalized_name": "drake",
            "musicbrainz_id": "4d5447d7-2a38-4b59-8a30-5c721f39add8",
            "country": "Canada",
            "genres": ["Hip Hop", "R&B"],
            "type": "Person",
            "spotify_popularity": 92,
            "monthly_listeners": 45000000,
        },
        {
            "name": "Billie Eilish",
            "normalized_name": "billie eilish",
            "musicbrainz_id": "6552f5a5-cbf9-4489-ba37-15de3a9d6656",
            "country": "USA",
            "genres": ["Pop", "Electronic"],
            "type": "Person",
            "spotify_popularity": 88,
            "monthly_listeners": 35000000,
        },
    ]
    
    # Ingest artists
    for artist in artists:
        artist_key = repo.upsert_artist(artist, source_system="musicbrainz")
        print(f"✓ Ingested artist: {artist['name']} (key: {artist_key})")
        
        # Insert basic metrics (using intelligence repository interface)
        from datetime import timezone
        now = datetime.now(timezone.utc)
        repo.insert_artist_metric(
            artist_key=artist_key,
            source_system="spotify",
            metric_type="popularity",
            value=artist['spotify_popularity'],
            observed_date=date.today(),
            meta_data={"source_url": "https://api.spotify.com/v1/artists", "confidence": 0.95},
        )
        
        repo.insert_artist_metric(
            artist_key=artist_key,
            source_system="spotify",
            metric_type="monthly_listeners",
            value=artist['monthly_listeners'],
            observed_date=date.today(),
            meta_data={"source_url": "https://api.spotify.com/v1/artists", "confidence": 0.95},
        )
        
        print(f"  ✓ Added point-in-time metrics for {artist['name']}")
    
    # Sample festival
    festival = {
        "name": "Coachella",
        "normalized_name": "coachella",
        "location_country": "USA",
        "location_city": "Indio",
        "location_region": "California",
        "capacity": 125000,
        "genre_focus": ["Electronic", "Pop", "Hip Hop", "Indie"],
        "festival_type": "Music Festival",
        "duration_days": 6,
        "typical_month": 4,
    }
    
    festival_key = repo.upsert_festival(festival, source_system="c3")
    print(f"✓ Ingested festival: {festival['name']} (key: {festival_key})")
    
    return artists, festival_key


def demo_entity_resolution(resolver, artists: list):
    """Demonstrate entity resolution."""
    print_section("2. ENTITY RESOLUTION")
    
    # Test name resolution
    test_names = ["The Weeknd", "Taylor Swift", "Unknown Artist", "weekend"]
    
    for name in test_names:
        result = resolver.resolve_by_name(name)
        print(f"✓ Resolved '{name}':")
        # Intelligence resolver returns list of tuples
        if result and len(result) > 0:
            mbid, confidence = result[0]
            print(f"  - MusicBrainz ID: {mbid}")
            print(f"  - Confidence: {confidence}")
        else:
            print(f"  - No match found")
        print()



def demo_artist_factors(repo, artists: list, festival_key: str):
    """Demonstrate artist factor calculation."""
    print_section("3. ARTIST FACTOR CALCULATION")
    
    # Create festival context
    festival_context = {
        'genres': ["Electronic", "Pop", "Hip Hop", "Indie"],
        'country': "USA",
        'region': "California",
        'festival_type': "Music Festival",
    }
    
    calculator = ArtistFactorCalculator(festival_context)
    
    for artist in artists:
        # Build artist data
        artist_data = {
            'genres': artist['genres'],
            'subgenres': [],
            'countries': [artist['country']],
            'regions': [],
            'spotify_popularity': artist['spotify_popularity'],
            'monthly_listeners': artist['monthly_listeners'],
            'social_mentions': artist['monthly_listeners'] // 1000,  # Proxy
            'news_mentions': 10,  # Proxy
        }
        
        # Calculate factors
        factors = calculator.calculate_factors(
            artist_key=artist['musicbrainz_id'],
            artist_data=artist_data,
            feature_date=date.today(),
        )
        
        print(f"✓ Factors for {artist['name']}:")
        print(f"  - Momentum Score: {factors.momentum_score:.1f}/100")
        print(f"  - Relevance Score: {factors.relevance_score:.1f}/100")
        print(f"  - Audience Fit Score: {factors.audience_fit_score:.1f}/100")
        print(f"  - Value Proposition Score: {factors.value_proposition_score:.1f}/100")
        print(f"  - Booking Complexity Score: {factors.booking_complexity_score:.1f}/100")
        print(f"  - Risk Score: {factors.risk_score:.1f}/100")
        print(f"  - Overall Score: {factors.overall_score():.1f}/100")
        
        # Note: Skipping warehouse storage for this demo since schema may not support new tables
        print(f"  ✓ Factors calculated (warehouse storage skipped for demo)")
        print()


def demo_billing_prediction(repo, artists: list, festival_key: str):
    """Demonstrate expected billing prediction."""
    print_section("4. EXPECTED BILLING PREDICTION")
    
    festival_context = {
        'genres': ["Electronic", "Pop", "Hip Hop", "Indie"],
        'country': "USA",
    }
    
    model = ExpectedBillingModel(festival_context)
    
    for artist in artists:
        # Calculate factors first (inline for demo)
        artist_data = {
            'genres': artist['genres'],
            'subgenres': [],
            'countries': [artist['country']],
            'regions': [],
            'spotify_popularity': artist['spotify_popularity'],
            'monthly_listeners': artist['monthly_listeners'],
            'social_mentions': artist['monthly_listeners'] // 1000,
            'news_mentions': 10,
        }
        
        calculator = ArtistFactorCalculator(festival_context)
        factors = calculator.calculate_factors(
            artist_key=artist['musicbrainz_id'],
            artist_data=artist_data,
            feature_date=date.today(),
        )
        
        # Generate billing prediction
        prediction = model.predict_billing(
            artist_key=artist['musicbrainz_id'],
            artist_factors=factors.__dict__,
            festival_constraints={'budget_limited': False},
        )
        
        print(f"✓ Billing Prediction for {artist['name']}:")
        print(f"  - Expected Tier: {prediction.expected_tier.value}")
        print(f"  - Expected Order: {prediction.expected_order}")
        print(f"  - Confidence: {prediction.confidence:.2f}")
        print(f"  - Booking Probability: {prediction.booking_probability:.2f}")
        print(f"  - Reasoning: {prediction.reasoning}")
        print(f"  ✓ Prediction calculated (warehouse storage skipped for demo)")
        print()


def demo_relative_value(repo, artists: list, festival_key: str):
    """Demonstrate relative value analysis."""
    print_section("5. RELATIVE VALUE ANALYSIS")
    
    calculator = RelativeValueCalculator()
    
    for artist in artists:
        # Calculate factors first (inline for demo)
        artist_data = {
            'genres': artist['genres'],
            'subgenres': [],
            'countries': [artist['country']],
            'regions': [],
            'spotify_popularity': artist['spotify_popularity'],
            'monthly_listeners': artist['monthly_listeners'],
            'social_mentions': artist['monthly_listeners'] // 1000,
            'news_mentions': 10,
        }
        
        festival_context = {
            'genres': ["Electronic", "Pop", "Hip Hop", "Indie"],
            'country': "USA",
        }
        
        factor_calc = ArtistFactorCalculator(festival_context)
        factors = factor_calc.calculate_factors(
            artist_key=artist['musicbrainz_id'],
            artist_data=artist_data,
            feature_date=date.today(),
        )
        
        # Calculate relative value
        current_billing = {"tier": "SUPPORTING"}  # Assume current billing
        expected_billing = {"tier": "HEADLINER"} if factors.overall_score() > 70 else {"tier": "SUPPORTING"}
        
        result = calculator.calculate_relative_value(
            artist_key=artist['musicbrainz_id'],
            current_billing=current_billing,
            expected_billing=expected_billing,
            artist_factors=factors.__dict__,
        )
        
        print(f"✓ Relative Value for {artist['name']}:")
        print(f"  - Relative Value Score: {result.relative_value_score:.1f}/100")
        print(f"  - Value Category: {result.value_category.value}")
        print(f"  - Value Percentile: {result.value_percentile:.1f}")
        print(f"  - Current Billing: {result.current_billing_tier}")
        print(f"  - Expected Billing: {result.expected_billing_tier}")
        print(f"  - Billing Gap: {result.billing_gap:.2f}")
        print(f"  - Market Position: {result.market_position}")
        print(f"  ✓ Relative value calculated (warehouse storage skipped for demo)")
        print()


def demo_portfolio_analytics(repo, artists: list, festival_key: str):
    """Demonstrate portfolio analytics."""
    print_section("6. PORTFOLIO ANALYTICS")
    
    # Build artist lineup data with inline calculations
    festival_context = {
        'genres': ["Electronic", "Pop", "Hip Hop", "Indie"],
        'country': "USA",
        'region': "California",
        'festival_type': "Music Festival",
    }
    
    lineup_artists = []
    for artist in artists:
        # Calculate factors inline
        artist_data = {
            'genres': artist['genres'],
            'subgenres': [],
            'countries': [artist['country']],
            'regions': [],
            'spotify_popularity': artist['spotify_popularity'],
            'monthly_listeners': artist['monthly_listeners'],
            'social_mentions': artist['monthly_listeners'] // 1000,
            'news_mentions': 10,
        }
        
        factor_calc = ArtistFactorCalculator(festival_context)
        factors = factor_calc.calculate_factors(
            artist_key=artist['musicbrainz_id'],
            artist_data=artist_data,
            feature_date=date.today(),
        )
        
        # Determine billing tier based on overall score
        if factors.overall_score() > 70:
            billing_tier = "HEADLINER"
            estimated_cost = 1000000
        elif factors.overall_score() > 50:
            billing_tier = "SUB_HEADLINER"
            estimated_cost = 500000
        else:
            billing_tier = "SUPPORTING"
            estimated_cost = 100000
        
        lineup_artists.append({
            'artist_key': artist['musicbrainz_id'],
            'name': artist['name'],
            'billing_tier': billing_tier,
            'factors': factors.__dict__,
            'genres': artist['genres'],
            'country': artist['country'],
            'estimated_cost': estimated_cost,
        })
    
    # Analyze portfolio
    analyzer = FestivalPortfolioAnalyzer(festival_context)
    metrics = analyzer.analyze_portfolio(
        festival_key=festival_key,
        edition_key=f"{festival_key}_2026",
        edition_year=2026,
        artists=lineup_artists,
        total_budget=10000000,  # $10M budget
    )
    
    print(f"✓ Portfolio Metrics for Coachella 2026:")
    print(f"  - Total Artists: {metrics.total_artists}")
    print(f"  - Headliners: {metrics.headliner_count}")
    print(f"  - Sub-Headliners: {metrics.sub_headliner_count}")
    print(f"  - Supporting Acts: {metrics.supporting_count}")
    print(f"  - Early Day Acts: {metrics.early_day_count}")
    print(f"  - Portfolio Momentum Avg: {metrics.portfolio_momentum_avg:.1f}/100")
    print(f"  - Portfolio Momentum Median: {metrics.portfolio_momentum_median:.1f}/100")
    print(f"  - Portfolio Risk Avg: {metrics.portfolio_risk_avg:.1f}/100")
    print(f"  - Portfolio Value Avg: {metrics.portfolio_value_avg:.1f}/100")
    print(f"  - Portfolio Diversity Score: {metrics.portfolio_diversity_score:.1f}/100")
    print(f"  - Total Budget: ${metrics.total_budget:,.0f}")
    print(f"  - Budget Utilization: {metrics.budget_utilization:.1f}%")
    print(f"  - ROI Score: {metrics.roi_score:.1f}/100" if metrics.roi_score else "  - ROI Score: N/A")
    print(f"  - Efficiency Score: {metrics.efficiency_score:.1f}/100" if metrics.efficiency_score else "  - Efficiency Score: N/A")
    print(f"  ✓ Portfolio analytics calculated (warehouse storage skipped for demo)")
    print()


def demo_point_in_time_queries(repo, artists: list):
    """Demonstrate point-in-time queries."""
    print_section("7. POINT-IN-TIME QUERIES")
    
    artist = artists[0]  # Use first artist
    artist_key = artist['musicbrainz_id']
    
    # Demonstrate point-in-time concept (using basic warehouse queries)
    print("✓ Point-in-Time Query Demonstration:")
    print(f"  - Artist: {artist['name']}")
    print(f"  - Artist Key: {artist_key}")
    print(f"  - Current Knowledge Time: {datetime.now(timezone.utc).isoformat()}")
    
    # Get basic metrics to show PIT concept
    metrics = repo.get_artist_metrics(artist_key)
    print(f"  - Available Metrics: {len(metrics)}")
    for metric in metrics[:3]:
        print(f"    - {metric['source_system']}/{metric['metric_type']}: {metric['value']}")
        print(f"      Observed: {metric['observed_date']}")
        print(f"      Fetched: {metric['fetched_at']}")
    
    print(f"  ✓ Point-in-time metadata demonstrated (full PIT queries require consolidated schema)")
    print()


def main():
    """Run the complete vertical slice demonstration."""
    print("\n" + "="*60)
    print("  FESTIVAL BLOOMBERG - END-TO-END VERTICAL SLICE DEMO")
    print("="*60)
    
    # Initialize warehouse
    print("\nInitializing warehouse...")
    # Use intelligence warehouse which has known working schema
    from intelligence.warehouse.repository import FestivalRepository as IntelFestivalRepository
    intel_db_path = "/Users/scottthomasswitzer/CascadeProjects/festival-bloomberg/intelligence/data/warehouse/festival_bloomberg.duckdb"
    repo = IntelFestivalRepository(db_path=intel_db_path, read_only=False)
    print(f"✓ Warehouse initialized at: {repo.db_path}")
    print(f"✓ Artists in warehouse: {repo.count_artists()}")
    print(f"✓ Festivals in warehouse: {repo.count_festivals()}")
    
    # Initialize entity resolver
    resolver = create_test_resolver()
    print(f"✓ Entity resolver initialized with {len(resolver.get_all_mappings())} mappings")
    
    try:
        # Run demonstration pipeline
        artists, festival_key = demo_data_ingestion(repo)
        demo_entity_resolution(resolver, artists)
        demo_artist_factors(repo, artists, festival_key)
        demo_billing_prediction(repo, artists, festival_key)
        demo_relative_value(repo, artists, festival_key)
        demo_portfolio_analytics(repo, artists, festival_key)
        demo_point_in_time_queries(repo, artists)
        
        # Final summary
        print_section("VERTICAL SLICE COMPLETE")
        print("✓ All MVP components demonstrated successfully:")
        print("  1. Data ingestion with point-in-time metadata")
        print("  2. Entity resolution")
        print("  3. Artist factor calculation")
        print("  4. Expected billing prediction")
        print("  5. Relative value analysis")
        print("  6. Portfolio analytics")
        print("  7. Point-in-time queries")
        print()
        print("✓ Data persisted in DuckDB warehouse (intelligence schema)")
        print("✓ Analytics modules functional and producing results")
        print("✓ New consolidated modules created (python/festival_bloomberg/)")
        print()
        print("Next steps:")
        print("  - Consolidate schema migration to enable full warehouse storage")
        print("  - Start API server: python -m uvicorn apps.api.main:app --reload")
        print("  - Access docs: http://localhost:8000/docs")
        print("  - Test endpoints with real data")
        
    except Exception as e:
        print(f"\n✗ Error during demonstration: {e}")
        import traceback
        traceback.print_exc()
    finally:
        repo.close()


if __name__ == "__main__":
    main()