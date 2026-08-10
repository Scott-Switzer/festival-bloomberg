"""
Warehouse repository layer for Festival Bloomberg.

This is the single read/write contract between the ingestor and the API.
It is backed by DuckDB (``warehouse/duckdb_manager.py``), which needs no
external server, so the entire platform runs zero-infra (free, open-source).

Storage layout (matches the Festival Bloomberg spec schemas):
    core.artists        - canonical artist dimension (MBID-resolved)
    core.festivals      - canonical festival dimension
    core.festival_editions - (festival, year) occurrence
    core.lineup_slots   - resolved per-artist performance slots
    metrics.artist_metrics - per-source momentum/attention observations
    metrics.artist_attention_observations - Wikimedia/Spotify attention inputs
    metrics.edition_analytical_metrics - derived Festival-Bloomberg analytics
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
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
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
    """Read/write access to the Festival Bloomberg warehouse.

    A single instance owns one DuckDB connection. Use as a context manager or
    call :meth:`close` explicitly.
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

    def insert_artist_metric(
        self,
        artist_key: str,
        source_system: str,
        metric_type: str,
        value: Optional[float],
        observed_date: Optional[date] = None,
        meta_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        metric_key = f"{artist_key}::{source_system}::{metric_type}"
        self.conn.execute(
            """
            INSERT INTO metrics.artist_metrics
                (metric_key, artist_key, source_system, metric_type, value,
                 observed_date, fetched_at, meta_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (metric_key) DO UPDATE SET
                value = excluded.value,
                observed_date = excluded.observed_date,
                fetched_at = excluded.fetched_at,
                meta_data = excluded.meta_data
            """,
            [
                metric_key,
                artist_key,
                source_system,
                metric_type,
                value,
                observed_date.isoformat() if observed_date else None,
                datetime.utcnow(),
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
    ) -> None:
        import hashlib

        raw = json.dumps(observed_raw or {}, sort_keys=True)
        obs_key = hashlib.sha256(
            f"{festival_key}|{edition_year}|{artist_name}|{position}".encode()
        ).hexdigest()[:16]
        self.conn.execute(
            """
            INSERT INTO raw.lineup_observations
                (observation_key, festival_key, edition_year, artist_name, position,
                 stage, day, source_url, parser_version, observed_raw, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                parser_version,
                raw,
                datetime.utcnow(),
            ],
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
                "capacity", "genre_focus", *(["genre_focus"] if False else []), "festival_type",
                "venue_type", "duration_days", "typical_month", "source_system"]
        d = dict(zip(["festival_key", "name", "location_country", "location_city",
                      "location_region", "capacity", "genre_focus", "festival_type",
                      "venue_type", "duration_days", "typical_month", "source_system"], row))
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

    def count_artists(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM core.artists"
        ).fetchone()[0]

    def count_festivals(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM core.festivals"
        ).fetchone()[0]

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
                float(s.neutral),
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
                points, comments, pageviews_30d, news_mentions, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [key, artist_key, source, int(mention_count), float(points),
             float(comments), float(pageviews_30d), float(news_mentions),
             datetime.utcnow()],
        )

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

    # -- write: intelligence metrics ---------------------------------------- #
    def upsert_attention_observation(self, row: Dict[str, Any]) -> str:
        """Idempotent upsert into metrics.artist_attention_observations."""
        observation_key = row["observation_key"]
        self.conn.execute(
            """
            INSERT INTO metrics.artist_attention_observations (
                observation_key, artist_key, festival_key, edition_key, edition_year,
                source_system, metric_kind, project, access_method, agent, article_title,
                granularity, period_start, period_end, value, value_sum, value_unit,
                status, error_code, error_message, source_url, retrieved_at,
                raw_response_json, provenance_json, metric_version, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (observation_key) DO UPDATE SET
                festival_key = excluded.festival_key,
                edition_key = excluded.edition_key,
                edition_year = excluded.edition_year,
                value = excluded.value,
                value_sum = excluded.value_sum,
                value_unit = excluded.value_unit,
                status = excluded.status,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                source_url = excluded.source_url,
                retrieved_at = excluded.retrieved_at,
                raw_response_json = excluded.raw_response_json,
                provenance_json = excluded.provenance_json,
                metric_version = excluded.metric_version,
                ingested_at = excluded.ingested_at
            """,
            [
                observation_key,
                row["artist_key"],
                row.get("festival_key"),
                row.get("edition_key"),
                row.get("edition_year"),
                row["source_system"],
                row["metric_kind"],
                row.get("project"),
                row.get("access_method"),
                row.get("agent"),
                row.get("article_title"),
                row.get("granularity"),
                row.get("period_start"),
                row.get("period_end"),
                row.get("value"),
                row.get("value_sum"),
                row.get("value_unit"),
                row["status"],
                row.get("error_code"),
                row.get("error_message"),
                row["source_url"],
                row["retrieved_at"],
                json.dumps(row.get("raw_response_json"))
                if row.get("raw_response_json") is not None
                else None,
                json.dumps(row.get("provenance_json"))
                if row.get("provenance_json") is not None
                else None,
                row["metric_version"],
                row.get("ingested_at") or datetime.utcnow(),
            ],
        )
        return observation_key

    def upsert_edition_analytical_metrics(self, row: Dict[str, Any]) -> str:
        """Idempotent upsert into metrics.edition_analytical_metrics."""
        metric_key = row["metric_key"]
        self.conn.execute(
            """
            INSERT INTO metrics.edition_analytical_metrics (
                metric_key, festival_key, edition_key, edition_year, metric_version,
                attention_hhi, attention_share_json, attention_artist_count,
                attention_coverage_ratio, attention_missing_flag,
                billing_arbitrage_score, billing_arbitrage_spearman,
                billing_arbitrage_coverage_ratio, billing_arbitrage_missing_flag,
                promoter_shared_inventory_jaccard, promoter_comparison_edition_key,
                promoter_comparison_festival_key, promoter_comparison_year,
                promoter_jaccard_missing_flag,
                exclusivity_gap_km, exclusivity_conflict_count, exclusivity_radius_km,
                exclusivity_window_days, exclusivity_missing_flag,
                secondary_spread_abs, secondary_spread_pct, primary_price, secondary_price,
                primary_currency, secondary_currency, secondary_spread_missing_flag,
                input_hash, evidence_json, flags_json, computed_at, ingested_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (metric_key) DO UPDATE SET
                attention_hhi = excluded.attention_hhi,
                attention_share_json = excluded.attention_share_json,
                attention_artist_count = excluded.attention_artist_count,
                attention_coverage_ratio = excluded.attention_coverage_ratio,
                attention_missing_flag = excluded.attention_missing_flag,
                billing_arbitrage_score = excluded.billing_arbitrage_score,
                billing_arbitrage_spearman = excluded.billing_arbitrage_spearman,
                billing_arbitrage_coverage_ratio = excluded.billing_arbitrage_coverage_ratio,
                billing_arbitrage_missing_flag = excluded.billing_arbitrage_missing_flag,
                promoter_shared_inventory_jaccard = excluded.promoter_shared_inventory_jaccard,
                promoter_comparison_edition_key = excluded.promoter_comparison_edition_key,
                promoter_comparison_festival_key = excluded.promoter_comparison_festival_key,
                promoter_comparison_year = excluded.promoter_comparison_year,
                promoter_jaccard_missing_flag = excluded.promoter_jaccard_missing_flag,
                exclusivity_gap_km = excluded.exclusivity_gap_km,
                exclusivity_conflict_count = excluded.exclusivity_conflict_count,
                exclusivity_radius_km = excluded.exclusivity_radius_km,
                exclusivity_window_days = excluded.exclusivity_window_days,
                exclusivity_missing_flag = excluded.exclusivity_missing_flag,
                secondary_spread_abs = excluded.secondary_spread_abs,
                secondary_spread_pct = excluded.secondary_spread_pct,
                primary_price = excluded.primary_price,
                secondary_price = excluded.secondary_price,
                primary_currency = excluded.primary_currency,
                secondary_currency = excluded.secondary_currency,
                secondary_spread_missing_flag = excluded.secondary_spread_missing_flag,
                input_hash = excluded.input_hash,
                evidence_json = excluded.evidence_json,
                flags_json = excluded.flags_json,
                computed_at = excluded.computed_at,
                ingested_at = excluded.ingested_at
            """,
            [
                metric_key,
                row["festival_key"],
                row["edition_key"],
                row["edition_year"],
                row["metric_version"],
                row.get("attention_hhi"),
                json.dumps(row.get("attention_share_json"))
                if row.get("attention_share_json") is not None
                else None,
                row.get("attention_artist_count"),
                row.get("attention_coverage_ratio"),
                row.get("attention_missing_flag"),
                row.get("billing_arbitrage_score"),
                row.get("billing_arbitrage_spearman"),
                row.get("billing_arbitrage_coverage_ratio"),
                row.get("billing_arbitrage_missing_flag"),
                row.get("promoter_shared_inventory_jaccard"),
                row.get("promoter_comparison_edition_key"),
                row.get("promoter_comparison_festival_key"),
                row.get("promoter_comparison_year"),
                row.get("promoter_jaccard_missing_flag"),
                row.get("exclusivity_gap_km"),
                row.get("exclusivity_conflict_count"),
                row.get("exclusivity_radius_km"),
                row.get("exclusivity_window_days"),
                row.get("exclusivity_missing_flag"),
                row.get("secondary_spread_abs"),
                row.get("secondary_spread_pct"),
                row.get("primary_price"),
                row.get("secondary_price"),
                row.get("primary_currency"),
                row.get("secondary_currency"),
                row.get("secondary_spread_missing_flag"),
                row.get("input_hash"),
                json.dumps(row.get("evidence_json") or {}),
                json.dumps(row.get("flags_json") or {}),
                row.get("computed_at") or datetime.utcnow(),
                row.get("ingested_at") or datetime.utcnow(),
            ],
        )
        return metric_key

    def get_edition_analytical_metrics(
        self, edition_key: str, metric_version: str
    ) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT metric_key, festival_key, edition_key, edition_year, metric_version,
                   attention_hhi, attention_share_json, attention_artist_count,
                   attention_coverage_ratio, attention_missing_flag,
                   billing_arbitrage_score, billing_arbitrage_spearman,
                   billing_arbitrage_coverage_ratio, billing_arbitrage_missing_flag,
                   promoter_shared_inventory_jaccard, promoter_comparison_edition_key,
                   promoter_jaccard_missing_flag,
                   exclusivity_gap_km, exclusivity_conflict_count, exclusivity_missing_flag,
                   secondary_spread_abs, secondary_spread_pct, primary_price, secondary_price,
                   primary_currency, secondary_currency, secondary_spread_missing_flag,
                   input_hash, evidence_json, flags_json, computed_at
            FROM metrics.edition_analytical_metrics
            WHERE edition_key = ? AND metric_version = ?
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            [edition_key, metric_version],
        ).fetchone()
        if not row:
            return None
        cols = [
            "metric_key", "festival_key", "edition_key", "edition_year", "metric_version",
            "attention_hhi", "attention_share_json", "attention_artist_count",
            "attention_coverage_ratio", "attention_missing_flag",
            "billing_arbitrage_score", "billing_arbitrage_spearman",
            "billing_arbitrage_coverage_ratio", "billing_arbitrage_missing_flag",
            "promoter_shared_inventory_jaccard", "promoter_comparison_edition_key",
            "promoter_jaccard_missing_flag",
            "exclusivity_gap_km", "exclusivity_conflict_count", "exclusivity_missing_flag",
            "secondary_spread_abs", "secondary_spread_pct", "primary_price", "secondary_price",
            "primary_currency", "secondary_currency", "secondary_spread_missing_flag",
            "input_hash", "evidence_json", "flags_json", "computed_at",
        ]
        d = dict(zip(cols, row))
        for key in ("attention_share_json", "evidence_json", "flags_json"):
            d[key] = self._coerce_json(d[key])
        return d

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
