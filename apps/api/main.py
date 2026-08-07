"""
FastAPI backend for Festival Intelligence Terminal.
Provides REST API endpoints for artist intelligence, festival comparison,
tour prediction, and revenue simulation.
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent  # Go up from apps/api to project root
sys.path.insert(0, str(project_root))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from pydantic import BaseModel
import os
import polars as pl

from database import Artist, Festival
from database.manager import get_db_manager
from pipelines.monid import MonidClient, FestivalIntelligenceAgent
from pipelines.predictive_analytics import PredictiveAnalyticsEngine
from utils.logging import get_logger, APILogger
from utils.errors import (
    FestivalIntelligenceError, ArtistNotFoundError, FestivalNotFoundError,
    handle_exception, create_http_exception
)

# Initialize FastAPI app
app = FastAPI(
    title="Festival Intelligence Terminal API",
    description="Decision-support platform for festival talent buyers and promoters",
    version="1.0.0",
)

# Initialize logger
logger = get_logger(__name__)
api_logger = APILogger()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models for API requests/responses
class ArtistSearchRequest(BaseModel):
    query: str
    limit: int = 10


class MonidDiscoverRequest(BaseModel):
    query: str


class MonidToolResponse(BaseModel):
    id: str
    name: str
    description: str
    pricing: Dict[str, Any]
    category: Optional[str] = None


class MonidRunRequest(BaseModel):
    tool_id: str
    parameters: Dict[str, Any]


class ArtistMomentumResponse(BaseModel):
    artist_id: str
    artist_name: str
    momentum_score: float
    momentum_percentile: float
    momentum_change_30d: Optional[float]
    momentum_change_90d: Optional[float]
    observation_date: date


class BookingValueResponse(BaseModel):
    artist_id: str
    artist_name: str
    booking_value_index: float
    predicted_billing_tier: Optional[str]
    observed_recent_billing_tier: Optional[str]
    momentum_to_billing_residual: Optional[float]
    observation_date: date


class FestivalComparisonResponse(BaseModel):
    festival_id: str
    festival_name: str
    lineup_strength_index: float
    headliner_dependency: float
    genre_entropy: float
    emerging_artist_share: float
    lineup_uniqueness: float
    competitive_overlap: Optional[float]
    comparison_date: date


class RevenueScenarioRequest(BaseModel):
    festival_id: str
    capacity: int
    expected_attendance: int
    ticket_tiers: Dict[str, Dict[str, Any]]
    vip_mix: float = 0.0
    sponsorship_commitments: float = 0.0
    per_capita_fnb_spending: float = 0.0
    per_capita_merch_spending: float = 0.0
    artist_cost_range: List[float]
    production_costs: float = 0.0
    weather_assumption: Optional[str] = None


class RevenueScenarioResponse(BaseModel):
    scenario_id: str
    ticket_revenue: float
    ancillary_revenue: float
    total_revenue: float
    artist_costs: float
    contribution_margin: float
    p10_downside: Optional[float]
    p50_base_case: Optional[float]
    p90_upside: Optional[float]
    profitability_probability: Optional[float]
    break_even_attendance: Optional[int]
    break_even_ticket_price: Optional[float]
    revenue_at_risk_weather: Optional[float]


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.utcnow()}


# Festival endpoints
@app.get("/festivals", response_model=List[Dict[str, Any]])
async def get_festivals():
    """Get all festivals."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            festivals = session.query(Festival).all()
            return [
                {
                    "id": str(festival.id),
                    "name": festival.name,
                    "location_country": festival.location_country,
                    "location_city": festival.location_city,
                    "capacity": festival.capacity,
                    "genre_focus": festival.genre_focus,
                    "festival_type": festival.festival_type
                }
                for festival in festivals
            ]
    except Exception as e:
        logger.error(f"Error fetching festivals: {e}", exc_info=True)
        raise create_http_exception(
            FestivalIntelligenceError(f"Failed to fetch festivals: {str(e)}"),
            status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@app.get("/festivals/{festival_id}", response_model=Dict[str, Any])
async def get_festival(festival_id: str):
    """Get festival by ID."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            festival = session.query(Festival).filter(Festival.id == festival_id).first()
            if not festival:
                raise FestivalNotFoundError(festival_id)
            
            return {
                "id": str(festival.id),
                "name": festival.name,
                "location_country": festival.location_country,
                "location_city": festival.location_city,
                "location_region": festival.location_region,
                "capacity": festival.capacity,
                "genre_focus": festival.genre_focus,
                "festival_type": festival.festival_type,
                "venue_type": festival.venue_type,
                "duration_days": festival.duration_days,
                "ticket_price_min": festival.ticket_price_min,
                "ticket_price_max": festival.ticket_price_max,
                "prestige_score": festival.prestige_score
            }
    except FestivalNotFoundError as e:
        raise create_http_exception(e, status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error fetching festival: {e}", exc_info=True)
        raise create_http_exception(
            FestivalIntelligenceError(f"Failed to fetch festival: {str(e)}"),
            status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# Artist endpoints
@app.post("/artists/search", response_model=List[Dict[str, Any]])
async def search_artists(request: ArtistSearchRequest):
    """Search for artists by name."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            # Case-insensitive partial match search
            search_term = f"%{request.query.lower()}%"
            artists = session.query(Artist).filter(
                Artist.normalized_name.ilike(search_term)
            ).limit(request.limit).all()
            
            return [
                {
                    "id": str(artist.id),
                    "musicbrainz_id": artist.musicbrainz_id,
                    "name": artist.name,
                    "normalized_name": artist.normalized_name,
                    "genres": artist.genres,
                    "momentum_score": artist.momentum_score,
                    "confidence": 1.0,
                }
                for artist in artists
            ]
    except Exception as e:
        logger.error(f"Error searching artists: {e}", exc_info=True)
        raise create_http_exception(
            FestivalIntelligenceError(f"Failed to search artists: {str(e)}"),
            status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@app.get("/artists/{artist_id}", response_model=Dict[str, Any])
async def get_artist(artist_id: str):
    """Get artist by ID."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            artist = session.query(Artist).filter(Artist.id == artist_id).first()
            if not artist:
                raise ArtistNotFoundError(artist_id)
            
            return {
                "id": str(artist.id),
                "musicbrainz_id": artist.musicbrainz_id,
                "name": artist.name,
                "normalized_name": artist.normalized_name,
                "genres": artist.genres,
                "origin_country": artist.origin_country,
                "origin_city": artist.origin_city,
                "career_stage": artist.career_stage,
                "monthly_listeners": artist.monthly_listeners,
                "spotify_followers": artist.spotify_followers,
                "momentum_score": artist.momentum_score,
                "booking_value_index": artist.booking_value_index,
                "breakthrough_probability": artist.breakthrough_probability
            }
    except ArtistNotFoundError as e:
        raise create_http_exception(e, status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error fetching artist: {e}", exc_info=True)
        raise create_http_exception(
            FestivalIntelligenceError(f"Failed to fetch artist: {str(e)}"),
            status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@app.get("/artists/{artist_id}/momentum", response_model=ArtistMomentumResponse)
async def get_artist_momentum(artist_id: str):
    """Get artist momentum metrics."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            artist = session.query(Artist).filter(Artist.id == artist_id).first()
            if not artist:
                raise HTTPException(status_code=404, detail="Artist not found")
            
            return ArtistMomentumResponse(
                artist_id=artist_id,
                artist_name=artist.name,
                momentum_score=artist.momentum_score or 0,
                momentum_percentile=75.0,  # Would calculate from distribution
                momentum_change_30d=0,  # Would calculate from historical data
                momentum_change_90d=0,
                observation_date=date.today(),
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching artist momentum: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/artists/{artist_id}/booking-value", response_model=BookingValueResponse)
async def get_artist_booking_value(artist_id: str):
    """Get artist booking value index."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            artist = session.query(Artist).filter(Artist.id == artist_id).first()
            if not artist:
                raise HTTPException(status_code=404, detail="Artist not found")
            
            return BookingValueResponse(
                artist_id=artist_id,
                artist_name=artist.name,
                booking_value_index=artist.booking_value_index or 0,
                predicted_billing_tier="headliner" if (artist.booking_value_index or 0) > 80 else "supporting",
                observed_recent_billing_tier=None,  # Would calculate from historical data
                momentum_to_billing_residual=0,
                observation_date=date.today(),
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching artist booking value: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/artists/{artist_id}/tour-prediction", response_model=Dict[str, Any])
async def get_artist_tour_prediction(artist_id: str):
    """Get artist tour prediction."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            artist = session.query(Artist).filter(Artist.id == artist_id).first()
            if not artist:
                raise HTTPException(status_code=404, detail="Artist not found")
            
            # Use predictive analytics engine if available
            try:
                analytics_engine = PredictiveAnalyticsEngine()
                prediction = analytics_engine.predict_tour_probability(artist_id)
                return {
                    "artist_id": artist_id,
                    "artist_name": artist.name,
                    "tour_probability_90d": prediction.prediction.get('tour_probability_90d', 0.5),
                    "tour_probability_180d": prediction.prediction.get('tour_probability_180d', 0.6),
                    "tour_probability_365d": prediction.prediction.get('tour_probability_365d', 0.7),
                    "festival_appearance_probability": prediction.prediction.get('festival_appearance_probability', 0.5),
                    "geographically_routable": prediction.prediction.get('geographically_routable', True),
                    "routing_confidence": prediction.prediction.get('routing_confidence', 0.7),
                    "prediction_date": date.today(),
                }
            except Exception as e:
                print(f"Predictive analytics error: {e}, using fallback")
                # Fallback to simple calculation based on momentum
                momentum = artist.momentum_score or 50
                return {
                    "artist_id": artist_id,
                    "artist_name": artist.name,
                    "tour_probability_90d": min(0.9, momentum / 100),
                    "tour_probability_180d": min(0.95, (momentum + 10) / 100),
                    "tour_probability_365d": min(1.0, (momentum + 20) / 100),
                    "festival_appearance_probability": min(0.8, momentum / 100),
                    "geographically_routable": momentum > 60,
                    "routing_confidence": momentum / 100,
                    "prediction_date": date.today(),
                }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching tour prediction: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Festival comparison endpoints
@app.get("/festivals/{festival_id}/comparison", response_model=FestivalComparisonResponse)
async def get_festival_comparison(festival_id: str):
    """Get festival comparison metrics."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            festival = session.query(Festival).filter(Festival.id == festival_id).first()
            if not festival:
                raise HTTPException(status_code=404, detail="Festival not found")
            
            return FestivalComparisonResponse(
                festival_id=festival_id,
                festival_name=festival.name,
                lineup_strength_index=festival.prestige_score or 75.0,
                headliner_dependency=0.15,  # Would calculate from lineup data
                genre_entropy=0.78,  # Would calculate from genre distribution
                emerging_artist_share=0.22,  # Would calculate from lineup
                lineup_uniqueness=0.65,  # Would calculate from comparison
                competitive_overlap=0.35,  # Would calculate from other festivals
                comparison_date=date.today(),
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching festival comparison: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Prediction endpoints
@app.post("/predictions/artist-breakthrough/{artist_id}")
async def predict_artist_breakthrough(artist_id: str):
    """Predict artist breakthrough."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            artist = session.query(Artist).filter(Artist.id == artist_id).first()
            if not artist:
                raise HTTPException(status_code=404, detail="Artist not found")
            
            analytics_engine = PredictiveAnalyticsEngine()
            prediction = analytics_engine.predict_artist_breakthrough(artist_id)
            
            return {
                "artist_id": artist_id,
                "artist_name": artist.name,
                "breakthrough_probability": prediction.prediction.get('breakthrough_probability', 0.5),
                "timeline": prediction.prediction.get('timeline', '6-12 months'),
                "confidence": prediction.confidence.value,
                "key_drivers": prediction.key_drivers,
                "recommended_actions": prediction.recommended_actions
            }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error predicting breakthrough: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/predictions/lineup-success/{festival_id}")
async def predict_lineup_success(festival_id: str, lineup: List[Dict[str, Any]]):
    """Predict lineup success for festival."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            festival = session.query(Festival).filter(Festival.id == festival_id).first()
            if not festival:
                raise HTTPException(status_code=404, detail="Festival not found")
            
            analytics_engine = PredictiveAnalyticsEngine()
            prediction = analytics_engine.predict_festival_lineup_success(festival_id, lineup)
            
            return {
                "festival_id": festival_id,
                "festival_name": festival.name,
                "predicted_attendance": prediction.prediction.get('attendance', {}).get('predicted', 0),
                "predicted_revenue": prediction.prediction.get('revenue', {}).get('predicted', 0),
                "success_probability": prediction.confidence.value,
                "risk_factors": prediction.risk_assessment,
                "optimization_suggestions": prediction.recommended_actions
            }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error predicting lineup success: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/festivals/compare", response_model=List[FestivalComparisonResponse])
async def compare_festivals(festival_ids: List[str] = Query(...)):
    """Compare multiple festivals."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            results = []
            for festival_id in festival_ids:
                festival = session.query(Festival).filter(Festival.id == festival_id).first()
                if festival:
                    results.append(
                        FestivalComparisonResponse(
                            festival_id=festival_id,
                            festival_name=festival.name,
                            lineup_strength_index=75.0 + hash(festival_id) % 20,
                            headliner_dependency=0.1 + hash(festival_id) % 10 / 100,
                            genre_entropy=0.7 + hash(festival_id) % 20 / 100,
                            emerging_artist_share=0.2 + hash(festival_id) % 10 / 100,
                            lineup_uniqueness=0.6 + hash(festival_id) % 30 / 100,
                            competitive_overlap=0.3 + hash(festival_id) % 20 / 100,
                            comparison_date=date.today(),
                        )
                    )
            return results
    except Exception as e:
        print(f"Error comparing festivals: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Revenue simulation endpoints
@app.post("/revenue/simulate", response_model=RevenueScenarioResponse)
async def simulate_revenue(request: RevenueScenarioRequest):
    """Run revenue simulation."""
    # Placeholder - would use actual model in production
    from models.revenue_simulation import RevenueSimulationModel, RevenueScenario as ModelScenario
    
    model = RevenueSimulationModel(num_simulations=10000)
    scenario = ModelScenario(
        capacity=request.capacity,
        expected_attendance=request.expected_attendance,
        ticket_tiers=request.ticket_tiers,
        vip_mix=request.vip_mix,
        sponsorship_commitments=request.sponsorship_commitments,
        per_capita_fnb_spending=request.per_capita_fnb_spending,
        per_capita_merch_spending=request.per_capita_merch_spending,
        artist_cost_range=tuple(request.artist_cost_range),
        production_costs=request.production_costs,
        weather_assumption=request.weather_assumption,
    )
    
    result = model.run_monte_carlo_simulation(scenario, lineup_size=50)
    
    return RevenueScenarioResponse(
        scenario_id=f"scenario_{datetime.utcnow().timestamp()}",
        ticket_revenue=result.ticket_revenue,
        ancillary_revenue=result.ancillary_revenue,
        total_revenue=result.total_revenue,
        artist_costs=result.artist_costs,
        contribution_margin=result.contribution_margin,
        p10_downside=result.p10_downside,
        p50_base_case=result.p50_base_case,
        p90_upside=result.p90_upside,
        profitability_probability=result.profitability_probability,
        break_even_attendance=result.break_even_attendance,
        break_even_ticket_price=result.break_even_ticket_price,
        revenue_at_risk_weather=result.revenue_at_risk_weather,
    )


# Location intelligence endpoints
@app.get("/festivals/{festival_id}/location-intelligence", response_model=Dict[str, Any])
async def get_location_intelligence(festival_id: str):
    """Get location intelligence for a festival."""
    # Placeholder - would query database in production
    return {
        "festival_id": festival_id,
        "weather_risk_score": 35.5,
        "heat_stress_score": 42.0,
        "rain_disruption_probability": 0.25,
        "air_access_score": 78.5,
        "direct_flight_coverage": 0.85,
        "hotel_pressure_proxy": 0.65,
        "market_population": 2700000,
        "median_income": 65000,
        "observation_date": date.today(),
    }


# Market overview endpoint
@app.get("/market/overview")
async def get_market_overview():
    """Get market overview with top momentum artists and upcoming festivals."""
    try:
        db_manager = get_db_manager()
        with db_manager.get_session() as session:
            # Get top momentum artists
            top_artists = session.query(Artist).order_by(
                Artist.momentum_score.desc()
            ).limit(10).all()
            
            momentum_artists = [
                {
                    "artist_id": str(artist.id),
                    "name": artist.name,
                    "momentum_score": artist.momentum_score or 0,
                    "change_30d": 0,  # Would calculate from historical data
                }
                for artist in top_artists
            ]
            
            # Get upcoming festivals
            festivals = session.query(Festival).limit(10).all()
            upcoming_festivals = [
                {
                    "festival_id": str(festival.id),
                    "name": festival.name,
                    "date": f"{festival.typical_month or 'TBD'}-{festival.typical_year_start or 2025}",
                }
                for festival in festivals
            ]
    except Exception as e:
        print(f"Error loading market data: {e}")
        # Fallback to placeholder data if database not available
        momentum_artists = [
            {"artist_id": "1", "name": "Artist A", "momentum_score": 95.2, "change_30d": 12.5},
            {"artist_id": "2", "name": "Artist B", "momentum_score": 92.8, "change_30d": 8.3},
        ]
        upcoming_festivals = [
            {"festival_id": "coachella", "name": "Coachella", "date": "2025-04-11"},
        ]
    
    return {
        "top_momentum_artists": momentum_artists[:5],
        "upcoming_festivals": upcoming_festivals,
        "demand_shifts": [
            {"genre": "Pop", "change": 5.2},
            {"genre": "Rock", "change": -2.1},
            {"genre": "Hip-Hop", "change": 8.5},
        ],
        "weather_risks": [
            {"festival_id": "bonnaroo", "risk_score": 45.0, "primary_risk": "Heat"},
            {"festival_id": "outside_lands", "risk_score": 25.0, "primary_risk": "Wind"},
        ],
        "updated_at": datetime.utcnow(),
    }


# Monid.ai agentic endpoints
@app.post("/monid/discover", response_model=List[MonidToolResponse])
async def monid_discover(request: MonidDiscoverRequest):
    """Discover tools using Monid.ai agentic capabilities."""
    try:
        client = MonidClient()
        tools = client.discover(request.query)
        
        return [
            MonidToolResponse(
                id=tool.id,
                name=tool.name,
                description=tool.description,
                pricing=tool.pricing,
                category=tool.category
            )
            for tool in tools
        ]
    except Exception as e:
        print(f"Error discovering tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/monid/run")
async def monid_run(request: MonidRunRequest):
    """Execute a tool using Monid.ai."""
    try:
        client = MonidClient()
        result = client.run(request.tool_id, request.parameters)
        return result
    except Exception as e:
        print(f"Error running tool: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/monid/balance")
async def monid_balance():
    """Get Monid.ai wallet balance."""
    try:
        client = MonidClient()
        balance = client.get_balance()
        return balance
    except Exception as e:
        print(f"Error getting balance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/monid/artist-data")
async def monid_collect_artist_data(artist_name: str = Query(...)):
    """Agentic data collection for a specific artist."""
    try:
        agent = FestivalIntelligenceAgent()
        result = agent.collect_artist_data(artist_name)
        return result
    except Exception as e:
        print(f"Error collecting artist data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/monid/festival-data")
async def monid_collect_festival_data(festival_name: str = Query(...)):
    """Agentic data collection for a specific festival."""
    try:
        agent = FestivalIntelligenceAgent()
        result = agent.collect_festival_data(festival_name)
        return result
    except Exception as e:
        print(f"Error collecting festival data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/monid/social-sentiment")
async def monid_collect_social_sentiment(
    artist_name: str = Query(...),
    platform: str = Query("twitter")
):
    """Agentic social media sentiment collection."""
    try:
        agent = FestivalIntelligenceAgent()
        result = agent.collect_social_sentiment(artist_name, platform)
        return result
    except Exception as e:
        print(f"Error collecting social sentiment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
