"""Canonical artist master bootstrap from MusicBrainz event performers.

The reference graph already carries 113k+ distinct MusicBrainz artist MBIDs in
``core.event_performers`` (migration 028). This module promotes those REAL
artist identities into the canonical ``core.artists`` master without waiting
for the large artist dump:

- artist_key stays OURS (``mbid::<mbid>``); the MusicBrainz MBID is an
  EXTERNAL identifier stored in ``core.entity_external_ids`` (namespace
  ``musicbrainz``) — never the conceptual application primary key.
- Only name + normalized_name + MBID are written. Nothing is invented:
  no country, genre, Spotify metrics, management, agency, label.
- Different credited names for the same MBID are preserved as candidate
  aliases/credits in ``core.artist_aliases`` (never silently overwritten).

Evidence class: MusicBrainz core data is CROWD_CURATED_REFERENCE (CC0), not an
official primary source; that is recorded in provenance.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from ..identity.spotify import normalize_name

SOFTWARE_VERSION = "artist_master_bootstrap_v1"
SOURCE_SYSTEM = "musicbrainz"
EVIDENCE_CLASS = "CROWD_CURATED_REFERENCE"


def artist_key_for(mbid: str) -> str:
    """Canonical artist_key: ``mbid::<mbid>`` (ours, MBID is external)."""
    return f"mbid::{mbid}"


def alias_key(artist_key: str, credited_name: str) -> str:
    material = "|".join([artist_key, normalize_name(credited_name)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def external_id_key(mbid: str) -> str:
    return hashlib.sha256(f"musicbrainz:{mbid}".encode("utf-8")).hexdigest()[:32]


def collect_performer_mbids(conn) -> list[dict[str, Any]]:
    """Distinct performer MBIDs with their most-common credited name + counts.

    Returns rows ``{artist_mbid, artist_name, relation_count, festival_count,
    tour_count, recent_count}`` ordered by relation_count DESC.
    """
    rows = conn.execute(
        """
        SELECT artist_mbid,
               ARBITRARY(artist_name) AS artist_name,
               COUNT(*) AS relation_count,
               COUNT(DISTINCT event_mbid) AS event_count
        FROM core.event_performers
        WHERE artist_mbid IS NOT NULL
        GROUP BY artist_mbid
        ORDER BY relation_count DESC
        """
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "artist_mbid": r[0],
            "artist_name": r[1],
            "relation_count": int(r[2]),
            "event_count": int(r[3]),
        })
    return out


def credited_names_for(conn, artist_mbid: str) -> list[tuple[str, int]]:
    """Distinct credited names for one MBID, most frequent first."""
    rows = conn.execute(
        """
        SELECT artist_name, COUNT(*) AS n
        FROM core.event_performers
        WHERE artist_mbid = ? AND artist_name IS NOT NULL
        GROUP BY artist_name
        ORDER BY n DESC
        """,
        [artist_mbid],
    ).fetchall()
    return [(r[0], int(r[1])) for r in rows]


def persist_canonical_artist(
    conn,
    *,
    artist_mbid: str,
    primary_name: str,
    knowledge_time: str,
) -> int:
    """Insert one canonical artist row + musicbrainz external ID.

    Returns 1 if the artist was newly created, 0 if it already existed.
    """
    key = artist_key_for(artist_mbid)
    exists = conn.execute(
        "SELECT 1 FROM core.artists WHERE artist_key = ?", [key]
    ).fetchone()
    if exists:
        return 0
    norm = normalize_name(primary_name)
    conn.execute(
        """
        INSERT INTO core.artists
            (artist_key, musicbrainz_id, name, normalized_name, sort_name,
             type, source_system, evidence, evidence_url, extraction_method,
             resolution_status, manually_reviewed, ingested_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'UNKNOWN', 'musicbrainz', ?, NULL,
                'mbid_from_event_performers', 'REFERENCE', FALSE,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [
            key, artist_mbid, primary_name, norm, primary_name,
            json.dumps({"evidence_class": EVIDENCE_CLASS,
                        "semantics": "CROWD_CURATED_REFERENCE; not an official primary source",
                        "software_version": SOFTWARE_VERSION}),
        ],
    )
    # External identifier: MBID is a MAPPING, never the app primary key.
    eid_key = external_id_key(artist_mbid)
    conn.execute(
        """
        INSERT OR IGNORE INTO core.entity_external_ids
            (external_id_key, entity_type, entity_key, id_type, id_value,
             url, is_primary, confidence, source_system, namespace,
             resolution_status, resolution_method, first_seen_at, last_seen_at,
             knowledge_time, ingested_at)
        VALUES (?, 'artist', ?, 'musicbrainz', ?,
                ?, FALSE, 1.0, 'musicbrainz', 'musicbrainz',
                'CROWD_CURATED_REFERENCE', 'mbid_from_event_performers',
                ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            eid_key, key, artist_mbid,
            f"https://musicbrainz.org/artist/{artist_mbid}",
            knowledge_time, knowledge_time, knowledge_time,
        ],
    )
    # ``core.artist_resolution_keys`` is a VIEW that derives blocking keys
    # from core.artists / artist_aliases / entity_external_ids automatically,
    # so no separate insert is needed here.
    return 1


def persist_credit_aliases(
    conn,
    *,
    artist_mbid: str,
    credited_names: list[tuple[str, int]],
    knowledge_time: str,
) -> int:
    """Preserve every distinct credited name as a candidate alias/credit.

    Never overwrites: each (artist, credited name) pair is a unique alias row.
    Returns the number of NEW alias rows written.
    """
    key = artist_key_for(artist_mbid)
    inserted = 0
    for index, (name, n) in enumerate(credited_names):
        akey = alias_key(key, name)
        exists = conn.execute(
            "SELECT 1 FROM core.artist_aliases WHERE alias_key = ?", [akey]
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO core.artist_aliases
                (alias_key, artist_key, alias, normalized_alias, alias_type,
                 locale, is_primary, source_system, confidence, ingested_at)
            VALUES (?, ?, ?, ?, 'CREDIT_NAME', NULL, ?, 'musicbrainz',
                    ?, CURRENT_TIMESTAMP)
            """,
            [
                akey, key, name, normalize_name(name),
                index == 0,  # most-frequent credit is the primary display name
                min(1.0, 0.5 + 0.1 * min(n, 5)),
            ],
        )
        inserted += 1
    return inserted


