-- Festival Intelligence Terminal Database Schema
-- This schema defines the normalized analytical tables

-- Enable UUID extension if using PostgreSQL
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Canonical Artist Table
CREATE TABLE IF NOT EXISTS artists (
    musicbrainz_id VARCHAR(36) PRIMARY KEY,
    wikidata_id VARCHAR(20),
    ticketmaster_id VARCHAR(50),
    youtube_channel_id VARCHAR(50),
    spotify_id VARCHAR(50),
    setlistfm_id VARCHAR(50),
    normalized_name VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    aliases JSONB,
    country VARCHAR(3),
    genre VARCHAR(100),
    genres JSONB,
    formed_year INTEGER,
    disband_year INTEGER,
    name_confidence DECIMAL(3,2) DEFAULT 1.0,
    manually_reviewed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_artists_normalized_name ON artists(normalized_name);
CREATE INDEX idx_artists_wikidata ON artists(wikidata_id);
CREATE INDEX idx_artists_ticketmaster ON artists(ticketmaster_id);

-- Canonical Festival Table
CREATE TABLE IF NOT EXISTS festivals (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    normalized_name VARCHAR(255) NOT NULL,
    city VARCHAR(100) NOT NULL,
    state VARCHAR(50),
    country VARCHAR(3) DEFAULT 'US',
    latitude DECIMAL(9,6),
    longitude DECIMAL(9,6),
    typical_month INTEGER,
    typical_duration_days INTEGER,
    capacity INTEGER,
    genre_focus VARCHAR(100),
    festival_type VARCHAR(100),
    ticketmaster_id VARCHAR(50),
    wikidata_id VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_festivals_city ON festivals(city, state);

-- Canonical Venue Table
CREATE TABLE IF NOT EXISTS venues (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    normalized_name VARCHAR(255) NOT NULL,
    city VARCHAR(100) NOT NULL,
    state VARCHAR(50),
    country VARCHAR(3) DEFAULT 'US',
    latitude DECIMAL(9,6),
    longitude DECIMAL(9,6),
    capacity INTEGER,
    ticketmaster_id VARCHAR(50),
    musicbrainz_id VARCHAR(36),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_venues_city ON venues(city, state);

-- Events Table (concerts, festival appearances, etc.)
CREATE TABLE IF NOT EXISTS events (
    id VARCHAR(50) PRIMARY KEY,
    artist_id VARCHAR(36) NOT NULL REFERENCES artists(musicbrainz_id),
    venue_id VARCHAR(50) REFERENCES venues(id),
    festival_id VARCHAR(50) REFERENCES festivals(id),
    event_date TIMESTAMP NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    billing_tier VARCHAR(50),
    day_of_festival INTEGER,
    ticketmaster_id VARCHAR(50),
    setlistfm_id VARCHAR(50),
    source VARCHAR(50) NOT NULL,
    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confidence DECIMAL(3,2) DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_events_artist ON events(artist_id);
CREATE INDEX idx_events_venue ON events(venue_id);
CREATE INDEX idx_events_festival ON events(festival_id);
CREATE INDEX idx_events_date ON events(event_date);
CREATE INDEX idx_events_type ON events(event_type);

-- Festival Lineup Table
CREATE TABLE IF NOT EXISTS festival_lineups (
    festival_id VARCHAR(50) NOT NULL REFERENCES festivals(id),
    year INTEGER NOT NULL,
    artist_id VARCHAR(36) NOT NULL REFERENCES artists(musicbrainz_id),
    billing_tier VARCHAR(50) NOT NULL,
    day_of_festival INTEGER,
    stage VARCHAR(100),
    source VARCHAR(50) NOT NULL,
    confidence DECIMAL(3,2) DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (festival_id, year, artist_id)
);

CREATE INDEX idx_lineups_festival_year ON festival_lineups(festival_id, year);
CREATE INDEX idx_lineups_artist ON festival_lineups(artist_id);

-- Artist Momentum Table
CREATE TABLE IF NOT EXISTS artist_momentum (
    artist_id VARCHAR(36) NOT NULL REFERENCES artists(musicbrainz_id),
    observation_date DATE NOT NULL,
    momentum_score DECIMAL(5,2) NOT NULL,
    momentum_percentile DECIMAL(5,2) NOT NULL,
    youtube_momentum DECIMAL(5,2),
    wiki_momentum DECIMAL(5,2),
    news_momentum DECIMAL(5,2),
    momentum_change_30d DECIMAL(5,2),
    momentum_change_90d DECIMAL(5,2),
    model_version VARCHAR(50) NOT NULL,
    feature_version VARCHAR(50) NOT NULL,
    metric_type VARCHAR(20) DEFAULT 'modeled',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (artist_id, observation_date)
);

CREATE INDEX idx_momentum_date ON artist_momentum(observation_date);

-- Booking Value Index Table
CREATE TABLE IF NOT EXISTS booking_value_index (
    artist_id VARCHAR(36) NOT NULL REFERENCES artists(musicbrainz_id),
    observation_date DATE NOT NULL,
    booking_value_index DECIMAL(5,2) NOT NULL,
    predicted_billing_tier VARCHAR(50),
    predicted_festival_demand_rank INTEGER,
    observed_recent_billing_tier VARCHAR(50),
    momentum_to_billing_residual DECIMAL(5,2),
    youtube_growth_score DECIMAL(5,2),
    wiki_growth_score DECIMAL(5,2),
    news_volume_score DECIMAL(5,2),
    live_performance_frequency DECIMAL(5,2),
    venue_progression_score DECIMAL(5,2),
    festival_billing_history_score DECIMAL(5,2),
    headliner_frequency_score DECIMAL(5,2),
    market_diversity_score DECIMAL(5,2),
    release_recency_score DECIMAL(5,2),
    genre_momentum_score DECIMAL(5,2),
    competition_score DECIMAL(5,2),
    local_affinity_score DECIMAL(5,2),
    model_version VARCHAR(50) NOT NULL,
    feature_version VARCHAR(50) NOT NULL,
    metric_type VARCHAR(20) DEFAULT 'modeled',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (artist_id, observation_date)
);

CREATE INDEX idx_bvi_date ON booking_value_index(observation_date);

-- Tour Prediction Table
CREATE TABLE IF NOT EXISTS tour_predictions (
    artist_id VARCHAR(36) NOT NULL REFERENCES artists(musicbrainz_id),
    prediction_date DATE NOT NULL,
    tour_probability_90d DECIMAL(3,2) NOT NULL,
    tour_probability_180d DECIMAL(3,2) NOT NULL,
    tour_probability_365d DECIMAL(3,2) NOT NULL,
    festival_appearance_probability DECIMAL(3,2),
    market_appearance_probability DECIMAL(3,2),
    geographically_routable BOOLEAN,
    routing_confidence DECIMAL(3,2),
    model_version VARCHAR(50) NOT NULL,
    feature_version VARCHAR(50) NOT NULL,
    metric_type VARCHAR(20) DEFAULT 'modeled',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (artist_id, prediction_date)
);

CREATE INDEX idx_tour_pred_date ON tour_predictions(prediction_date);

-- Festival Comparison Table
CREATE TABLE IF NOT EXISTS festival_comparisons (
    festival_id VARCHAR(50) NOT NULL REFERENCES festivals(id),
    comparison_date DATE NOT NULL,
    lineup_strength_index DECIMAL(5,2) NOT NULL,
    headliner_dependency DECIMAL(3,2) NOT NULL,
    genre_entropy DECIMAL(5,2) NOT NULL,
    emerging_artist_share DECIMAL(3,2) NOT NULL,
    lineup_uniqueness DECIMAL(3,2) NOT NULL,
    competitive_overlap DECIMAL(3,2),
    average_artist_momentum DECIMAL(5,2),
    market_fit_score DECIMAL(5,2),
    model_version VARCHAR(50) NOT NULL,
    feature_version VARCHAR(50) NOT NULL,
    metric_type VARCHAR(20) DEFAULT 'modeled',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (festival_id, comparison_date)
);

CREATE INDEX idx_fest_comp_date ON festival_comparisons(comparison_date);

-- Revenue Scenarios Table
CREATE TABLE IF NOT EXISTS revenue_scenarios (
    scenario_id VARCHAR(50) PRIMARY KEY,
    festival_id VARCHAR(50) NOT NULL REFERENCES festivals(id),
    scenario_date DATE NOT NULL,
    capacity INTEGER NOT NULL,
    expected_attendance INTEGER NOT NULL,
    ticket_tiers JSONB NOT NULL,
    vip_mix DECIMAL(3,2) DEFAULT 0.0,
    sponsorship_commitments DECIMAL(12,2) DEFAULT 0.0,
    per_capita_fnb_spending DECIMAL(10,2) DEFAULT 0.0,
    per_capita_merch_spending DECIMAL(10,2) DEFAULT 0.0,
    artist_cost_min DECIMAL(12,2) NOT NULL,
    artist_cost_max DECIMAL(12,2) NOT NULL,
    production_costs DECIMAL(12,2) DEFAULT 0.0,
    weather_assumption VARCHAR(100),
    ticket_revenue DECIMAL(12,2) NOT NULL,
    ancillary_revenue DECIMAL(12,2) NOT NULL,
    total_revenue DECIMAL(12,2) NOT NULL,
    artist_costs DECIMAL(12,2) NOT NULL,
    contribution_margin DECIMAL(12,2) NOT NULL,
    p10_downside DECIMAL(12,2),
    p50_base_case DECIMAL(12,2),
    p90_upside DECIMAL(12,2),
    profitability_probability DECIMAL(3,2),
    break_even_attendance INTEGER,
    break_even_ticket_price DECIMAL(10,2),
    revenue_at_risk_weather DECIMAL(12,2),
    artist_sensitivity JSONB,
    model_version VARCHAR(50) NOT NULL,
    metric_type VARCHAR(20) DEFAULT 'assumption',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_revenue_scenarios_festival ON revenue_scenarios(festival_id);

-- Location Intelligence Table
CREATE TABLE IF NOT EXISTS location_intelligence (
    festival_id VARCHAR(50) NOT NULL REFERENCES festivals(id),
    observation_date DATE NOT NULL,
    weather_risk_score DECIMAL(5,2),
    heat_stress_score DECIMAL(5,2),
    rain_disruption_probability DECIMAL(3,2),
    expected_weather_adjusted_attendance DECIMAL(10,2),
    air_access_score DECIMAL(5,2),
    weighted_avg_origin_distance DECIMAL(10,2),
    direct_flight_coverage DECIMAL(3,2),
    historical_passenger_capacity INTEGER,
    travel_cost_index DECIMAL(5,2),
    hotel_pressure_proxy DECIMAL(5,2),
    estimated_overnight_visitors INTEGER,
    estimated_room_supply INTEGER,
    market_population INTEGER,
    median_income DECIMAL(12,2),
    age_distribution JSONB,
    model_version VARCHAR(50) NOT NULL,
    feature_version VARCHAR(50) NOT NULL,
    metric_type VARCHAR(20) DEFAULT 'modeled',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (festival_id, observation_date)
);

-- Data Provenance Table (for tracking data lineage)
CREATE TABLE IF NOT EXISTS data_provenance (
    id SERIAL PRIMARY KEY,
    table_name VARCHAR(100) NOT NULL,
    record_id VARCHAR(100) NOT NULL,
    source VARCHAR(50) NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    observation_date DATE,
    model_version VARCHAR(50),
    feature_version VARCHAR(50),
    confidence DECIMAL(3,2) DEFAULT 1.0,
    is_observed BOOLEAN DEFAULT TRUE,
    is_estimated BOOLEAN DEFAULT FALSE,
    is_synthetic BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_provenance_table ON data_provenance(table_name, record_id);
