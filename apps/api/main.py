"""
Festival Bloomberg API - Consolidated FastAPI application.

This API provides comprehensive endpoints for festival intelligence:
- Artist search and entity resolution
- Festival information and analytics
- Artist factors and momentum analysis
- Expected billing predictions
- Relative value analysis
- Portfolio analytics and optimization
- Point-in-time data queries
- Source governance and eligibility

All data is served from the canonical DuckDB warehouse with point-in-time accuracy.
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add project paths
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("ENVIRONMENT", "development")

from python.festival_bloomberg.warehouse import FestivalRepository, get_repository
from python.festival_bloomberg.entities import EntityResolver, ResolutionResult
from python.festival_bloomberg.analytics import (
    ArtistFactorCalculator,
    ExpectedBillingModel,
    RelativeValueCalculator,
    FestivalPortfolioAnalyzer,
)

# Initialize FastAPI app
app = FastAPI(
    title="Festival Bloomberg API",
    description="Decision-support platform for festival talent buyers and promoters with point-in-time analytics",
    version="2.0.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Pydantic Models
# --------------------------------------------------------------------------- #
class ArtistSearchRequest(BaseModel):
    query: str
    limit: int = 10


class ArtistFactorsRequest(BaseModel):
    artist_key: str
    festival_key: Optional[str] = None
    feature_date: Optional[date] = None


class BillingPredictionRequest(BaseModel):
    artist_key: str
    festival_key: Optional[str] = None
    edition_key: Optional[str] = None
    feature_date: Optional[date] = None


class RelativeValueRequest(BaseModel):
    artist_key: str
    current_billing_tier: Optional[str] = None
    festival_key: Optional[str] = None
    edition_key: Optional[str] = None
    feature_date: Optional[date] = None


class PortfolioAnalysisRequest(BaseModel):
    festival_key: str
    edition_key: str
    edition_year: int
    total_budget: Optional[float] = None


class PointInTimeQuery(BaseModel):
    knowledge_time: Optional[datetime] = None
    feature_date: Optional[date] = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def get_repo() -> FestivalRepository:
    """Get warehouse repository instance."""
    return get_repository()


# --------------------------------------------------------------------------- #
# Root & Health
# --------------------------------------------------------------------------- #
@app.get("/")
async def root():
    return {
        "service": "Festival Bloomberg API",
        "version": "2.0.0",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health_check():
    repo = get_repo()
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "artists": repo.count_artists(),
        "festivals": repo.count_festivals(),
        "warehouse_path": repo.db_path,
    }


# --------------------------------------------------------------------------- #
# Festivals
# --------------------------------------------------------------------------- #
@app.get("/festivals")
async def list_festivals():
    """List all festivals in the warehouse."""
    repo = get_repo()
    return repo.list_festivals()


@app.get("/festivals/{festival_key}")
async def get_festival(festival_key: str):
    """Get detailed festival information."""
    repo = get_repo()
    festival = repo.get_festival(festival_key)
    if not festival:
        raise HTTPException(status_code=404, detail="Festival not found")
    return festival


# --------------------------------------------------------------------------- #
# Artists
# --------------------------------------------------------------------------- #
@app.post("/artists/search")
async def search_artists(request: ArtistSearchRequest):
    """Search for artists by name."""
    repo = get_repo()
    return repo.search_artists(request.query, limit=request.limit)


@app.get("/artists/{artist_key}")
async def get_artist(artist_key: str):
    """Get detailed artist information."""
    repo = get_repo()
    artist = repo.get_artist(artist_key)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    return artist


@app.get("/artists/{artist_key}/metrics")
async def get_artist_metrics(artist_key: str, pit: PointInTimeQuery = None):
    """Get artist metrics with point-in-time accuracy."""
    repo = get_repo()
    metrics = repo.get_artist_metrics(artist_key)
    
    # Filter by knowledge time if provided
    if pit and pit.knowledge_time:
        filtered = [
            m for m in metrics 
            if datetime.fromisoformat(m.get('fetched_at', '')) <= pit.knowledge_time
        ]
        metrics = filtered
    
    return {
        "artist_key": artist_key,
        "metrics": metrics,
        "knowledge_time": pit.knowledge_time.isoformat() if pit and pit.knowledge_time else None,
    }


# --------------------------------------------------------------------------- #
# Entity Resolution
# --------------------------------------------------------------------------- #
@app.post("/artists/resolve")
async def resolve_artist(name: str, use_warehouse: bool = True):
    """Resolve artist name to canonical entity."""
    repo = get_repo()
    
    # Create resolver
    from python.festival_bloomberg.entities import create_test_resolver
    resolver = create_test_resolver()
    
    # Try warehouse lookup if enabled
    if use_warehouse:
        result = resolver.resolve_by_name(name)
        if result.confidence < 0.5:
            # Try warehouse search
            search_results = repo.search_artists(name, limit=5)
            if search_results:
                return {
                    "name": name,
                    "resolved": True,
                    "method": "warehouse_search",
                    "candidates": search_results,
                    "confidence": 0.8,
                }
    
    # Use resolver
    result = resolver.resolve_by_name(name)
    
    return {
        "name": name,
        "resolved": result.confidence > 0.5,
        "artist_key": result.artist_key,
        "musicbrainz_id": result.musicbrainz_id,
        "confidence": result.confidence,
        "match_method": result.match_method,
        "requires_review": result.requires_review,
        "alternatives": result.alternatives,
    }


# --------------------------------------------------------------------------- #
# Artist Factors
# --------------------------------------------------------------------------- #
@app.post("/artists/factors")
async def calculate_artist_factors(request: ArtistFactorsRequest):
    """Calculate comprehensive artist factors."""
    repo = get_repo()
    
    # Get artist data
    artist = repo.get_artist(request.artist_key)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    
    # Get artist metrics for factor calculation
    metrics = repo.get_artist_metrics(request.artist_key)
    
    # Build artist data dict for factor calculation
    artist_data = {
        'genres': artist.get('genres', []),
        'subgenres': [],
        'countries': [artist.get('country')] if artist.get('country') else [],
        'regions': [artist.get('origin_region')] if artist.get('origin_region') else [],
        'spotify_popularity': artist.get('spotify_popularity'),
        'monthly_listeners': artist.get('monthly_listeners'),
    }
    
    # Add metric data
    for metric in metrics:
        metric_type = metric.get('metric_type')
        value = metric.get('value')
        if metric_type and value is not None:
            artist_data[metric_type] = value
    
    # Create festival context
    festival_context = {}
    if request.festival_key:
        festival = repo.get_festival(request.festival_key)
        if festival:
            festival_context = {
                'genres': festival.get('genre_focus', []),
                'country': festival.get('location_country'),
                'region': festival.get('location_region'),
                'festival_type': festival.get('festival_type'),
            }
    
    # Calculate factors
    calculator = ArtistFactorCalculator(festival_context)
    factors = calculator.calculate_factors(
        artist_key=request.artist_key,
        artist_data=artist_data,
        feature_date=request.feature_date,
    )
    
    return {
        "artist_key": request.artist_key,
        "factors": {
            "momentum_score": factors.momentum_score,
            "relevance_score": factors.relevance_score,
            "audience_fit_score": factors.audience_fit_score,
            "value_proposition_score": factors.value_proposition_score,
            "booking_complexity_score": factors.booking_complexity_score,
            "risk_score": factors.risk_score,
            "overall_score": factors.overall_score(),
        },
        "components": {
            "momentum": factors.momentum_components,
            "relevance": factors.relevance_components,
            "audience": factors.audience_components,
            "value": factors.value_components,
            "complexity": factors.complexity_components,
            "risk": factors.risk_components,
        },
        "metadata": {
            "calculated_at": factors.calculated_at.isoformat(),
            "feature_date": factors.feature_date.isoformat() if factors.feature_date else None,
            "model_version": factors.model_version,
            "confidence": factors.confidence,
        },
    }


# --------------------------------------------------------------------------- #
# Expected Billing
# --------------------------------------------------------------------------- #
@app.post("/artists/billing-prediction")
async def predict_billing(request: BillingPredictionRequest):
    """Generate expected billing prediction."""
    repo = get_repo()
    
    # Get artist factors first
    factors_request = ArtistFactorsRequest(
        artist_key=request.artist_key,
        festival_key=request.festival_key,
        feature_date=request.feature_date,
    )
    factors_response = await calculate_artist_factors(factors_request)
    artist_factors = factors_response["factors"]
    
    # Get historical billing if available
    historical_billing = None
    # In production, this would query warehouse for historical billing data
    
    # Create festival context
    festival_context = {}
    if request.festival_key:
        festival = repo.get_festival(request.festival_key)
        if festival:
            festival_context = {
                'genres': festival.get('genre_focus', []),
                'country': festival.get('location_country'),
            }
    
    # Generate prediction
    model = ExpectedBillingModel(festival_context)
    prediction = model.predict_billing(
        artist_key=request.artist_key,
        artist_factors=artist_factors,
        historical_billing=historical_billing,
    )
    
    return {
        "artist_key": request.artist_key,
        "prediction": prediction.to_dict(),
        "festival_key": request.festival_key,
        "edition_key": request.edition_key,
    }


# --------------------------------------------------------------------------- #
# Relative Value
# --------------------------------------------------------------------------- #
@app.post("/artists/relative-value")
async def calculate_relative_value(request: RelativeValueRequest):
    """Calculate relative value analysis."""
    repo = get_repo()
    
    # Get artist factors
    factors_request = ArtistFactorsRequest(
        artist_key=request.artist_key,
        festival_key=request.festival_key,
        feature_date=request.feature_date,
    )
    factors_response = await calculate_artist_factors(factors_request)
    artist_factors = factors_response["factors"]
    
    # Get current billing from request
    current_billing = {"tier": request.current_billing_tier} if request.current_billing_tier else None
    
    # Get expected billing
    billing_request = BillingPredictionRequest(
        artist_key=request.artist_key,
        festival_key=request.festival_key,
        edition_key=request.edition_key,
        feature_date=request.feature_date,
    )
    billing_response = await predict_billing(billing_request)
    expected_billing = {"tier": billing_response["prediction"]["expected_tier"]}
    
    # Calculate relative value
    calculator = RelativeValueCalculator()
    result = calculator.calculate_relative_value(
        artist_key=request.artist_key,
        current_billing=current_billing,
        expected_billing=expected_billing,
        artist_factors=artist_factors,
    )
    
    return {
        "artist_key": request.artist_key,
        "relative_value": result.to_dict(),
        "analysis": {
            "current_billing": current_billing,
            "expected_billing": expected_billing,
            "factors": artist_factors,
        },
    }


# --------------------------------------------------------------------------- #
# Portfolio Analytics
# --------------------------------------------------------------------------- #
@app.post("/festivals/portfolio/analyze")
async def analyze_portfolio(request: PortfolioAnalysisRequest):
    """Analyze festival portfolio composition and metrics."""
    repo = get_repo()
    
    # Get festival context
    festival = repo.get_festival(request.festival_key)
    if not festival:
        raise HTTPException(status_code=404, detail="Festival not found")
    
    festival_context = {
        'genres': festival.get('genre_focus', []),
        'country': festival.get('location_country'),
        'region': festival.get('location_region'),
        'festival_type': festival.get('festival_type'),
    }
    
    # Get lineup artists for this edition
    # In production, this would query warehouse for lineup data
    artists = []  # Placeholder - would be actual lineup data
    
    # Analyze portfolio
    analyzer = FestivalPortfolioAnalyzer(festival_context)
    metrics = analyzer.analyze_portfolio(
        festival_key=request.festival_key,
        edition_key=request.edition_key,
        edition_year=request.edition_year,
        artists=artists,
        total_budget=request.total_budget,
    )
    
    return {
        "festival_key": request.festival_key,
        "edition_key": request.edition_key,
        "portfolio_metrics": metrics.to_dict(),
    }


@app.post("/festivals/portfolio/optimize")
async def optimize_portfolio(request: PortfolioAnalysisRequest):
    """Generate portfolio optimization recommendations."""
    repo = get_repo()
    
    # Get current portfolio analysis
    portfolio_response = await analyze_portfolio(request)
    current_metrics = portfolio_response["portfolio_metrics"]
    
    # Convert to PortfolioMetrics object
    from python.festival_bloomberg.analytics.portfolio import PortfolioMetrics
    current_portfolio = PortfolioMetrics(**current_metrics)
    
    # Get lineup artists
    artists = []  # Placeholder - would be actual lineup data
    
    # Optimize portfolio
    analyzer = FestivalPortfolioAnalyzer()
    optimization = analyzer.optimize_portfolio(
        current_portfolio=current_portfolio,
        artists=artists,
        constraints={'budget_limited': bool(request.total_budget)},
    )
    
    return {
        "festival_key": request.festival_key,
        "edition_key": request.edition_key,
        "optimization": optimization.to_dict(),
    }


# --------------------------------------------------------------------------- #
# Point-in-Time Features
# --------------------------------------------------------------------------- #
@app.get("/artists/{artist_key}/features")
async def get_artist_features(
    artist_key: str,
    feature_date: Optional[date] = None,
    knowledge_time: Optional[datetime] = None,
    feature_names: Optional[List[str]] = Query(None),
):
    """Get point-in-time features for an artist."""
    repo = get_repo()
    
    features = repo.get_artist_features(
        artist_key=artist_key,
        feature_date=feature_date,
        knowledge_time=knowledge_time,
        feature_names=feature_names,
    )
    
    return {
        "artist_key": artist_key,
        "features": features,
        "query_params": {
            "feature_date": feature_date.isoformat() if feature_date else None,
            "knowledge_time": knowledge_time.isoformat() if knowledge_time else None,
            "feature_names": feature_names,
        },
    }


# --------------------------------------------------------------------------- #
# Market Overview
# --------------------------------------------------------------------------- #
@app.get("/market/overview")
async def get_market_overview():
    """Get market overview with top artists and festivals."""
    repo = get_repo()
    
    festivals = repo.list_festivals()
    
    # Get top artists by overall factor score
    # In production, this would query the factors table
    top_artists = []
    
    return {
        "total_artists": repo.count_artists(),
        "total_festivals": repo.count_festivals(),
        "festivals": festivals[:10],
        "top_artists": top_artists[:10],
        "updated_at": datetime.utcnow().isoformat(),
    }


# --------------------------------------------------------------------------- #
# Source Governance
# --------------------------------------------------------------------------- #
@app.get("/sources/eligibility")
async def get_source_eligibility():
    """Get source eligibility metadata."""
    from python.festival_bloomberg.governance.source_registry import SourceRegistry
    
    registry = SourceRegistry()
    sources = registry.get_all_sources()
    
    return {
        "sources": sources,
        "total_sources": len(sources),
        "updated_at": datetime.utcnow().isoformat(),
    }


@app.get("/sources/{source_name}/eligibility")
async def get_source_eligibility_detail(source_name: str):
    """Get detailed eligibility information for a specific source."""
    from python.festival_bloomberg.governance.source_registry import SourceRegistry
    
    registry = SourceRegistry()
    source = registry.get_source(source_name)
    
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    
    return {
        "source": source,
        "eligibility_checks": registry.check_eligibility(source_name, "production"),
    }


def get_app() -> FastAPI:
    """Get FastAPI application instance."""
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)