def bootstrap_canonical_artists(
    conn,
    *,
    limit: int | None = None,
    knowledge_time: str | None = None,
) -> dict[str, Any]:
    """Bootstrap core.artists from distinct event-performer MBIDs.

    Returns a summary with per-MBID counts. Idempotent: re-running only adds
    artists not already present.
    """
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    performers = collect_performer_mbids(conn)
    if limit:
        performers = performers[:limit]

    summary: dict[str, Any] = {
        "status": "RUNNING",
        "distinct_mbids": len(performers),
        "artists_created": 0,
        "artists_existing": 0,
        "alias_rows_written": 0,
        "relation_total": sum(p["relation_count"] for p in performers),
        "event_total": sum(p["event_count"] for p in performers),
        "knowledge_time": knowledge_time,
    }
    for p in performers:
        created = persist_canonical_artist(
            conn,
            artist_mbid=p["artist_mbid"],
            primary_name=p["artist_name"] or p["artist_mbid"],
            knowledge_time=knowledge_time,
        )
        if created:
            summary["artists_created"] += 1
        else:
            summary["artists_existing"] += 1
        credits = credited_names_for(conn, p["artist_mbid"])
        summary["alias_rows_written"] += persist_credit_aliases(
            conn,
            artist_mbid=p["artist_mbid"],
            credited_names=credits,
            knowledge_time=knowledge_time,
        )
    summary["status"] = "COMPLETE"
    return summary


def measure_performer_universe(conn) -> dict[str, Any]:
    """Phase-2 measurement: distinct MBIDs by context (festival/tour/recent)."""
    out: dict[str, Any] = {}
    queries = {
        "distinct_performer_mbids": """
            SELECT COUNT(DISTINCT artist_mbid) FROM core.event_performers
            WHERE artist_mbid IS NOT NULL""",
        "distinct_credited_names": """
            SELECT COUNT(DISTINCT artist_name) FROM core.event_performers
            WHERE artist_name IS NOT NULL""",
        "mbids_in_festival_events": """
            SELECT COUNT(DISTINCT ep.artist_mbid)
            FROM core.event_performers ep
            JOIN core.series_events se ON se.event_mbid = ep.event_mbid
            JOIN core.event_series s ON s.series_key = se.series_key
            WHERE s.series_type = 'FESTIVAL'""",
        "mbids_in_tour_events": """
            SELECT COUNT(DISTINCT ep.artist_mbid)
            FROM core.event_performers ep
            JOIN core.series_events se ON se.event_mbid = ep.event_mbid
            JOIN core.event_series s ON s.series_key = se.series_key
            WHERE s.series_type = 'TOUR'""",
        "mbids_in_recent_events": """
            SELECT COUNT(DISTINCT ep.artist_mbid)
            FROM core.event_performers ep
            JOIN raw.musicbrainz_event e ON e.mbid = ep.event_mbid
            WHERE e.begin_date >= '2023-01-01'""",
    }
    for key, sql in queries.items():
        try:
            out[key] = int(conn.execute(sql).fetchone()[0])
        except Exception:  # noqa: BLE001
            out[key] = None
    return out
