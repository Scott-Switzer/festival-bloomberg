"""
Unified Festival Bloomberg warehouse repository interface.

This is the canonical read/write contract for the Festival Bloomberg warehouse,
consolidating functionality from both the intelligence and main codebases.
It supports point-in-time data modeling, entity resolution, feature stores,
and comprehensive source governance.

Storage layout (matches the Festival Bloomberg spec schemas):
    core.artists           - canonical artist dimension (MBID-resolved)
    core.festivals         - canonical festival dimension
    core.festival_editions - (festival, year) occurrence
    core.lineup_slots      - resolved per-artist performance slots
    metrics.artist_metrics - per-source momentum/attention observations
    metrics.artist_feature_store - point-in-time feature storage
    metrics.artist_factors - artist factor model outputs
    metrics.expected_billing - expected billing baseline model
    metrics.relative_value - relative value calculations
    metrics.festival_portfolio - festival portfolio analytics
    raw.lineup_observations - raw lineup scrape/parse records

The full DDL (including the rich artist/festival/lineup specifications and the
entity-resolution support tables) lives in ``schema/duckdb.sql`` and is applied
through ``warehouse.schema_loader``.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Dict, Iterator, List, Optional

import duckdb

from .duckdb_manager import DuckDBWarehouse, create_warehouse
from .schema_loader import SCHEMA_PATH, apply_schema

logger = logging.getLogger(__name__)

# Default on-disk location. Relative to project data dir so it persists.
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "warehouse",
    "festival_bloomberg.duckdb",
)


# --------------------------------------------------------------------------- #
# DDL
# --------------------------------------------------------------------------- #
# Canonical DDL location, re-exported for tooling that applies the schema
# without instantiating a repository.
SCHEMA_DDL_PATH = SCHEMA_PATH


def _apply_schema(wh: DuckDBWarehouse) -> None:
    """Apply ``schema/duckdb.sql`` to the warehouse connection (idempotent)."""
    apply_schema(wh.connection)


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
class FestivalRepository:
    """Unified read/write access to the Festival Bloomberg warehouse.

    A single instance owns one DuckDB connection. Use as a context manager or
    call :meth:`close` explicitly.
    
    This repository consolidates functionality from both the intelligence and
    main codebases, with support for:
    - Point-in-time data modeling with temporal fields
    - Entity resolution and canonical data management
    - Feature store for backtesting and analytics
    - Artist factors, expected billing, and relative value calculations
    - Festival portfolio analytics
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH, read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only
        self._warehouse = create_warehouse(db_path, read_only=read_only)
        if not read_only:
            _apply_schema(self._warehouse)

    # -- lifecycle ---------------------------------------------------------- #
    @property
    def warehouse(self) -> DuckDBWarehouse:
        return self._warehouse

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Non-optional connection handle (safe: warehouse always connects)."""
        conn = self._warehouse.connection
        if conn is None:  # pragma: no cover - connection is created in __init__
            self._warehouse._initialize_connection()
            conn = self._warehouse.connection
        assert conn is not None, "DuckDB connection failed to initialize"
        return conn

    def close(self) -> None:
        self._warehouse.close()

    def __enter__(self) -> "FestivalRepository":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- write: artists ------------------------------------------------------ #
    def upsert_artist(self, artist: Dict[str, Any], source_system: str = "musicbrainz") -> str:
        """Insert or update a canonical artist. Returns the artist_key."""
        mb_id = artist.get("musicbrainz_id")
        key = mb_id or f"name::{artist['normalized_name']}"
        self.conn.execute(
            """
            INSERT INTO core.artists
                (artist_key, musicbrainz_id, name, normalized_name, disambiguation,
                 country, genres, type, life_span_begin, life_span_end,
                 source_system, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (artist_key) DO UPDATE SET
                musicbrainz_id = excluded.musicbrainz_id,
                name = excluded.name,
                normalized_name = excluded.normalized_name,
                disambiguation = excluded.disambiguation,
                country = excluded.country,
                genres = excluded.genres,
                type = excluded.type,
                life_span_begin = excluded.life_span_begin,
                life_span_end = excluded.life_span_end,
                source_system = excluded.source_system,
                ingested_at = excluded.ingested_at
            """,
            [
                key,
                mb_id,
                artist["name"],
                artist["normalized_name"],
                artist.get("disambiguation"),
                artist.get("country"),
                json.dumps(artist.get("genres") or []),
                artist.get("type"),
                artist.get("life_span_begin"),
                artist.get("life_span_end"),
                source_system,
                datetime.utcnow(),
            ],
        )
        return key

    def upsert_festival(self, festival: Dict[str, Any], source_system: str = "c3") -> str:
        key = festival.get("festival_key") or f"name::{festival['normalized_name']}"
        self.conn.execute(
            """
            INSERT INTO core.festivals
                (festival_key, name, normalized_name, location_country, location_city,
                 location_region, capacity, genre_focus, festival_type, venue_type,
                 duration_days, typical_month, source_system, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (festival_key) DO UPDATE SET
                name = excluded.name,
                normalized_name = excluded.normalized_name,
                location_country = excluded.location_country,
                location_city = excluded.location_city,
                location_region = excluded.location_region,
                capacity = excluded.capacity,
                genre_focus = excluded.genre_focus,
                festival_type = excluded.festival_type,
                venue_type = excluded.venue_type,
                duration_days = excluded.duration_days,
                typical_month = excluded.typical_month,
                source_system = excluded.source_system,
                ingested_at = excluded.ingested_at
            """,
            [
                key,
                festival["name"],
                festival["normalized_name"],
                festival.get("location_country"),
                festival.get("location_city"),
                festival.get("location_region"),
                festival.get("capacity"),
                json.dumps(festival.get("genre_focus") or []),
                festival.get("festival_type"),
                festival.get("venue_type"),
                festival.get("duration_days"),
                festival.get("typical_month"),
                source_system,
                datetime.utcnow(),
            ],
        )
        return key

    # -- write: point-in-time metrics ---------------------------------------- #
    def insert_artist_metric(
        self,
        artist_key: str,
        source_system: str,
        metric_type: str,
        value: Optional[float],
        observed_date: Optional[date] = None,
        source_publication_time: Optional[datetime] = None,
        source_as_of: Optional[datetime] = None,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        knowledge_time: Optional[datetime] = None,
        source_url: Optional[str] = None,
        source_record_id: Optional[str] = None,
        confidence: Optional[float] = None,
        quality_flags: Optional[Dict[str, Any]] = None,
        license_class: Optional[str] = None,
        commercial_use_status: Optional[str] = None,
        feature_version: Optional[str] = None,
        model_version: Optional[str] = None,
        meta_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert an artist metric with full point-in-time temporal fields."""
        metric_key = f"{artist_key}::{source_system}::{metric_type}"
        self.conn.execute(
            """
            INSERT INTO metrics.artist_metrics
                (metric_key, artist_key, source_system, metric_type, value,
                 observed_date, source_publication_time, source_as_of, retrieved_at,
                 valid_from, valid_to, knowledge_time, calculated_at,
                 source_url, source_record_id, confidence, quality_flags,
                 license_class, commercial_use_status, feature_version, model_version,
                 meta_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (metric_key) DO UPDATE SET
                value = excluded.value,
                observed_date = excluded.observed_date,
                source_publication_time = excluded.source_publication_time,
                source_as_of = excluded.source_as_of,
                retrieved_at = excluded.retrieved_at,
                valid_from = excluded.valid_from,
                valid_to = excluded.valid_to,
                knowledge_time = excluded.knowledge_time,
                calculated_at = excluded.calculated_at,
                source_url = excluded.source_url,
                source_record_id = excluded.source_record_id,
                confidence = excluded.confidence,
                quality_flags = excluded.quality_flags,
                license_class = excluded.license_class,
                commercial_use_status = excluded.commercial_use_status,
                feature_version = excluded.feature_version,
                model_version = excluded.model_version,
                meta_data = excluded.meta_data
            """,
            [
                metric_key,
                artist_key,
                source_system,
                metric_type,
                value,
                observed_date.isoformat() if observed_date else None,
                source_publication_time.isoformat() if source_publication_time else None,
                source_as_of.isoformat() if source_as_of else None,
                datetime.utcnow(),
                valid_from.isoformat() if valid_from else None,
                valid_to.isoformat() if valid_to else None,
                knowledge_time.isoformat() if knowledge_time else None,
                datetime.utcnow(),
                source_url,
                source_record_id,
                confidence,
                json.dumps(quality_flags or {}),
                license_class,
                commercial_use_status,
                feature_version,
                model_version,
                json.dumps(meta_data or {}),
            ],
        )

    def insert_lineup_observation(
        self,
        artist_name: str,
        festival_key: Optional[str] = None,
        edition_year: Optional[int] = None,
        position: Optional[str] = None,
        stage: Optional[str] = None,
        day: Optional[str] = None,
        source_url: Optional[str] = None,
        parser_version: Optional[str] = None,
        observed_raw: Optional[Dict[str, Any]] = None,
        source_publication_time: Optional[datetime] = None,
        source_as_of: Optional[datetime] = None,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        knowledge_time: Optional[datetime] = None,
        confidence: Optional[float] = None,
        quality_flags: Optional[Dict[str, Any]] = None,
        license_class: Optional[str] = None,
        commercial_use_status: Optional[str] = None,
        feature_version: Optional[str] = None,
    ) -> None:
        """Insert a lineup observation with point-in-time temporal fields."""
        import hashlib

        raw = json.dumps(observed_raw or {}, sort_keys=True)
        obs_key = hashlib.sha256(
            f"{festival_key}|{edition_year}|{artist_name}|{position}".encode()
        ).hexdigest()[:16]
        self.conn.execute(
            """
            INSERT INTO raw.lineup_observations
                (observation_key, festival_key, edition_year, artist_name, position,
                 stage, day, source_url, source_publication_time, source_as_of,
                 retrieved_at, valid_from, valid_to, knowledge_time,
                 parser_version, observed_raw, confidence, quality_flags,
                 license_class, commercial_use_status, feature_version, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (observation_key) DO NOTHING
            """,
            [
                obs_key,
                festival_key,
                edition_year,
                artist_name,
                position,
                stage,
                day,
                source_url,
                source_publication_time.isoformat() if source_publication_time else None,
                source_as_of.isoformat() if source_as_of else None,
                datetime.utcnow(),
                valid_from.isoformat() if valid_from else None,
                valid_to.isoformat() if valid_to else None,
                knowledge_time.isoformat() if knowledge_time else None,
                parser_version,
                raw,
                confidence,
                json.dumps(quality_flags or {}),
                license_class,
                commercial_use_status,
                feature_version,
                datetime.utcnow(),
            ],
        )

    # -- write: feature store ------------------------------------------------ #
    def insert_feature(
        self,
        artist_key: str,
        feature_name: str,
        feature_value: float,
        feature_type: str = "derived",
        feature_category: Optional[str] = None,
        feature_date: Optional[date] = None,
        source_publication_time: Optional[datetime] = None,
        source_as_of: Optional[datetime] = None,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        knowledge_time: Optional[datetime] = None,
        feature_version: Optional[str] = None,
        model_version: Optional[str] = None,
        formula: Optional[str] = None,
        input_features: Optional[List[str]] = None,
        confidence: Optional[float] = None,
        quality_flags: Optional[Dict[str, Any]] = None,
        source_system: Optional[str] = None,
        source_url: Optional[str] = None,
        source_record_id: Optional[str] = None,
        license_class: Optional[str] = None,
        commercial_use_status: Optional[str] = None,
        festival_key: Optional[str] = None,
        edition_key: Optional[str] = None,
        edition_year: Optional[int] = None,
        evidence_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert a feature into the point-in-time feature store."""
        feature_key = f"{artist_key}::{feature_name}::{feature_date.isoformat() if feature_date else 'latest'}"
        self.conn.execute(
            """
            INSERT INTO metrics.artist_feature_store
                (feature_key, artist_key, festival_key, edition_key, edition_year,
                 feature_name, feature_type, feature_value, feature_category,
                 feature_date, source_publication_time, source_as_of, retrieved_at,
                 valid_from, valid_to, knowledge_time, calculated_at,
                 feature_version, model_version, formula, input_features,
                 confidence, quality_flags, source_system, source_url, source_record_id,
                 license_class, commercial_use_status, evidence_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (feature_key) DO UPDATE SET
                feature_value = excluded.feature_value,
                feature_type = excluded.feature_type,
                feature_category = excluded.feature_category,
                feature_date = excluded.feature_date,
                source_publication_time = excluded.source_publication_time,
                source_as_of = excluded.source_as_of,
                retrieved_at = excluded.retrieved_at,
                valid_from = excluded.valid_from,
                valid_to = excluded.valid_to,
                knowledge_time = excluded.knowledge_time,
                calculated_at = excluded.calculated_at,
                feature_version = excluded.feature_version,
                model_version = excluded.model_version,
                formula = excluded.formula,
                input_features = excluded.input_features,
                confidence = excluded.confidence,
                quality_flags = excluded.quality_flags,
                source_system = excluded.source_system,
                source_url = excluded.source_url,
                source_record_id = excluded.source_record_id,
                license_class = excluded.license_class,
                commercial_use_status = excluded.commercial_use_status,
                evidence_json = excluded.evidence_json,
                ingested_at = excluded.ingested_at
            """,
            [
                feature_key,
                artist_key,
                festival_key,
                edition_key,
                edition_year,
                feature_name,
                feature_type,
                feature_value,
                feature_category,
                feature_date.isoformat() if feature_date else None,
                source_publication_time.isoformat() if source_publication_time else None,
                source_as_of.isoformat() if source_as_of else None,
                datetime.utcnow(),
                valid_from.isoformat() if valid_from else None,
                valid_to.isoformat() if valid_to else None,
                knowledge_time.isoformat() if knowledge_time else None,
                datetime.utcnow(),
                feature_version,
                model_version,
                formula,
                json.dumps(input_features or []),
                confidence,
                json.dumps(quality_flags or {}),
                source_system,
                source_url,
                source_record_id,
                license_class,
                commercial_use_status,
                json.dumps(evidence_json or {}),
                datetime.utcnow(),
            ],
        )

    # -- write: artist factors ------------------------------------------------ #
    def insert_artist_factors(
        self,
        artist_key: str,
        momentum_score: float,
        relevance_score: float,
        audience_fit_score: float,
        value_proposition_score: float,
        booking_complexity_score: float,
        risk_score: float,
        momentum_components: Optional[Dict[str, Any]] = None,
        relevance_components: Optional[Dict[str, Any]] = None,
        audience_components: Optional[Dict[str, Any]] = None,
        value_components: Optional[Dict[str, Any]] = None,
        complexity_components: Optional[Dict[str, Any]] = None,
        risk_components: Optional[Dict[str, Any]] = None,
        factor_model_version: Optional[str] = None,
        scoring_method: Optional[str] = None,
        confidence: Optional[float] = None,
        quality_flags: Optional[Dict[str, Any]] = None,
        feature_date: Optional[date] = None,
        source_as_of: Optional[datetime] = None,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        knowledge_time: Optional[datetime] = None,
        source_system: Optional[str] = None,
        festival_key: Optional[str] = None,
        edition_key: Optional[str] = None,
        edition_year: Optional[int] = None,
        evidence_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert artist factor scores."""
        factor_key = f"{artist_key}::{edition_key if edition_key else 'general'}::{feature_date.isoformat() if feature_date else 'latest'}"
        self.conn.execute(
            """
            INSERT INTO metrics.artist_factors
                (factor_key, artist_key, festival_key, edition_key, edition_year,
                 momentum_score, relevance_score, audience_fit_score, value_proposition_score,
                 booking_complexity_score, risk_score, momentum_components, relevance_components,
                 audience_components, value_components, complexity_components, risk_components,
                 factor_model_version, scoring_method, confidence, quality_flags,
                 feature_date, source_as_of, calculated_at, valid_from, valid_to, knowledge_time,
                 source_system, evidence_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (factor_key) DO UPDATE SET
                momentum_score = excluded.momentum_score,
                relevance_score = excluded.relevance_score,
                audience_fit_score = excluded.audience_fit_score,
                value_proposition_score = excluded.value_proposition_score,
                booking_complexity_score = excluded.booking_complexity_score,
                risk_score = excluded.risk_score,
                momentum_components = excluded.momentum_components,
                relevance_components = excluded.relevance_components,
                audience_components = excluded.audience_components,
                value_components = excluded.value_components,
                complexity_components = excluded.complexity_components,
                risk_components = excluded.risk_components,
                factor_model_version = excluded.factor_model_version,
                scoring_method = excluded.scoring_method,
                confidence = excluded.confidence,
                quality_flags = excluded.quality_flags,
                feature_date = excluded.feature_date,
                source_as_of = excluded.source_as_of,
                calculated_at = excluded.calculated_at,
                valid_from = excluded.valid_from,
                valid_to = excluded.valid_to,
                knowledge_time = excluded.knowledge_time,
                source_system = excluded.source_system,
                evidence_json = excluded.evidence_json,
                ingested_at = excluded.ingested_at
            """,
            [
                factor_key,
                artist_key,
                festival_key,
                edition_key,
                edition_year,
                momentum_score,
                relevance_score,
                audience_fit_score,
                value_proposition_score,
                booking_complexity_score,
                risk_score,
                json.dumps(momentum_components or {}),
                json.dumps(relevance_components or {}),
                json.dumps(audience_components or {}),
                json.dumps(value_components or {}),
                json.dumps(complexity_components or {}),
                json.dumps(risk_components or {}),
                factor_model_version,
                scoring_method,
                confidence,
                json.dumps(quality_flags or {}),
                feature_date.isoformat() if feature_date else None,
                source_as_of.isoformat() if source_as_of else None,
                datetime.utcnow(),
                valid_from.isoformat() if valid_from else None,
                valid_to.isoformat() if valid_to else None,
                knowledge_time.isoformat() if knowledge_time else None,
                source_system,
                json.dumps(evidence_json or {}),
                datetime.utcnow(),
            ],
        )

    # -- write: expected billing --------------------------------------------- #
    def insert_expected_billing(
        self,
        artist_key: str,
        expected_billing_tier: str,
        expected_billing_order: int,
        billing_confidence: float,
        booking_probability: Optional[float] = None,
        expected_day: Optional[int] = None,
        expected_stage: Optional[str] = None,
        billing_reasoning: Optional[str] = None,
        billing_factors: Optional[Dict[str, Any]] = None,
        model_version: Optional[str] = None,
        training_period: Optional[str] = None,
        confidence: Optional[float] = None,
        quality_flags: Optional[Dict[str, Any]] = None,
        feature_date: Optional[date] = None,
        source_as_of: Optional[datetime] = None,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        knowledge_time: Optional[datetime] = None,
        source_system: Optional[str] = None,
        festival_key: Optional[str] = None,
        edition_key: Optional[str] = None,
        edition_year: Optional[int] = None,
        evidence_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert expected billing prediction."""
        billing_key = f"{artist_key}::{edition_key if edition_key else 'general'}::{feature_date.isoformat() if feature_date else 'latest'}"
        self.conn.execute(
            """
            INSERT INTO metrics.expected_billing
                (billing_key, artist_key, festival_key, edition_key, edition_year,
                 expected_billing_tier, expected_billing_order, billing_confidence,
                 booking_probability, expected_day, expected_stage, billing_reasoning,
                 billing_factors, model_version, training_period, confidence, quality_flags,
                 feature_date, source_as_of, calculated_at, valid_from, valid_to, knowledge_time,
                 source_system, evidence_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (billing_key) DO UPDATE SET
                expected_billing_tier = excluded.expected_billing_tier,
                expected_billing_order = excluded.expected_billing_order,
                billing_confidence = excluded.billing_confidence,
                booking_probability = excluded.booking_probability,
                expected_day = excluded.expected_day,
                expected_stage = excluded.expected_stage,
                billing_reasoning = excluded.billing_reasoning,
                billing_factors = excluded.billing_factors,
                model_version = excluded.model_version,
                training_period = excluded.training_period,
                confidence = excluded.confidence,
                quality_flags = excluded.quality_flags,
                feature_date = excluded.feature_date,
                source_as_of = excluded.source_as_of,
                calculated_at = excluded.calculated_at,
                valid_from = excluded.valid_from,
                valid_to = excluded.valid_to,
                knowledge_time = excluded.knowledge_time,
                source_system = excluded.source_system,
                evidence_json = excluded.evidence_json,
                ingested_at = excluded.ingested_at
            """,
            [
                billing_key,
                artist_key,
                festival_key,
                edition_key,
                edition_year,
                expected_billing_tier,
                expected_billing_order,
                billing_confidence,
                booking_probability,
                expected_day,
                expected_stage,
                billing_reasoning,
                json.dumps(billing_factors or {}),
                model_version,
                training_period,
                confidence,
                json.dumps(quality_flags or {}),
                feature_date.isoformat() if feature_date else None,
                source_as_of.isoformat() if source_as_of else None,
                datetime.utcnow(),
                valid_from.isoformat() if valid_from else None,
                valid_to.isoformat() if valid_to else None,
                knowledge_time.isoformat() if knowledge_time else None,
                source_system,
                json.dumps(evidence_json or {}),
                datetime.utcnow(),
            ],
        )

    # -- write: relative value ----------------------------------------------- #
    def insert_relative_value(
        self,
        artist_key: str,
        relative_value_score: float,
        value_category: str,
        value_percentile: Optional[float] = None,
        current_billing_tier: Optional[str] = None,
        expected_billing_tier: Optional[str] = None,
        billing_gap: Optional[float] = None,
        momentum_vs_billing: Optional[float] = None,
        audience_vs_billing: Optional[float] = None,
        peer_group: Optional[str] = None,
        peer_comparison: Optional[Dict[str, Any]] = None,
        market_position: Optional[str] = None,
        value_model_version: Optional[str] = None,
        scoring_method: Optional[str] = None,
        confidence: Optional[float] = None,
        quality_flags: Optional[Dict[str, Any]] = None,
        feature_date: Optional[date] = None,
        source_as_of: Optional[datetime] = None,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        knowledge_time: Optional[datetime] = None,
        source_system: Optional[str] = None,
        festival_key: Optional[str] = None,
        edition_key: Optional[str] = None,
        edition_year: Optional[int] = None,
        evidence_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert relative value calculation."""
        value_key = f"{artist_key}::{edition_key if edition_key else 'general'}::{feature_date.isoformat() if feature_date else 'latest'}"
        self.conn.execute(
            """
            INSERT INTO metrics.relative_value
                (value_key, artist_key, festival_key, edition_key, edition_year,
                 relative_value_score, value_category, value_percentile,
                 current_billing_tier, expected_billing_tier, billing_gap,
                 momentum_vs_billing, audience_vs_billing, peer_group, peer_comparison,
                 market_position, value_model_version, scoring_method, confidence,
                 quality_flags, feature_date, source_as_of, calculated_at,
                 valid_from, valid_to, knowledge_time, source_system, evidence_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (value_key) DO UPDATE SET
                relative_value_score = excluded.relative_value_score,
                value_category = excluded.value_category,
                value_percentile = excluded.value_percentile,
                current_billing_tier = excluded.current_billing_tier,
                expected_billing_tier = excluded.expected_billing_tier,
                billing_gap = excluded.billing_gap,
                momentum_vs_billing = excluded.momentum_vs_billing,
                audience_vs_billing = excluded.audience_vs_billing,
                peer_group = excluded.peer_group,
                peer_comparison = excluded.peer_comparison,
                market_position = excluded.market_position,
                value_model_version = excluded.value_model_version,
                scoring_method = excluded.scoring_method,
                confidence = excluded.confidence,
                quality_flags = excluded.quality_flags,
                feature_date = excluded.feature_date,
                source_as_of = excluded.source_as_of,
                calculated_at = excluded.calculated_at,
                valid_from = excluded.valid_from,
                valid_to = excluded.valid_to,
                knowledge_time = excluded.knowledge_time,
                source_system = excluded.source_system,
                evidence_json = excluded.evidence_json,
                ingested_at = excluded.ingested_at
            """,
            [
                value_key,
                artist_key,
                festival_key,
                edition_key,
                edition_year,
                relative_value_score,
                value_category,
                value_percentile,
                current_billing_tier,
                expected_billing_tier,
                billing_gap,
                momentum_vs_billing,
                audience_vs_billing,
                peer_group,
                json.dumps(peer_comparison or {}),
                market_position,
                value_model_version,
                scoring_method,
                confidence,
                json.dumps(quality_flags or {}),
                feature_date.isoformat() if feature_date else None,
                source_as_of.isoformat() if source_as_of else None,
                datetime.utcnow(),
                valid_from.isoformat() if valid_from else None,
                valid_to.isoformat() if valid_to else None,
                knowledge_time.isoformat() if knowledge_time else None,
                source_system,
                json.dumps(evidence_json or {}),
                datetime.utcnow(),
            ],
        )

    # -- write: festival portfolio --------------------------------------------- #
    def insert_festival_portfolio(
        self,
        festival_key: str,
        edition_key: str,
        edition_year: int,
        total_artists: int,
        headliner_count: int,
        sub_headliner_count: int,
        supporting_count: int,
        early_day_count: int,
        portfolio_momentum_avg: float,
        portfolio_momentum_median: float,
        portfolio_risk_avg: float,
        portfolio_value_avg: float,
        portfolio_diversity_score: float,
        total_budget: Optional[float] = None,
        headliner_budget: Optional[float] = None,
        supporting_budget: Optional[float] = None,
        budget_utilization: Optional[float] = None,
        cost_per_momentum: Optional[float] = None,
        cost_per_attendance: Optional[float] = None,
        roi_score: Optional[float] = None,
        efficiency_score: Optional[float] = None,
        portfolio_version: Optional[str] = None,
        optimization_method: Optional[str] = None,
        confidence: Optional[float] = None,
        quality_flags: Optional[Dict[str, Any]] = None,
        feature_date: Optional[date] = None,
        source_as_of: Optional[datetime] = None,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        knowledge_time: Optional[datetime] = None,
        source_system: Optional[str] = None,
        evidence_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert festival portfolio analytics."""
        portfolio_key = f"{festival_key}::{edition_key}::{edition_year}"
        self.conn.execute(
            """
            INSERT INTO metrics.festival_portfolio
                (portfolio_key, festival_key, edition_key, edition_year,
                 total_artists, headliner_count, sub_headliner_count, supporting_count, early_day_count,
                 portfolio_momentum_avg, portfolio_momentum_median, portfolio_risk_avg,
                 portfolio_value_avg, portfolio_diversity_score, total_budget, headliner_budget,
                 supporting_budget, budget_utilization, cost_per_momentum, cost_per_attendance,
                 roi_score, efficiency_score, portfolio_version, optimization_method,
                 confidence, quality_flags, feature_date, source_as_of, calculated_at,
                 valid_from, valid_to, knowledge_time, source_system, evidence_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (portfolio_key) DO UPDATE SET
                total_artists = excluded.total_artists,
                headliner_count = excluded.headliner_count,
                sub_headliner_count = excluded.sub_headliner_count,
                supporting_count = excluded.supporting_count,
                early_day_count = excluded.early_day_count,
                portfolio_momentum_avg = excluded.portfolio_momentum_avg,
                portfolio_momentum_median = excluded.portfolio_momentum_median,
                portfolio_risk_avg = excluded.portfolio_risk_avg,
                portfolio_value_avg = excluded.portfolio_value_avg,
                portfolio_diversity_score = excluded.portfolio_diversity_score,
                total_budget = excluded.total_budget,
                headliner_budget = excluded.headliner_budget,
                supporting_budget = excluded.supporting_budget,
                budget_utilization = excluded.budget_utilization,
                cost_per_momentum = excluded.cost_per_momentum,
                cost_per_attendance = excluded.cost_per_attendance,
                roi_score = excluded.roi_score,
                efficiency_score = excluded.efficiency_score,
                portfolio_version = excluded.portfolio_version,
                optimization_method = excluded.optimization_method,
                confidence = excluded.confidence,
                quality_flags = excluded.quality_flags,
                feature_date = excluded.feature_date,
                source_as_of = excluded.source_as_of,
                calculated_at = excluded.calculated_at,
                valid_from = excluded.valid_from,
                valid_to = excluded.valid_to,
                knowledge_time = excluded.knowledge_time,
                source_system = excluded.source_system,
                evidence_json = excluded.evidence_json,
                ingested_at = excluded.ingested_at
            """,
            [
                portfolio_key,
                festival_key,
                edition_key,
                edition_year,
                total_artists,
                headliner_count,
                sub_headliner_count,
                supporting_count,
                early_day_count,
                portfolio_momentum_avg,
                portfolio_momentum_median,
                portfolio_risk_avg,
                portfolio_value_avg,
                portfolio_diversity_score,
                total_budget,
                headliner_budget,
                supporting_budget,
                budget_utilization,
                cost_per_momentum,
                cost_per_attendance,
                roi_score,
                efficiency_score,
                portfolio_version,
                optimization_method,
                confidence,
                json.dumps(quality_flags or {}),
                feature_date.isoformat() if feature_date else None,
                source_as_of.isoformat() if source_as_of else None,
                datetime.utcnow(),
                valid_from.isoformat() if valid_from else None,
                valid_to.isoformat() if valid_to else None,
                knowledge_time.isoformat() if knowledge_time else None,
                source_system,
                json.dumps(evidence_json or {}),
                datetime.utcnow(),
            ],
        )

    # -- write: sentiment + social signals ----------------------------------- #
    def upsert_sentiment(self, artist_key: str, insight) -> None:
        """Persist an :class:`ArtistInsight` sentiment record (idempotent)."""
        s = insight.sentiment
        self.conn.execute(
            """
            INSERT OR REPLACE INTO metrics.artist_sentiment (
                artist_key, sentiment_label, compound, positive, neutral, negative,
                sample_size, mention_volume, attention_score, top_topics,
                top_positive, top_negative, llm_summary, sources_used, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                artist_key,
                insight.sentiment_label,
                float(s.compound),
                float(s.positive),
                float(s.neu),
                float(s.negative),
                int(s.sample_size),
                int(insight.mention_volume),
                float(insight.attention_score),
                json.dumps(insight.top_topics),
                json.dumps(s.top_positive),
                json.dumps(s.top_negative),
                insight.llm_summary,
                json.dumps(insight.sources_used),
                datetime.utcnow(),
            ],
        )

    def insert_social_signal(self, artist_key: str, source: str,
                             mention_count: int, points: float = 0.0,
                             comments: float = 0.0, pageviews_30d: float = 0.0,
                             news_mentions: float = 0.0) -> None:
        key = f"{artist_key}::{source}"
        self.conn.execute(
            """
            INSERT OR REPLACE INTO metrics.social_signals (
                signal_key, artist_key, source_system, mention_count,
                points, "comments", pageviews_30d, news_mentions, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [key, artist_key, source, int(mention_count), float(points),
             float(comments), float(pageviews_30d), float(news_mentions),
             datetime.utcnow()],
        )

    # -- read: festivals ----------------------------------------------------- #
    def list_festivals(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT festival_key, name, location_country, location_city, capacity, "
            "genre_focus, festival_type, duration_days, typical_month, source_system "
            "FROM core.festivals ORDER BY name"
        ).fetchall()
        cols = ["festival_key", "name", "location_country", "location_city", "capacity",
                "genre_focus", "festival_type", "duration_days", "typical_month", "source_system"]
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["genre_focus"] = self._coerce_json(d["genre_focus"])
            out.append(d)
        return out

    def get_festival(self, festival_key: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT festival_key, name, location_country, location_city, location_region, "
            "capacity, genre_focus, festival_type, venue_type, duration_days, "
            "typical_month, source_system FROM core.festivals WHERE festival_key = ?",
            [festival_key],
        ).fetchone()
        if not row:
            return None
        cols = ["festival_key", "name", "location_country", "location_city", "location_region",
                "capacity", "genre_focus", "festival_type",
                "venue_type", "duration_days", "typical_month", "source_system"]
        d = dict(zip(cols, row))
        d["genre_focus"] = self._coerce_json(d["genre_focus"])
        return d

    # -- read: artists ------------------------------------------------------- #
    def search_artists(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        like = f"%{query.lower()}%"
        rows = self.conn.execute(
            "SELECT artist_key, musicbrainz_id, name, normalized_name, country, genres, "
            "type FROM core.artists WHERE lower(normalized_name) LIKE ? "
            "ORDER BY name LIMIT ?",
            [like, limit],
        ).fetchall()
        cols = ["artist_key", "musicbrainz_id", "name", "normalized_name", "country",
                "genres", "type"]
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["genres"] = self._coerce_json(d["genres"])
            out.append(d)
        return out

    def get_artist(self, artist_key: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT artist_key, musicbrainz_id, name, normalized_name, country, "
            "genres, type, life_span_begin, life_span_end, source_system "
            "FROM core.artists WHERE artist_key = ?",
            [artist_key],
        ).fetchone()
        if not row:
            return None
        d = dict(zip(["artist_key", "musicbrainz_id", "name", "normalized_name", "country",
                      "genres", "type", "life_span_begin", "life_span_end", "source_system"], row))
        d["genres"] = self._coerce_json(d["genres"])
        return d

    def get_artist_metrics(self, artist_key: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT source_system, metric_type, value, observed_date, fetched_at, meta_data "
            "FROM metrics.artist_metrics WHERE artist_key = ? ORDER BY source_system, metric_type",
            [artist_key],
        ).fetchall()
        cols = ["source_system", "metric_type", "value", "observed_date", "fetched_at", "meta_data"]
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["meta_data"] = self._coerce_json(d["meta_data"])
            out.append(d)
        return out

    # -- read: point-in-time features ----------------------------------------- #
    def get_artist_features(
        self,
        artist_key: str,
        feature_date: Optional[date] = None,
        knowledge_time: Optional[datetime] = None,
        feature_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve point-in-time features for an artist."""
        query = """
            SELECT feature_name, feature_type, feature_value, feature_category,
                   feature_date, source_publication_time, source_as_of, retrieved_at,
                   valid_from, valid_to, knowledge_time, calculated_at,
                   feature_version, model_version, formula, input_features,
                   confidence, quality_flags, source_system, source_url, source_record_id,
                   license_class, commercial_use_status, evidence_json
            FROM metrics.artist_feature_store
            WHERE artist_key = ?
        """
        params = [artist_key]
        
        if feature_date:
            query += " AND feature_date = ?"
            params.append(feature_date.isoformat())
        
        if knowledge_time:
            query += " AND knowledge_time <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([knowledge_time.isoformat(), knowledge_time.isoformat()])
        
        if feature_names:
            placeholders = ",".join(["?"] * len(feature_names))
            query += f" AND feature_name IN ({placeholders})"
            params.extend(feature_names)
        
        query += " ORDER BY feature_name"
        
        rows = self.conn.execute(query, params).fetchall()
        cols = ["feature_name", "feature_type", "feature_value", "feature_category",
                "feature_date", "source_publication_time", "source_as_of", "retrieved_at",
                "valid_from", "valid_to", "knowledge_time", "calculated_at",
                "feature_version", "model_version", "formula", "input_features",
                "confidence", "quality_flags", "source_system", "source_url", "source_record_id",
                "license_class", "commercial_use_status", "evidence_json"]
        
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            for json_field in ["input_features", "quality_flags", "evidence_json"]:
                d[json_field] = self._coerce_json(d[json_field])
            out.append(d)
        return out

    def get_artist_factors(
        self,
        artist_key: str,
        edition_key: Optional[str] = None,
        knowledge_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve artist factor scores."""
        query = """
            SELECT momentum_score, relevance_score, audience_fit_score, value_proposition_score,
                   booking_complexity_score, risk_score, momentum_components, relevance_components,
                   audience_components, value_components, complexity_components, risk_components,
                   factor_model_version, scoring_method, confidence, quality_flags,
                   feature_date, source_as_of, calculated_at, valid_from, valid_to, knowledge_time,
                   source_system, evidence_json
            FROM metrics.artist_factors
            WHERE artist_key = ?
        """
        params = [artist_key]
        
        if edition_key:
            query += " AND edition_key = ?"
            params.append(edition_key)
        
        if knowledge_time:
            query += " AND knowledge_time <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([knowledge_time.isoformat(), knowledge_time.isoformat()])
        
        query += " ORDER BY knowledge_time DESC LIMIT 1"
        
        row = self.conn.execute(query, params).fetchone()
        if not row:
            return None
        
        cols = ["momentum_score", "relevance_score", "audience_fit_score", "value_proposition_score",
                "booking_complexity_score", "risk_score", "momentum_components", "relevance_components",
                "audience_components", "value_components", "complexity_components", "risk_components",
                "factor_model_version", "scoring_method", "confidence", "quality_flags",
                "feature_date", "source_as_of", "calculated_at", "valid_from", "valid_to", "knowledge_time",
                "source_system", "evidence_json"]
        
        d = dict(zip(cols, row))
        for json_field in ["momentum_components", "relevance_components", "audience_components",
                          "value_components", "complexity_components", "risk_components",
                          "quality_flags", "evidence_json"]:
            d[json_field] = self._coerce_json(d[json_field])
        return d

    def get_expected_billing(
        self,
        artist_key: str,
        edition_key: Optional[str] = None,
        knowledge_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve expected billing prediction."""
        query = """
            SELECT expected_billing_tier, expected_billing_order, billing_confidence,
                   booking_probability, expected_day, expected_stage, billing_reasoning,
                   billing_factors, model_version, training_period, confidence, quality_flags,
                   feature_date, source_as_of, calculated_at, valid_from, valid_to, knowledge_time,
                   source_system, evidence_json
            FROM metrics.expected_billing
            WHERE artist_key = ?
        """
        params = [artist_key]
        
        if edition_key:
            query += " AND edition_key = ?"
            params.append(edition_key)
        
        if knowledge_time:
            query += " AND knowledge_time <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([knowledge_time.isoformat(), knowledge_time.isoformat()])
        
        query += " ORDER BY knowledge_time DESC LIMIT 1"
        
        row = self.conn.execute(query, params).fetchone()
        if not row:
            return None
        
        cols = ["expected_billing_tier", "expected_billing_order", "billing_confidence",
                "booking_probability", "expected_day", "expected_stage", "billing_reasoning",
                "billing_factors", "model_version", "training_period", "confidence", "quality_flags",
                "feature_date", "source_as_of", "calculated_at", "valid_from", "valid_to", "knowledge_time",
                "source_system", "evidence_json"]
        
        d = dict(zip(cols, row))
        for json_field in ["billing_factors", "quality_flags", "evidence_json"]:
            d[json_field] = self._coerce_json(d[json_field])
        return d

    def get_relative_value(
        self,
        artist_key: str,
        edition_key: Optional[str] = None,
        knowledge_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve relative value calculation."""
        query = """
            SELECT relative_value_score, value_category, value_percentile,
                   current_billing_tier, expected_billing_tier, billing_gap,
                   momentum_vs_billing, audience_vs_billing, peer_group, peer_comparison,
                   market_position, value_model_version, scoring_method, confidence,
                   quality_flags, feature_date, source_as_of, calculated_at,
                   valid_from, valid_to, knowledge_time, source_system, evidence_json
            FROM metrics.relative_value
            WHERE artist_key = ?
        """
        params = [artist_key]
        
        if edition_key:
            query += " AND edition_key = ?"
            params.append(edition_key)
        
        if knowledge_time:
            query += " AND knowledge_time <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([knowledge_time.isoformat(), knowledge_time.isoformat()])
        
        query += " ORDER BY knowledge_time DESC LIMIT 1"
        
        row = self.conn.execute(query, params).fetchone()
        if not row:
            return None
        
        cols = ["relative_value_score", "value_category", "value_percentile",
                "current_billing_tier", "expected_billing_tier", "billing_gap",
                "momentum_vs_billing", "audience_vs_billing", "peer_group", "peer_comparison",
                "market_position", "value_model_version", "scoring_method", "confidence",
                "quality_flags", "feature_date", "source_as_of", "calculated_at",
                "valid_from", "valid_to", "knowledge_time", "source_system", "evidence_json"]
        
        d = dict(zip(cols, row))
        for json_field in ["peer_comparison", "quality_flags", "evidence_json"]:
            d[json_field] = self._coerce_json(d[json_field])
        return d

    def get_festival_portfolio(
        self,
        festival_key: str,
        edition_key: str,
        knowledge_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve festival portfolio analytics."""
        query = """
            SELECT total_artists, headliner_count, sub_headliner_count, supporting_count, early_day_count,
                   portfolio_momentum_avg, portfolio_momentum_median, portfolio_risk_avg,
                   portfolio_value_avg, portfolio_diversity_score, total_budget, headliner_budget,
                   supporting_budget, budget_utilization, cost_per_momentum, cost_per_attendance,
                   roi_score, efficiency_score, portfolio_version, optimization_method,
                   confidence, quality_flags, feature_date, source_as_of, calculated_at,
                   valid_from, valid_to, knowledge_time, source_system, evidence_json
            FROM metrics.festival_portfolio
            WHERE festival_key = ? AND edition_key = ?
        """
        params = [festival_key, edition_key]
        
        if knowledge_time:
            query += " AND knowledge_time <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([knowledge_time.isoformat(), knowledge_time.isoformat()])
        
        query += " ORDER BY knowledge_time DESC LIMIT 1"
        
        row = self.conn.execute(query, params).fetchone()
        if not row:
            return None
        
        cols = ["total_artists", "headliner_count", "sub_headliner_count", "supporting_count", "early_day_count",
                "portfolio_momentum_avg", "portfolio_momentum_median", "portfolio_risk_avg",
                "portfolio_value_avg", "portfolio_diversity_score", "total_budget", "headliner_budget",
                "supporting_budget", "budget_utilization", "cost_per_momentum", "cost_per_attendance",
                "roi_score", "efficiency_score", "portfolio_version", "optimization_method",
                "confidence", "quality_flags", "feature_date", "source_as_of", "calculated_at",
                "valid_from", "valid_to", "knowledge_time", "source_system", "evidence_json"]
        
        d = dict(zip(cols, row))
        for json_field in ["quality_flags", "evidence_json"]:
            d[json_field] = self._coerce_json(d[json_field])
        return d

    def count_artists(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM core.artists"
        ).fetchone()[0]

    def count_festivals(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM core.festivals"
        ).fetchone()[0]

    # -- read: sentiment ------------------------------------------------------ #
    def get_artist_sentiment(self, artist_key: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT artist_key, sentiment_label, compound, positive, neutral, negative, "
            "sample_size, mention_volume, attention_score, top_topics, top_positive, "
            "top_negative, llm_summary, sources_used, generated_at "
            "FROM metrics.artist_sentiment WHERE artist_key = ?",
            [artist_key],
        ).fetchone()
        if not row:
            return None
        cols = ["artist_key", "sentiment_label", "compound", "positive", "neutral",
                "negative", "sample_size", "mention_volume", "attention_score",
                "top_topics", "top_positive", "top_negative", "llm_summary",
                "sources_used", "generated_at"]
        d = dict(zip(cols, row))
        for k in ("top_topics", "top_positive", "top_negative", "sources_used"):
            d[k] = self._coerce_json(d[k])
        return d

    def get_social_signals(self, artist_key: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT source_system, mention_count, points, comments, pageviews_30d, "
            "news_mentions FROM metrics.social_signals WHERE artist_key = ? "
            "ORDER BY source_system",
            [artist_key],
        ).fetchall()
        cols = ["source_system", "mention_count", "points", "comments",
                "pageviews_30d", "news_mentions"]
        return [dict(zip(cols, r)) for r in rows]

    def list_sentiment_ranked(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Artists ranked by attention_score (for market overview)."""
        rows = self.conn.execute(
            "SELECT artist_key, sentiment_label, compound, attention_score, "
            "mention_volume FROM metrics.artist_sentiment "
            "ORDER BY attention_score DESC LIMIT ?",
            [limit],
        ).fetchall()
        cols = ["artist_key", "sentiment_label", "compound",
                "attention_score", "mention_volume"]
        return [dict(zip(cols, r)) for r in rows]

    # -- helpers ------------------------------------------------------------- #
    @staticmethod
    def _coerce_json(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (list, dict)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return value
        return value


# --------------------------------------------------------------------------- #
# Module-level singleton helpers (used by the API and tests)
# --------------------------------------------------------------------------- #
_DEFAULT_REPO: Optional[FestivalRepository] = None


def get_repository(db_path: Optional[str] = None, read_only: bool = False) -> FestivalRepository:
    """Return a process-wide repository singleton.

    Defaults to read-only when the warehouse file already exists, so the API
    can serve while an ingestion process holds the writer lock (DuckDB allows
    one writer + many concurrent readers).
    """
    global _DEFAULT_REPO
    if _DEFAULT_REPO is None:
        path = db_path or DEFAULT_DB_PATH
        # If the file exists, serve read-only to avoid lock contention with a
        # concurrent ingestion writer. If it doesn't exist yet, open writable
        # so the schema is created.
        ro = read_only or os.path.exists(path)
        _DEFAULT_REPO = FestivalRepository(path, read_only=ro)
    return _DEFAULT_REPO


def reset_repository() -> None:
    global _DEFAULT_REPO
    if _DEFAULT_REPO is not None:
        _DEFAULT_REPO.close()
    _DEFAULT_REPO = None
