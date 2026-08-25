"""Common observation contract — the core data model for all external sources.

Every scraper, API, and external provider writes into ONE observation table.
No source-specific tables. No overwrites. Every observation is immutable.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Types ────────────────────────────────────────────────────────────

OBSERVATION_TYPES = {
    "EVENT_DISCOVERY",      # new event found
    "TICKET_PRICE",          # price observation (min, max, list)
    "TICKET_AVAILABILITY",   # sold-out, available, listing count
    "TICKET_LISTING",        # individual resale listing
    "EVENT_METADATA",        # venue, date, lineup, promoter
    "CAPACITY",              # venue capacity evidence
    "ARTIST_ATTENTION",      # social/listener metrics
    "MARKET_CONTEXT",        # competitive calendar, area info
}

CHANGE_TYPES = {
    "PRICE_CHANGED",
    "PRICE_DELTA",
    "LISTING_COUNT_CHANGED",
    "AVAILABLE_COUNT_CHANGED",
    "SOLD_OUT_STATE_CHANGED",
    "EVENT_ANNOUNCED",
    "EVENT_CANCELLED",
    "EVENT_RESCHEDULED",
    "VENUE_CHANGED",
    "LINEUP_CHANGED",
    "SUPPORT_ADDED",
    "TICKET_URL_CHANGED",
    "ONSALE_STARTED",
    "PROMOTER_CHANGED",
    "CAPACITY_CHANGED",
    "METADATA_CHANGED",
}

RIGHTS_DEFAULT = "TERMS_REVIEW_REQUIRED"
COMMERCIAL_DEFAULT = "PROTOTYPE_ONLY"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(data: Any, n: int = 64) -> str:
    """Content-addressable hash for integrity."""
    return hashlib.sha256(
        json.dumps(data, default=str, sort_keys=True).encode()
    ).hexdigest()[:n]


def _oid(source_platform: str, source_record_id: str, observation_type: str, retrieved_at: str) -> str:
    """Deterministic observation_id."""
    material = f"{source_platform}|{source_record_id}|{observation_type}|{retrieved_at}"
    return f"obs::{_hash(material, 16)}"


@dataclass
class ObservationRecord:
    """Normalized observation ready for ingestion."""
    source_platform: str
    acquisition_provider: str
    source_record_id: str
    observation_type: str
    raw_payload: dict[str, Any]
    
    # Optional canonical resolution
    artist_key: str | None = None
    venue_key: str | None = None
    event_key: str | None = None
    market_key: str | None = None
    
    # Temporal
    observed_at: str = field(default_factory=_now)
    retrieved_at: str | None = None
    source_publication_time: str | None = None
    announcement_time: str | None = None
    onsale_time: str | None = None
    event_time: str | None = None
    knowledge_time: str | None = None
    
    # Metadata
    actor_or_endpoint: str | None = None
    observation_category: str | None = None
    normalized_fields: dict[str, Any] | None = None
    parser_version: str | None = "evidence_rails_v1"
    rights_status: str = RIGHTS_DEFAULT
    commercial_use_status: str = COMMERCIAL_DEFAULT
    
    def to_row(self) -> dict[str, Any]:
        """Convert to database row dict."""
        rt = self.retrieved_at or self.observed_at
        oid = _oid(self.source_platform, self.source_record_id, self.observation_type, rt)
        
        return {
            "observation_id": oid,
            "source_platform": self.source_platform,
            "acquisition_provider": self.acquisition_provider,
            "actor_or_endpoint": self.actor_or_endpoint,
            "source_record_id": self.source_record_id,
            "artist_key": self.artist_key,
            "venue_key": self.venue_key,
            "event_key": self.event_key,
            "market_key": self.market_key,
            "observed_at": self.observed_at,
            "retrieved_at": rt,
            "source_publication_time": self.source_publication_time,
            "announcement_time": self.announcement_time,
            "onsale_time": self.onsale_time,
            "event_time": self.event_time,
            "knowledge_time": self.knowledge_time or rt,
            "observation_type": self.observation_type,
            "observation_category": self.observation_category,
            "raw_payload": json.dumps(self.raw_payload, default=str),
            "raw_payload_hash": _hash(self.raw_payload),
            "normalized_fields": json.dumps(self.normalized_fields or {}, default=str),
            "parser_version": self.parser_version,
            "rights_status": self.rights_status,
            "commercial_use_status": self.commercial_use_status,
            "software_version": self.parser_version,
        }


@dataclass
class ChangeRecord:
    """A detected change between two observations."""
    observation_id_current: str
    observation_id_previous: str
    source_record_id: str
    source_platform: str
    change_type: str
    change_category: str
    field_name: str
    
    value_previous: Any = None
    value_current: Any = None
    change_magnitude: float | None = None
    change_direction: str | None = None
    
    event_key: str | None = None
    observed_at: str = field(default_factory=_now)
    previous_observed_at: str | None = None
    hours_between: float | None = None

    def to_row(self) -> dict[str, Any]:
        cid = _hash(f"{self.observation_id_current}|{self.field_name}|{self.change_type}", 16)
        return {
            "change_id": f"chg::{cid}",
            "observation_id_current": self.observation_id_current,
            "observation_id_previous": self.observation_id_previous,
            "source_record_id": self.source_record_id,
            "source_platform": self.source_platform,
            "event_key": self.event_key,
            "change_type": self.change_type,
            "change_category": self.change_category,
            "field_name": self.field_name,
            "value_previous": json.dumps(self.value_previous, default=str) if self.value_previous is not None else None,
            "value_current": json.dumps(self.value_current, default=str) if self.value_current is not None else None,
            "change_magnitude": self.change_magnitude,
            "change_direction": self.change_direction,
            "observed_at": self.observed_at,
            "previous_observed_at": self.previous_observed_at,
            "hours_between_observations": self.hours_between,
        }


# ── Ingestion ────────────────────────────────────────────────────────

def ingest_observation(conn, obs: ObservationRecord) -> str:
    """Insert one observation. Returns observation_id."""
    row = obs.to_row()
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT OR IGNORE INTO acquisition.external_event_observations ({cols}) VALUES ({placeholders})",
        list(row.values()),
    )
    return row["observation_id"]


def ingest_observations_batch(conn, observations: list[ObservationRecord]) -> list[str]:
    """Insert many observations efficiently. Returns observation_ids."""
    ids = []
    for obs in observations:
        oid = ingest_observation(conn, obs)
        ids.append(oid)
    return ids


# ── Change Detection ─────────────────────────────────────────────────

def detect_changes(conn, source_platform: str) -> list[ChangeRecord]:
    """Detect changes between sequential observations of the same source record.
    
    Compares each observation's raw_payload to the previous one for the same
    (source_platform, source_record_id). Detects field-level changes.
    """
    changes: list[ChangeRecord] = []
    
    # Get pairs of (previous, current) observations
    rows = conn.execute("""
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY source_platform, source_record_id 
                ORDER BY observed_at
            ) AS rn
            FROM acquisition.external_event_observations
            WHERE source_platform = ?
        )
        SELECT 
            cur.observation_id AS cur_id,
            prev.observation_id AS prev_id,
            cur.source_record_id,
            cur.source_platform,
            cur.event_key,
            cur.raw_payload AS cur_raw,
            prev.raw_payload AS prev_raw,
            cur.observed_at AS cur_obs,
            prev.observed_at AS prev_obs
        FROM ranked cur
        JOIN ranked prev ON prev.source_record_id = cur.source_record_id 
            AND prev.rn = cur.rn - 1
        WHERE cur.source_platform = ?
    """, [source_platform, source_platform]).fetchall()
    
    for row in rows:
        cur_id, prev_id, src_id, platform, event_key, cur_raw, prev_raw, cur_obs, prev_obs = row
        
        try:
            cur_data = json.loads(cur_raw) if isinstance(cur_raw, str) else cur_raw
            prev_data = json.loads(prev_raw) if isinstance(prev_raw, str) else prev_raw
        except (json.JSONDecodeError, TypeError):
            continue
        
        if not isinstance(cur_data, dict) or not isinstance(prev_data, dict):
            continue
        
        # Compare fields
        for field_name in set(list(cur_data.keys()) + list(prev_data.keys())):
            cur_val = cur_data.get(field_name)
            prev_val = prev_data.get(field_name)
            
            if cur_val == prev_val:
                continue
            
            # Classify the change
            change_type, category = _classify_change(field_name, prev_val, cur_val)
            if change_type is None:
                continue
            
            magnitude = None
            direction = None
            if isinstance(cur_val, (int, float)) and isinstance(prev_val, (int, float)):
                magnitude = abs(float(cur_val) - float(prev_val))
                direction = "INCREASED" if float(cur_val) > float(prev_val) else "DECREASED"
            elif prev_val is None and cur_val is not None:
                direction = "ADDED"
                magnitude = 1
            elif prev_val is not None and cur_val is None:
                direction = "REMOVED"
                magnitude = 1
            
            # Hours between
            hours = None
            if cur_obs and prev_obs:
                try:
                    t_cur = datetime.fromisoformat(str(cur_obs).replace("Z", "+00:00"))
                    t_prev = datetime.fromisoformat(str(prev_obs).replace("Z", "+00:00"))
                    hours = (t_cur - t_prev).total_seconds() / 3600
                except (ValueError, TypeError):
                    pass
            
            changes.append(ChangeRecord(
                observation_id_current=cur_id,
                observation_id_previous=prev_id,
                source_record_id=src_id,
                source_platform=platform,
                change_type=change_type,
                change_category=category,
                field_name=field_name,
                value_previous=prev_val,
                value_current=cur_val,
                change_magnitude=magnitude,
                change_direction=direction,
                event_key=event_key,
                observed_at=str(cur_obs) if cur_obs else _now(),
                previous_observed_at=str(prev_obs) if prev_obs else None,
                hours_between=hours,
            ))
    
    # Persist
    for ch in changes:
        row = ch.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT OR IGNORE INTO acquisition.observation_changes ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
    
    return changes


def _classify_change(field_name: str, prev_val: Any, cur_val: Any) -> tuple[str | None, str | None]:
    """Classify what kind of change a field delta represents."""
    fn = field_name.lower()
    
    # Price changes
    if any(w in fn for w in ("price", "cost", "fee", "amount")):
        return ("PRICE_CHANGED", "TICKET_MARKET")
    
    # Sold out
    if any(w in fn for w in ("soldout", "sold_out", "soldOut", "isSoldOut")):
        prev_bool = bool(prev_val) if prev_val is not None else False
        cur_bool = bool(cur_val) if cur_val is not None else False
        if prev_bool != cur_bool:
            return ("SOLD_OUT_STATE_CHANGED", "TICKET_MARKET")
    
    # Listing / availability counts
    if any(w in fn for w in ("listing", "available", "inventory", "count")):
        return ("LISTING_COUNT_CHANGED", "TICKET_MARKET")
    
    # Venue changes
    if any(w in fn for w in ("venue", "location")):
        return ("VENUE_CHANGED", "EVENT_METADATA")
    
    # Lineup
    if any(w in fn for w in ("lineup", "artist", "performer")):
        return ("LINEUP_CHANGED", "EVENT_METADATA")
    
    # Promoter
    if any(w in fn for w in ("promoter", "organizer", "presented")):
        return ("PROMOTER_CHANGED", "EVENT_METADATA")
    
    # Status
    if any(w in fn for w in ("status", "cancelled", "rescheduled")):
        if str(cur_val).lower() in ("cancelled", "canceled"):
            return ("EVENT_CANCELLED", "EVENT_METADATA")
        if str(cur_val).lower() in ("rescheduled", "postponed"):
            return ("EVENT_RESCHEDULED", "EVENT_METADATA")
    
    # Generic metadata change
    return ("METADATA_CHANGED", "EVENT_METADATA")


# ── Coverage Tracking ────────────────────────────────────────────────

def compute_coverage(conn) -> dict[str, int]:
    """Compute information-advantage metrics across the entire estate."""
    metrics = {}
    
    # Total observations
    metrics["total_observations"] = conn.execute(
        "SELECT COUNT(*) FROM acquisition.external_event_observations"
    ).fetchone()[0]
    
    # Unique events with external coverage
    metrics["events_with_external_coverage"] = conn.execute(
        "SELECT COUNT(DISTINCT source_record_id) FROM acquisition.external_event_observations"
    ).fetchone()[0]
    
    # Sources per event
    row = conn.execute("""
        SELECT AVG(cnt) FROM (
            SELECT source_record_id, COUNT(DISTINCT source_platform) AS cnt
            FROM acquisition.external_event_observations
            GROUP BY source_record_id
        )
    """).fetchone()
    metrics["avg_sources_per_event"] = round(row[0], 2) if row and row[0] else 0
    
    # Events with price
    metrics["events_with_price"] = conn.execute("""
        SELECT COUNT(DISTINCT source_record_id) FROM acquisition.external_event_observations
        WHERE observation_type = 'TICKET_PRICE'
    """).fetchone()[0]
    
    # Events with multi-observation history
    metrics["events_with_history"] = conn.execute("""
        SELECT COUNT(DISTINCT source_record_id) FROM (
            SELECT source_record_id FROM acquisition.external_event_observations
            GROUP BY source_record_id HAVING COUNT(*) >= 2
        )
    """).fetchone()[0]
    
    # Change count
    metrics["total_changes_detected"] = conn.execute(
        "SELECT COUNT(*) FROM acquisition.observation_changes"
    ).fetchone()[0]
    
    # Platform breakdown
    platforms = conn.execute("""
        SELECT source_platform, COUNT(*) 
        FROM acquisition.external_event_observations 
        GROUP BY source_platform ORDER BY 2 DESC
    """).fetchall()
    metrics["platform_counts"] = dict(platforms)
    
    return metrics


def update_event_coverage(conn, event_key: str) -> None:
    """Update coverage snapshot for a single event."""
    row = conn.execute("""
        SELECT 
            COUNT(DISTINCT source_platform) AS total_sources,
            BOOL_OR(source_platform = 'ticketmaster.com') AS tm,
            BOOL_OR(source_platform = 'dice.fm') AS dice,
            BOOL_OR(source_platform = 'eventbrite.com') AS eb,
            BOOL_OR(source_platform = 'songkick.com') AS sk,
            BOOL_OR(source_platform = 'bandsintown.com') AS bit,
            BOOL_OR(source_platform = 'residentadvisor.net') AS ra,
            BOOL_OR(source_platform = 'allevents.in') AS ae,
            BOOL_OR(observation_type = 'TICKET_PRICE') AS has_price,
            BOOL_OR(observation_type IS NOT NULL) AS has_any,
            MIN(observed_at) AS earliest,
            MAX(observed_at) AS latest,
            COUNT(*) AS obs_count,
            COUNT(DISTINCT DATE_TRUNC('day', observed_at)) AS days_covered
        FROM acquisition.external_event_observations
        WHERE event_key = ?
    """, [event_key]).fetchone()
    
    if not row:
        return
    
    conn.execute("""
        INSERT OR REPLACE INTO acquisition.event_coverage_snapshot
            (event_key, snapshot_time, total_sources, ticketmaster_covered,
             dice_covered, eventbrite_covered, songkick_covered,
             bandsintown_covered, resident_advisor_covered, allevents_covered,
             has_price, earliest_observation, latest_observation,
             observation_count, days_with_observations)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        event_key, _now(),
        row[0], row[1], row[2], row[3], row[4], row[5],
        row[6], row[7], row[8],
        row[9], row[10], row[11], row[12],
    ])