"""Persist normalized events, venues, identities, and PIT-safe reads."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from ..evidence.provenance import parse_iso, utc
from ..migrations import apply_pending_migrations
from .reconcile import ProviderEvent, ReconciledEvent, canonical_venue_id


class EventRepository:
    def __init__(self, connection) -> None:
        self.conn = connection
        apply_pending_migrations(connection)

    def upsert_identity(self, identity: dict[str, Any], *, resolved_at: datetime) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO events.artist_identities
                (canonical_artist_id, display_name, musicbrainz_mbid,
                 ticketmaster_attraction_id, youtube_channel_id, setlistfm_mbid,
                 resolution_method, ambiguities_json, resolved_at, supporting_observation_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                identity["canonical_artist_id"],
                identity["display_name"],
                identity.get("musicbrainz_mbid"),
                identity.get("ticketmaster_attraction_id"),
                identity.get("youtube_channel_id"),
                identity.get("setlistfm_mbid"),
                identity["resolution_method"],
                json.dumps(identity.get("ambiguities") or []),
                resolved_at.isoformat(),
                json.dumps(identity.get("supporting_observation_ids") or []),
            ],
        )
        self.conn.commit()

    def store_reconciled(
        self,
        cluster: ReconciledEvent,
        *,
        artist_id: str,
        retrieved_at: datetime,
    ) -> str:
        primary = cluster.members[0]
        venue_id = primary.venue_id or canonical_venue_id(primary.venue_name, primary.city)
        if venue_id:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO events.venues
                    (venue_id, venue_name, city, state, state_code, country, country_code,
                     market_id, latitude, longitude, ticketmaster_venue_id, setlistfm_venue_id,
                     first_observed_at, last_observed_at, supporting_observation_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    venue_id,
                    primary.venue_name,
                    primary.city,
                    primary.payload.get("state"),
                    primary.payload.get("state_code"),
                    primary.payload.get("country"),
                    primary.payload.get("country_code"),
                    primary.payload.get("market_id"),
                    primary.payload.get("latitude"),
                    primary.payload.get("longitude"),
                    primary.payload.get("ticketmaster_venue_id"),
                    primary.payload.get("venue_id") if primary.platform == "setlistfm" else None,
                    retrieved_at.isoformat(),
                    retrieved_at.isoformat(),
                    json.dumps([m.raw_observation_id for m in cluster.members if m.raw_observation_id]),
                ],
            )

        supporting = [m.raw_observation_id for m in cluster.members if m.raw_observation_id]
        event_type = primary.event_type or "UNKNOWN"
        festival_names = [m.festival_name for m in cluster.members if m.festival_name]
        tour_names = [m.tour_name for m in cluster.members if m.tour_name]
        self.conn.execute(
            """
            INSERT OR REPLACE INTO events.events
                (event_id, event_type, event_name, event_time, local_date, venue_id, venue_name,
                 market_id, city, state, country, festival_name, tour_name, event_status,
                 provider_support_count, first_observed_at, last_observed_at, knowledge_time,
                 match_gate, supporting_observation_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                cluster.event_id,
                event_type,
                primary.event_name,
                primary.payload.get("event_time") or primary.local_date,
                primary.local_date,
                venue_id,
                primary.venue_name,
                primary.payload.get("market_id"),
                primary.city,
                primary.payload.get("state"),
                primary.payload.get("country") or primary.payload.get("country_code"),
                festival_names[0] if festival_names else None,
                tour_names[0] if tour_names else None,
                primary.payload.get("event_status"),
                len(cluster.members),
                retrieved_at.isoformat(),
                retrieved_at.isoformat(),
                retrieved_at.isoformat(),
                cluster.match_gate,
                json.dumps(supporting),
            ],
        )
        self.conn.execute(
            """
            INSERT INTO events.artist_event_relations
                (relation_id, artist_id, event_id, role, knowledge_time, supporting_observation_ids)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (relation_id) DO UPDATE SET
                knowledge_time = excluded.knowledge_time,
                supporting_observation_ids = excluded.supporting_observation_ids
            """,
            [
                f"aer_{artist_id}_{cluster.event_id}",
                artist_id,
                cluster.event_id,
                "headliner",
                retrieved_at.isoformat(),
                json.dumps(supporting),
            ],
        )
        for member in cluster.members:
            obs_id = f"peo_{uuid.uuid4().hex[:16]}"
            self.conn.execute(
                """
                INSERT INTO events.provider_event_observations
                    (observation_id, event_id, provider, platform, platform_object_id,
                     provider_event_name, provider_venue_name, provider_date, provider_tour_name,
                     provider_festival_name, raw_observation_id, knowledge_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    obs_id,
                    cluster.event_id,
                    member.provider,
                    member.platform,
                    member.platform_object_id,
                    member.event_name,
                    member.venue_name,
                    member.local_date,
                    member.tour_name,
                    member.festival_name,
                    member.raw_observation_id,
                    member.knowledge_time,
                ],
            )
        for disagreement in cluster.disagreements:
            self.conn.execute(
                """
                INSERT INTO events.event_disagreements
                    (disagreement_id, event_id, dimension, left_provider, right_provider,
                     left_value, right_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"dis_{uuid.uuid4().hex[:16]}",
                    cluster.event_id,
                    disagreement.dimension,
                    disagreement.left_provider,
                    disagreement.right_provider,
                    disagreement.left_value,
                    disagreement.right_value,
                ],
            )
        self.conn.commit()
        return cluster.event_id

    def upsert_artist_market_relation(
        self,
        *,
        artist_id: str,
        market_id: str,
        events: list[dict[str, Any]],
        knowledge_time: datetime,
    ) -> None:
        dates = [e.get("local_date") for e in events if e.get("local_date")]
        supporting: list[str] = []
        for event in events:
            supporting.extend(event.get("supporting_observation_ids") or [])
        self.conn.execute(
            """
            INSERT INTO events.artist_market_relations
                (relation_id, artist_id, market_id, relation_type, first_event_date,
                 last_event_date, event_count, knowledge_time, supporting_observation_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (relation_id) DO UPDATE SET
                first_event_date = excluded.first_event_date,
                last_event_date = excluded.last_event_date,
                event_count = excluded.event_count,
                knowledge_time = excluded.knowledge_time,
                supporting_observation_ids = excluded.supporting_observation_ids
            """,
            [
                f"amr_{artist_id}_{market_id}",
                artist_id,
                market_id,
                "PERFORMED_IN_MARKET",
                min(dates) if dates else None,
                max(dates) if dates else None,
                len(events),
                knowledge_time.isoformat(),
                json.dumps(supporting),
            ],
        )
        self.conn.commit()

    def store_fan_link(
        self,
        *,
        youtube_video_id: str,
        event_id: str,
        link_method: str,
        supporting_evidence: str,
        knowledge_time: datetime,
        confidence_state: str = "EXPLICIT",
    ) -> str:
        link_id = f"efl_{uuid.uuid4().hex[:16]}"
        self.conn.execute(
            """
            INSERT INTO events.event_fan_links
                (link_id, youtube_video_id, canonical_event_id, link_method,
                 supporting_evidence, confidence_state, knowledge_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                link_id,
                youtube_video_id,
                event_id,
                link_method,
                supporting_evidence,
                confidence_state,
                knowledge_time.isoformat(),
            ],
        )
        self.conn.commit()
        return link_id

    def query_events(
        self,
        *,
        artist_id: str | None = None,
        market_id: str | None = None,
        cutoff: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT e.event_id, e.event_type, e.event_name, e.event_time, e.local_date,
                   e.venue_id, e.venue_name, e.market_id, e.city, e.state, e.country,
                   e.festival_name, e.tour_name, e.event_status, e.provider_support_count,
                   e.knowledge_time, e.match_gate, e.supporting_observation_ids,
                   r.artist_id
            FROM events.events e
            JOIN events.artist_event_relations r ON r.event_id = e.event_id
            WHERE 1 = 1
        """
        params: list[Any] = []
        if artist_id:
            query += " AND r.artist_id = ?"
            params.append(artist_id)
        if market_id:
            query += " AND e.market_id = ?"
            params.append(market_id)
        if cutoff is not None:
            query += " AND e.knowledge_time <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        query += " ORDER BY e.local_date NULLS LAST, e.event_id"
        rows = self.conn.execute(query, params).fetchall()
        columns = [
            "event_id",
            "event_type",
            "event_name",
            "event_time",
            "local_date",
            "venue_id",
            "venue_name",
            "market_id",
            "city",
            "state",
            "country",
            "festival_name",
            "tour_name",
            "event_status",
            "provider_support_count",
            "knowledge_time",
            "match_gate",
            "supporting_observation_ids",
            "artist_id",
        ]
        results = []
        for row in rows:
            item = dict(zip(columns, row))
            raw_ids = item.get("supporting_observation_ids")
            if isinstance(raw_ids, str):
                try:
                    item["supporting_observation_ids"] = json.loads(raw_ids)
                except json.JSONDecodeError:
                    item["supporting_observation_ids"] = []
            results.append(item)
        return results

    def query_provider_observations(self, event_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT observation_id, event_id, provider, platform, platform_object_id,
                   provider_event_name, provider_venue_name, provider_date,
                   provider_tour_name, provider_festival_name, raw_observation_id,
                   knowledge_time
            FROM events.provider_event_observations
            WHERE event_id = ?
            """,
            [event_id],
        ).fetchall()
        keys = [
            "observation_id",
            "event_id",
            "provider",
            "platform",
            "platform_object_id",
            "provider_event_name",
            "provider_venue_name",
            "provider_date",
            "provider_tour_name",
            "provider_festival_name",
            "raw_observation_id",
            "knowledge_time",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def query_disagreements(self, event_id: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT disagreement_id, event_id, dimension, left_provider, right_provider,
                   left_value, right_value
            FROM events.event_disagreements
        """
        params: list[Any] = []
        if event_id:
            sql += " WHERE event_id = ?"
            params.append(event_id)
        rows = self.conn.execute(sql, params).fetchall()
        keys = [
            "disagreement_id",
            "event_id",
            "dimension",
            "left_provider",
            "right_provider",
            "left_value",
            "right_value",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def query_venues(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT venue_id, venue_name, city, state, state_code, country, country_code,
                   market_id, latitude, longitude, ticketmaster_venue_id, setlistfm_venue_id,
                   first_observed_at, last_observed_at, supporting_observation_ids
            FROM events.venues
            ORDER BY venue_name
            """
        ).fetchall()
        keys = [
            "venue_id",
            "venue_name",
            "city",
            "state",
            "state_code",
            "country",
            "country_code",
            "market_id",
            "latitude",
            "longitude",
            "ticketmaster_venue_id",
            "setlistfm_venue_id",
            "first_observed_at",
            "last_observed_at",
            "supporting_observation_ids",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def query_fan_links(self, *, event_id: str | None = None, video_id: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT link_id, youtube_video_id, canonical_event_id, link_method,
                   supporting_evidence, confidence_state, knowledge_time
            FROM events.event_fan_links
            WHERE 1 = 1
        """
        params: list[Any] = []
        if event_id:
            sql += " AND canonical_event_id = ?"
            params.append(event_id)
        if video_id:
            sql += " AND youtube_video_id = ?"
            params.append(video_id)
        rows = self.conn.execute(sql, params).fetchall()
        keys = [
            "link_id",
            "youtube_video_id",
            "canonical_event_id",
            "link_method",
            "supporting_evidence",
            "confidence_state",
            "knowledge_time",
        ]
        return [dict(zip(keys, row)) for row in rows]


def provider_event_from_record(record: dict[str, Any], *, artist_id: str, raw_observation_id: str | None) -> ProviderEvent:
    return ProviderEvent(
        provider=record.get("provider") or "",
        platform=record.get("platform") or "",
        platform_object_id=str(record.get("platform_object_id") or ""),
        artist_id=artist_id,
        event_name=record.get("event_name") or record.get("text"),
        local_date=str(record.get("local_date") or "") or None,
        venue_id=record.get("ticketmaster_venue_id") or record.get("venue_id"),
        venue_name=record.get("venue_name"),
        city=record.get("city"),
        event_type=record.get("event_type"),
        tour_name=record.get("tour_name"),
        festival_name=record.get("festival_name"),
        raw_observation_id=raw_observation_id,
        knowledge_time=str(record.get("knowledge_time") or record.get("retrieved_at") or ""),
        payload=record,
    )
