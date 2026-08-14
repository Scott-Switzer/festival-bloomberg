"""Tracked event registry for recurring market history collection.

Provides lifecycle management for events under active observation.
Tracking status transitions: ACTIVE -> COMPLETED/EXPIRED/CANCELED/PAUSED.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..acquisition.contracts import utc_now


# Tracking status constants
TRACKING_ACTIVE = "ACTIVE"
TRACKING_COMPLETED = "COMPLETED"
TRACKING_EXPIRED = "EXPIRED"
TRACKING_CANCELED = "CANCELED"
TRACKING_PAUSED = "PAUSED"

TRACKING_STATUSES = [
    TRACKING_ACTIVE,
    TRACKING_COMPLETED,
    TRACKING_EXPIRED,
    TRACKING_CANCELED,
    TRACKING_PAUSED,
]


class TrackedEvent:
    """A tracked event with lifecycle state."""
    
    def __init__(
        self,
        canonical_event_id: str,
        artist_id: str,
        venue_id: str,
        event_time: datetime,
        tracking_started_at: datetime,
        tracking_status: str = TRACKING_ACTIVE,
        providers: list[str] | None = None,
        reason: str | None = None,
        last_snapshot_at: datetime | None = None,
        knowledge_time: datetime | None = None,
    ):
        self.canonical_event_id = canonical_event_id
        self.artist_id = artist_id
        self.venue_id = venue_id
        self.event_time = event_time
        self.tracking_started_at = tracking_started_at
        self.tracking_status = tracking_status
        self.providers = providers or []
        self.reason = reason
        self.last_snapshot_at = last_snapshot_at
        self.knowledge_time = knowledge_time or utc_now()
    
    def to_row(self) -> dict[str, Any]:
        return {
            "canonical_event_id": self.canonical_event_id,
            "artist_id": self.artist_id,
            "venue_id": self.venue_id,
            "event_time": self.event_time.isoformat(),
            "tracking_started_at": self.tracking_started_at.isoformat(),
            "tracking_status": self.tracking_status,
            "providers": json.dumps(self.providers),
            "reason": self.reason,
            "last_snapshot_at": self.last_snapshot_at.isoformat() if self.last_snapshot_at else None,
            "knowledge_time": self.knowledge_time.isoformat(),
        }
    
    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TrackedEvent":
        def parse_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                return datetime.fromisoformat(val)
            return datetime.fromisoformat(str(val))
        
        return cls(
            canonical_event_id=row["canonical_event_id"],
            artist_id=row["artist_id"],
            venue_id=row["venue_id"],
            event_time=parse_dt(row["event_time"]),
            tracking_started_at=parse_dt(row["tracking_started_at"]),
            tracking_status=row["tracking_status"],
            providers=json.loads(row["providers"]) if row.get("providers") else [],
            reason=row.get("reason"),
            last_snapshot_at=parse_dt(row.get("last_snapshot_at")),
            knowledge_time=parse_dt(row["knowledge_time"]),
        )


class TrackedEventRegistry:
    """Registry for tracking events with recurring collection."""
    
    def __init__(self, economics_repo, post_event_window_hours: int = 48) -> None:
        self.repo = economics_repo
        self.post_event_window_hours = post_event_window_hours
    
    def track_event(
        self,
        canonical_event_id: str,
        artist_id: str,
        venue_id: str,
        event_time: datetime,
        providers: list[str] | None = None,
        reason: str | None = None,
    ) -> TrackedEvent:
        """Add or update a tracked event. Idempotent."""
        existing = self.get_event(canonical_event_id)
        now = utc_now()
        
        if existing:
            # Update last snapshot time if re-tracking
            existing.last_snapshot_at = now
            existing.knowledge_time = now
            if providers:
                existing.providers = providers
            if reason:
                existing.reason = reason
            self._upsert(existing)
            return existing
        
        tracked = TrackedEvent(
            canonical_event_id=canonical_event_id,
            artist_id=artist_id,
            venue_id=venue_id,
            event_time=event_time,
            tracking_started_at=now,
            tracking_status=TRACKING_ACTIVE,
            providers=providers or ["ticketmaster"],
            reason=reason,
            last_snapshot_at=None,
            knowledge_time=now,
        )
        self._upsert(tracked)
        return tracked
    
    def untrack_event(self, canonical_event_id: str) -> bool:
        """Remove event from tracking registry."""
        existing = self.get_event(canonical_event_id)
        if not existing:
            return False
        
        self.repo.conn.execute(
            "DELETE FROM economics.tracked_events WHERE canonical_event_id = ?",
            [canonical_event_id],
        )
        self.repo.conn.commit()
        return True
    
    def get_event(self, canonical_event_id: str) -> TrackedEvent | None:
        """Get tracked event by ID."""
        row = self.repo.conn.execute(
            "SELECT * FROM economics.tracked_events WHERE canonical_event_id = ?",
            [canonical_event_id],
        ).fetchone()
        if not row:
            return None
        
        cols = [col[0] for col in self.repo.conn.description]
        return TrackedEvent.from_row(dict(zip(cols, row)))
    
    def get_active_events(self, as_of: datetime | None = None) -> list[TrackedEvent]:
        """Get all currently active tracked events."""
        as_of = as_of or utc_now()
        rows = self.repo.conn.execute(
            """
            SELECT * FROM economics.tracked_events 
            WHERE tracking_status = ? 
            ORDER BY event_time ASC
            """,
            [TRACKING_ACTIVE],
        ).fetchall()
        
        if not rows:
            return []
        
        cols = [col[0] for col in self.repo.conn.description]
        events = [TrackedEvent.from_row(dict(zip(cols, row))) for row in rows]
        
        # Filter events that should remain active based on post-event window
        active = []
        for event in events:
            # Ensure event_time is timezone-aware
            event_time = event.event_time
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            cutoff = event_time + timedelta(hours=self.post_event_window_hours)
            # Ensure as_of is timezone-aware
            as_of_tz = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
            if as_of_tz <= cutoff:
                active.append(event)
        
        return active
    
    def update_event_status(
        self,
        canonical_event_id: str,
        new_status: str,
        reason: str | None = None,
    ) -> bool:
        """Update tracking status for an event."""
        if new_status not in TRACKING_STATUSES:
            raise ValueError(f"Invalid tracking status: {new_status}")
        
        existing = self.get_event(canonical_event_id)
        if not existing:
            return False
        
        existing.tracking_status = new_status
        existing.reason = reason or existing.reason
        existing.knowledge_time = utc_now()
        self._upsert(existing)
        return True
    
    def update_snapshot_time(self, canonical_event_id: str, snapshot_time: datetime) -> bool:
        """Update last snapshot time for an event."""
        existing = self.get_event(canonical_event_id)
        if not existing:
            return False
        
        existing.last_snapshot_at = snapshot_time
        existing.knowledge_time = utc_now()
        self._upsert(existing)
        return True
    
    def transition_expired_events(self, as_of: datetime | None = None) -> int:
        """Transition events past post-event window to EXPIRED.

        Queries the full registry (not ``get_active_events``, which already
        filters by the window) so ACTIVE rows past their window can actually
        be transitioned.
        """
        as_of = as_of or utc_now()
        as_of_tz = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        rows = self.repo.conn.execute(
            """
            SELECT * FROM economics.tracked_events 
            WHERE tracking_status = ?
            """,
            [TRACKING_ACTIVE],
        ).fetchall()
        if not rows:
            return 0

        cols = [col[0] for col in self.repo.conn.description]
        transitioned = 0
        for row in rows:
            event = TrackedEvent.from_row(dict(zip(cols, row)))
            event_time = event.event_time
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            cutoff = event_time + timedelta(hours=self.post_event_window_hours)
            if as_of_tz > cutoff:
                self.update_event_status(
                    event.canonical_event_id,
                    TRACKING_EXPIRED,
                    reason="Post-event observation window closed",
                )
                transitioned += 1
        
        return transitioned
    
    def _upsert(self, event: TrackedEvent) -> None:
        """Insert or update tracked event."""
        row = event.to_row()
        self.repo.conn.execute(
            """
            INSERT OR REPLACE INTO economics.tracked_events
                (canonical_event_id, artist_id, venue_id, event_time, tracking_started_at,
                 tracking_status, providers, reason, last_snapshot_at, knowledge_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["canonical_event_id"],
                row["artist_id"],
                row["venue_id"],
                row["event_time"],
                row["tracking_started_at"],
                row["tracking_status"],
                row["providers"],
                row["reason"],
                row["last_snapshot_at"],
                row["knowledge_time"],
            ],
        )
        self.repo.conn.commit()
