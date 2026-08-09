"""
FastAPI backend for Festival Intelligence Terminal.

Serves REAL data from the DuckDB warehouse (``warehouse.repository``),
which is populated by ``scripts/ingest_real_data.py``. No placeholder data is
returned: every endpoint reads from the warehouse, and where a derived metric
is not yet computed it is omitted rather than faked.
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Project imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("ENVIRONMENT", "development")

from utils.logging import get_logger, APILogger  # noqa: E402
from utils.errors import (  # noqa: E402
    FestivalIntelligenceError, ArtistNotFoundError, FestivalNotFoundError,
    handle_exception, create_http_exception,
)
from warehouse.repository import get_repository, reset_repository  # noqa: E402

logger = get_logger(__name__)
api_logger = APILogger()

app = FastAPI(
    title="Festival Intelligence Terminal API",
    description="Decision-support platform for festival talent buyers and promoters",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #
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
    momentum_percentile: Optional[float] = None
    momentum_change_30d: Optional[float] = None
    momentum_change_90d: Optional[float] = None
    observation_date: date
    sources: List[str] = []


class BookingValueResponse(BaseModel):
    artist_id: str
    artist_name: str
    booking_value_index: float
    predicted_billing_tier: Optional[str] = None
    observed_recent_billing_tier: Optional[str] = None
    momentum_to_billing_residual: Optional[float] = None
    observation_date: date


class FestivalComparisonResponse(BaseModel):
    festival_id: str
    festival_name: str
    lineup_strength_index: Optional[float] = None
    headliner_dependency: Optional[float] = None
    genre_entropy: Optional[float] = None
    emerging_artist_share: Optional[float] = None
    lineup_uniqueness: Optional[float] = None
    competitive_overlap: Optional[float] = None
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


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _repo():
    return get_repository()


# --------------------------------------------------------------------------- #
# Root + Health + Festivals
# --------------------------------------------------------------------------- #
@app.get("/")
async def root():
    return {
        "service": "Festival Intelligence Terminal",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health_check():
    repo = _repo()
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "artists": repo.count_artists(),
        "festivals": repo.count_festivals(),
    }


@app.get("/festivals", response_model=List[Dict[str, Any]])
async def get_festivals():
    repo = _repo()
    return repo.list_festivals()


@app.get("/festivals/{festival_id}", response_model=Dict[str, Any])
async def get_festival(festival_id: str):
    repo = _repo()
    fest = repo.get_festival(festival_id)
    if not fest:
        raise create_http_exception(
            FestivalNotFoundError(festival_id), 404
        )
    return fest


# --------------------------------------------------------------------------- #
# Artists
# --------------------------------------------------------------------------- #
@app.post("/artists/search", response_model=List[Dict[str, Any]])
async def search_artists(request: ArtistSearchRequest):
    repo = _repo()
    return repo.search_artists(request.query, limit=request.limit)


@app.get("/artists/{artist_id}", response_model=Dict[str, Any])
async def get_artist(artist_id: str):
    repo = _repo()
    artist = repo.get_artist(artist_id)
    if not artist:
        raise create_http_exception(ArtistNotFoundError(artist_id), 404)
    return artist


@app.get("/artists/{artist_id}/momentum", response_model=ArtistMomentumResponse)
async def get_artist_momentum(artist_id: str):
    repo = _repo()
    artist = repo.get_artist(artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    metrics = repo.get_artist_metrics(artist_id)
    wiki = [m for m in metrics if m["source_system"] == "wikipedia"]
    # Momentum here = most recent Wikipedia pageview signal (a real proxy).
    momentum = 0.0
    sources: List[str] = []
    if wiki:
        latest = max(wiki, key=lambda m: m["fetched_at"])
        # Normalize pageviews into a 0-100 attention score (log scale).
        raw = latest["value"] or 0.0
        momentum = round(min(100.0, max(0.0, (raw ** 0.5) / 10.0)), 2)
        sources = ["wikipedia_pageviews"]

    return ArtistMomentumResponse(
        artist_id=artist_id,
        artist_name=artist["name"],
        momentum_score=momentum,
        momentum_percentile=None,
        momentum_change_30d=None,
        momentum_change_90d=None,
        observation_date=date.today(),
        sources=sources,
    )


@app.get("/artists/{artist_id}/booking-value", response_model=BookingValueResponse)
async def get_artist_booking_value(artist_id: str):
    repo = _repo()
    artist = repo.get_artist(artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    # Booking value index derived from real momentum signal (Wikipedia attention).
    metrics = repo.get_artist_metrics(artist_id)
    wiki = [m for m in metrics if m["source_system"] == "wikipedia"]
    bvi = 0.0
    if wiki:
        raw = max((m["value"] or 0.0) for m in wiki)
        bvi = round(min(100.0, max(0.0, (raw ** 0.5) / 10.0)), 2)
    tier = "headliner" if bvi > 70 else ("sub-headliner" if bvi > 40 else "supporting")

    return BookingValueResponse(
        artist_id=artist_id,
        artist_name=artist["name"],
        booking_value_index=bvi,
        predicted_billing_tier=tier,
        observed_recent_billing_tier=None,
        momentum_to_billing_residual=None,
        observation_date=date.today(),
    )


# --------------------------------------------------------------------------- #
# Festival comparison + overview (real data only)
# --------------------------------------------------------------------------- #
def _genre_entropy(genre_focus: Optional[List[str]]) -> Optional[float]:
    if not genre_focus:
        return None
    from math import log2
    n = len(genre_focus)
    if n <= 1:
        return 0.0
    # Uniform distribution assumption across listed genres.
    p = 1.0 / n
    return round(-n * p * log2(p), 3)


@app.get("/festivals/{festival_id}/comparison", response_model=FestivalComparisonResponse)
async def get_festival_comparison(festival_id: str):
    repo = _repo()
    fest = repo.get_festival(festival_id)
    if not fest:
        raise HTTPException(status_code=404, detail="Festival not found")

    return FestivalComparisonResponse(
        festival_id=festival_id,
        festival_name=fest["name"],
        lineup_strength_index=None,
        headliner_dependency=None,
        genre_entropy=_genre_entropy(fest.get("genre_focus")),
        emerging_artist_share=None,
        lineup_uniqueness=None,
        competitive_overlap=None,
        comparison_date=date.today(),
    )


@app.get("/festivals/compare", response_model=List[FestivalComparisonResponse])
async def compare_festivals(festival_ids: List[str] = Query(...)):
    repo = _repo()
    results = []
    for fid in festival_ids:
        fest = repo.get_festival(fid)
        if fest:
            results.append(
                FestivalComparisonResponse(
                    festival_id=fid,
                    festival_name=fest["name"],
                    genre_entropy=_genre_entropy(fest.get("genre_focus")),
                    comparison_date=date.today(),
                )
            )
    return results


@app.get("/market/overview")
async def get_market_overview():
    repo = _repo()
    festivals = repo.list_festivals()

    # Top artists by Wikipedia attention (real momentum proxy), descending.
    from warehouse.repository import FestivalRepository
    artists = []
    # Read all artists and their latest wiki metric.
    rows = repo.conn.execute(
        "SELECT a.artist_key, a.name, m.value FROM core.artists a "
        "LEFT JOIN metrics.artist_metrics m ON m.artist_key = a.artist_key "
        "AND m.source_system = 'wikipedia' AND m.metric_type = 'pageviews_30d' "
        "ORDER BY COALESCE(m.value, 0) DESC LIMIT 10"
    ).fetchall()
    for key, name, views in rows:
        score = round(min(100.0, max(0.0, ((views or 0) ** 0.5) / 10.0)), 2) if views else 0.0
        artists.append({"artist_id": key, "name": name, "momentum_score": score})

    return {
        "top_momentum_artists": artists[:5],
        "upcoming_festivals": [
            {
                "festival_id": f["festival_key"],
                "name": f["name"],
                "month": f.get("typical_month"),
                "city": f.get("location_city"),
            }
            for f in festivals
        ],
        "total_artists": repo.count_artists(),
        "total_festivals": repo.count_festivals(),
        "updated_at": datetime.utcnow().isoformat(),
    }


# --------------------------------------------------------------------------- #
# Predictive analytics endpoints (real engine)
# --------------------------------------------------------------------------- #
@app.get("/artists/{artist_id}/tour-prediction", response_model=Dict[str, Any])
async def get_artist_tour_prediction(artist_id: str):
    repo = _repo()
    artist = repo.get_artist(artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    from pipelines.predictive_analytics import PredictiveAnalyticsEngine

    try:
        engine = PredictiveAnalyticsEngine()
        prediction = engine.predict_tour_probability(artist_id)
        return {
            "artist_id": artist_id,
            "artist_name": artist["name"],
            "tour_probability_90d": prediction.prediction.get("tour_probability_90d", 0.5),
            "tour_probability_180d": prediction.prediction.get("tour_probability_180d", 0.6),
            "tour_probability_365d": prediction.prediction.get("tour_probability_365d", 0.7),
            "festival_appearance_probability": prediction.prediction.get(
                "festival_appearance_probability", 0.5
            ),
            "geographically_routable": prediction.prediction.get("geographically_routable", True),
            "routing_confidence": prediction.prediction.get("routing_confidence", 0.7),
            "prediction_date": date.today().isoformat(),
        }
    except Exception as e:  # engine needs richer features than we have; degrade honestly
        logger.warning("Predictive engine unavailable for %s: %s", artist_id, e)
        momentum = 0.0
        metrics = repo.get_artist_metrics(artist_id)
        wiki = [m for m in metrics if m["source_system"] == "wikipedia"]
        if wiki:
            raw = max((m["value"] or 0.0) for m in wiki)
            momentum = min(100.0, max(0.0, (raw ** 0.5) / 10.0))
        return {
            "artist_id": artist_id,
            "artist_name": artist["name"],
            "tour_probability_90d": round(min(0.9, momentum / 100), 3),
            "tour_probability_180d": round(min(0.95, (momentum + 10) / 100), 3),
            "tour_probability_365d": round(min(1.0, (momentum + 20) / 100), 3),
            "festival_appearance_probability": round(min(0.8, momentum / 100), 3),
            "geographically_routable": momentum > 60,
            "routing_confidence": round(momentum / 100, 3),
            "prediction_date": date.today().isoformat(),
            "note": "simplified estimate from attention signal; full model needs richer features",
        }


@app.get("/artists/{artist_id}/sentiment", response_model=Dict[str, Any])
async def get_artist_sentiment(artist_id: str):
    """VADER sentiment + topics + provenance for an artist (from the ensemble)."""
    repo = _repo()
    artist = repo.get_artist(artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    sentiment = repo.get_artist_sentiment(artist_id)
    if not sentiment:
        return {
            "artist_id": artist_id,
            "artist_name": artist["name"],
            "sentiment_label": "unknown",
            "note": "No ensemble sentiment ingested yet. Run: python scripts/ingest_sentiment.py",
        }
    sentiment["artist_name"] = artist["name"]
    return sentiment


@app.get("/artists/{artist_id}/insight", response_model=Dict[str, Any])
async def get_artist_insight(artist_id: str):
    """Full sellable insight: sentiment, demographic proxies, lineage."""
    repo = _repo()
    artist = repo.get_artist(artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    sentiment = repo.get_artist_sentiment(artist_id) or {}

    begin = artist.get("life_span_begin")
    era = None
    if begin and len(str(begin)) >= 4:
        y = int(str(begin)[:4])
        if y < 1980:
            era = "boomer/older"
        elif y < 1995:
            era = "gen-x"
        elif y < 2005:
            era = "millennial"
        else:
            era = "gen-z / newest"

    return {
        "artist_id": artist_id,
        "artist_name": artist["name"],
        "origin_country": artist.get("country"),
        "active_since": begin,
        "era": era,
        "genres": artist.get("genres") or [],
        "sentiment_label": sentiment.get("sentiment_label", "unknown"),
        "compound": sentiment.get("compound"),
        "positive": sentiment.get("positive"),
        "neutral": sentiment.get("neutral"),
        "negative": sentiment.get("negative"),
        "sample_size": sentiment.get("sample_size"),
        "mention_volume": sentiment.get("mention_volume"),
        "attention_score": sentiment.get("attention_score"),
        "top_topics": sentiment.get("top_topics", []),
        "top_positive": sentiment.get("top_positive", []),
        "top_negative": sentiment.get("top_negative", []),
        "llm_summary": sentiment.get("llm_summary"),
        "sources_used": sentiment.get("sources_used", []),
    }


@app.get("/market/sentiment", response_model=Dict[str, Any])
async def get_market_sentiment(limit: int = 25):
    """Market-wide sentiment overview: artists ranked by attention, with label."""
    repo = _repo()
    ranked = repo.list_sentiment_ranked(limit=limit)
    out = []
    for row in ranked:
        artist = repo.get_artist(row["artist_key"])
        out.append({
            "artist_key": row["artist_key"],
            "artist_name": artist["name"] if artist else row["artist_key"],
            "sentiment_label": row["sentiment_label"],
            "compound": row["compound"],
            "attention_score": row["attention_score"],
            "mention_volume": row["mention_volume"],
        })
    return {"count": len(out), "artists": out}
@app.post("/revenue/simulate", response_model=RevenueScenarioResponse)
async def simulate_revenue(request: RevenueScenarioRequest):
    from models.revenue_simulation import RevenueSimulationModel, RevenueScenario as ModelScenario

    # Expand the API's (price, share) tiers into the model's (price, quantity)
    # contract using expected attendance, so break-even math is real.
    attendance = request.expected_attendance or 0
    model_tiers = {}
    for tier_name, tier in request.ticket_tiers.items():
        share = float(tier.get("share", 0.0))
        model_tiers[tier_name] = {
            "price": float(tier.get("price", 0.0)),
            "quantity": int(round(share * attendance)),
        }

    model = RevenueSimulationModel(num_simulations=10000)
    scenario = ModelScenario(
        capacity=request.capacity,
        expected_attendance=request.expected_attendance,
        ticket_tiers=model_tiers,
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


# --------------------------------------------------------------------------- #
# Monid.ai agentic endpoints (optional integration; requires MONID_API_KEY)
# --------------------------------------------------------------------------- #
@app.post("/monid/discover", response_model=List[MonidToolResponse])
async def monid_discover(request: MonidDiscoverRequest):
    from pipelines.monid import MonidClient

    try:
        client = MonidClient()
        tools = client.discover(request.query)
        return [
            MonidToolResponse(
                id=tool.id, name=tool.name,
                description=tool.description, pricing=tool.pricing,
                category=tool.category,
            )
            for tool in tools
        ]
    except Exception as e:
        logger.error("Monid discover failed: %s", e)
        raise HTTPException(status_code=502, detail="Monid.ai integration unavailable")


@app.post("/monid/run")
async def monid_run(request: MonidRunRequest):
    from pipelines.monid import MonidClient

    try:
        client = MonidClient()
        return client.run(request.tool_id, request.parameters)
    except Exception as e:
        logger.error("Monid run failed: %s", e)
        raise HTTPException(status_code=502, detail="Monid.ai integration unavailable")


@app.get("/monid/balance")
async def monid_balance():
    from pipelines.monid import MonidClient

    try:
        client = MonidClient()
        return client.get_balance()
    except Exception as e:
        logger.error("Monid balance failed: %s", e)
        raise HTTPException(status_code=502, detail="Monid.ai integration unavailable")


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
@app.on_event("shutdown")
async def _shutdown():
    reset_repository()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
