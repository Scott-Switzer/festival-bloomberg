"""
Warehouse repository layer for Festival Bloomberg.

This is the single read/write contract between the ingestor and the API.
It is backed by DuckDB (``warehouse/duckdb_manager.py``), which needs no
external server, so the entire platform runs zero-infra (free, open-source).

Storage layout (matches the Festival Bloomberg spec schemas):
    core.artists        - canonical artist dimension (MBID-resolved)
    core.festivals      - canonical festival dimension
    core.festival_editions - (festival, year) occurrence
    metrics.artist_metrics - per-source momentum/attention observations
    raw.lineup_observations - raw lineup scrape/parse records
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
_SCHEMA_DDL = [
    # Canonical artist dimension. musicbrainz_id is the natural key.
    """
    CREATE TABLE IF NOT EXISTS core.artists (
        artist_key          VARCHAR PRIMARY KEY,
        musicbrainz_id      VARCHAR UNIQUE,
        name                VARCHAR NOT NULL,
        normalized_name     VARCHAR NOT NULL,
        disambiguation      VARCHAR,
        country             VARCHAR,
        genres              JSON,
        type                VARCHAR,
        life_span_begin     VARCHAR,
        life_span_end       VARCHAR,
        source_system       VARCHAR,
        ingested_at         TIMESTAMP
    )
    """,
    # Canonical festival dimension.
    """
    CREATE TABLE IF NOT EXISTS core.festivals (
        festival_key        VARCHAR PRIMARY KEY,
        name                VARCHAR NOT NULL,
        normalized_name     VARCHAR NOT NULL,
        location_country    VARCHAR,
        location_city       VARCHAR,
        location_region     VARCHAR,
        capacity            INTEGER,
        genre_focus         JSON,
        festival_type       VARCHAR,
        venue_type          VARCHAR,
        duration_days       INTEGER,
        typical_month       INTEGER,
        source_system       VARCHAR,
        ingested_at         TIMESTAMP
    )
    """,
    # Festival occurrence (festival + year).
    """
    CREATE TABLE IF NOT EXISTS core.festival_editions (
        edition_key         VARCHAR PRIMARY KEY,
        festival_key        VARCHAR NOT NULL,
        year                INTEGER NOT NULL,
        start_date          DATE,
        end_date            DATE,
        attendance          INTEGER,
        headliner_count     INTEGER,
        total_artists       INTEGER,
        source_system       VARCHAR,
        ingested_at         TIMESTAMP
    )
    """,
    # Per-source momentum / attention observations (point-in-time safe).
    """
    CREATE TABLE IF NOT EXISTS metrics.artist_metrics (
        metric_key          VARCHAR PRIMARY KEY,
        artist_key          VARCHAR NOT NULL,
        source_system       VARCHAR NOT NULL,
        metric_type         VARCHAR NOT NULL,
        value               DOUBLE,
        observed_date       DATE,
        fetched_at          TIMESTAMP,
        meta_data           JSON
    )
    """,
    # Raw lineup observation records (evidence-backed).
    """
    CREATE TABLE IF NOT EXISTS raw.lineup_observations (
        observation_key    VARCHAR PRIMARY KEY,
        festival_key        VARCHAR,
        edition_year        INTEGER,
        artist_name         VARCHAR NOT NULL,
        position            VARCHAR,
        stage               VARCHAR,
        day                 VARCHAR,
        source_url          VARCHAR,
        parser_version      VARCHAR,
        observed_raw        JSON,
        ingested_at         TIMESTAMP
    )
    """,
]


def _apply_schema(wh: DuckDBWarehouse) -> None:
    for ddl in _SCHEMA_DDL:
        wh.connection.execute(ddl)


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
