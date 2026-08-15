"""Festival spine repository: canonical write/read over core.festival_*.

The write side is idempotent and append-only where auditability matters:
billing observations are keyed by a stable dedupe_key so re-ingestion never
duplicates a claim and never rewrites an earlier one. Identity is never
forced: ``artist_key`` stays NULL until deterministic entity resolution
assigns it, and conflicting billing observations coexist by design.

Read helpers power the terminal's FEST page and the descriptive intelligence
(billing trajectory, co-occurrence, relationship graph) without inventing a
composite score.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .seed import build_seed_rows

#: entity/type enums shared with the tape.
PERFORMANCE_STATUSES: frozenset[str] = frozenset(
    {"announced", "scheduled", "performed", "cancelled", "substituted",
     "surprise", "unverified"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    try:
        cur = conn.execute(sql, params or [])
    except Exception:
        return []
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


class FestivalSpineRepository:
    """Write/read access to the canonical festival spine."""

    def __init__(self, connection) -> None:
        self.conn = connection

    # -- write ----------------------------------------------------------------
    def ingest_seed(self, rows: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, int]:
        """Idempotently ingest research-seed rows. Returns counts written."""
        rows = rows or build_seed_rows()
        counts = {"festivals": 0, "editions": 0, "lineup_slots": 0,
                  "billing_observations": 0}

        for r in rows["festivals"]:
            exists = self.conn.execute(
                "SELECT 1 FROM core.festivals WHERE festival_key = ?", [r["festival_key"]]
            ).fetchone()
            if exists:
                continue
            self.conn.execute(
                """
                INSERT INTO core.festivals
                    (festival_key, name, normalized_name, aliases, location_country,
                     location_city, location_region, first_edition_year,
                     source_system, source_url, evidence, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    r["festival_key"], r["name"], r["normalized_name"],
                    _json(r.get("aliases")), r.get("location_country"),
                    r.get("location_city"), r.get("location_region"),
                    r.get("first_edition_year"), r.get("source_system"),
                    r.get("source_url"), _json(r.get("evidence")), _now(),
                ],
            )
            counts["festivals"] += 1

        for r in rows["editions"]:
            exists = self.conn.execute(
                "SELECT 1 FROM core.festival_editions WHERE edition_key = ?", [r["edition_key"]]
            ).fetchone()
            if exists:
                continue
            self.conn.execute(
                """
                INSERT INTO core.festival_editions
                    (edition_key, festival_key, year, start_date, end_date,
                     venue_name, location_city, location_region, location_country,
                     date_precision, source_system, source_url, evidence, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    r["edition_key"], r["festival_key"], r["year"],
                    r.get("start_date"), r.get("end_date"), r.get("venue_name"),
                    r.get("location_city"), r.get("location_region"),
                    r.get("location_country"), r.get("date_precision"),
                    r.get("source_system"), r.get("source_url"),
                    _json(r.get("evidence")), _now(),
                ],
            )
            counts["editions"] += 1

        for r in rows["lineup_slots"]:
            exists = self.conn.execute(
                "SELECT 1 FROM core.lineup_slots WHERE slot_key = ?", [r["slot_key"]]
            ).fetchone()
            if exists:
                continue
            self.conn.execute(
                """
                INSERT INTO core.lineup_slots
                    (slot_key, festival_key, edition_key, year, artist_key,
                     artist_name, normalized_artist_name, performance_status,
                     identity_confidence, source_system, source_url, evidence,
                     ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    r["slot_key"], r["festival_key"], r["edition_key"], r["year"],
                    r.get("artist_key"), r["artist_name"], r.get("normalized_artist_name"),
                    r.get("performance_status"), r.get("identity_confidence"),
                    r.get("source_system"), r.get("source_url"), _json(r.get("evidence")),
                    _now(),
                ],
            )
            counts["lineup_slots"] += 1

        for r in rows["billing_observations"]:
            exists = self.conn.execute(
                "SELECT 1 FROM core.festival_billing_observations WHERE dedupe_key = ?",
                [r["dedupe_key"]],
            ).fetchone()
            if exists:
                continue
            self.conn.execute(
                """
                INSERT INTO core.festival_billing_observations
                    (observation_id, festival_key, edition_key, artist_key,
                     raw_artist_name, billing_context, printed_order, printed_tier,
                     billing_group, headline_flag, co_headliner_flag, first_line_flag,
                     closing_act_flag, stage_name, day_label, set_time_order,
                     extraction_method, extraction_version, identity_confidence,
                     source_provider, source_url, source_document_id, publication_date,
                     rights_status, commercial_use_status, evidence_class, notes,
                     dedupe_key, software_version, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    r["observation_id"], r["festival_key"], r["edition_key"],
                    r.get("artist_key"), r["raw_artist_name"], r["billing_context"],
                    r.get("printed_order"), r.get("printed_tier"), r.get("billing_group"),
                    r.get("headline_flag"), r.get("co_headliner_flag"),
                    r.get("first_line_flag"), r.get("closing_act_flag"),
                    r.get("stage_name"), r.get("day_label"), r.get("set_time_order"),
                    r.get("extraction_method"), r.get("extraction_version"),
                    r.get("identity_confidence"), r["source_provider"],
                    r.get("source_url"), r.get("source_document_id"),
                    r.get("publication_date"), r["rights_status"],
                    r["commercial_use_status"], r["evidence_class"], r.get("notes"),
                    r["dedupe_key"], r.get("software_version"), _now(),
                ],
            )
            counts["billing_observations"] += 1

        return counts

    # -- read -----------------------------------------------------------------
    def list_festivals(self) -> list[dict[str, Any]]:
        return _rows(self.conn, """
            SELECT festival_key, name, normalized_name, location_country,
                   location_city, location_region, first_edition_year,
                   source_system, source_url
            FROM core.festivals ORDER BY first_edition_year, name
        """)

    def get_festival(self, festival_key: str) -> dict[str, Any] | None:
        f = _rows(self.conn, """
            SELECT festival_key, name, normalized_name, location_country,
                   location_city, location_region, first_edition_year,
                   source_system, source_url
            FROM core.festivals WHERE festival_key = ?
        """, [festival_key])
        if not f:
            return None
        out = dict(f[0])
        out["editions"] = _rows(self.conn, """
            SELECT edition_key, year, start_date, end_date, venue_name,
                   location_city, location_region, location_country,
                   date_precision, source_url, evidence
            FROM core.festival_editions WHERE festival_key = ? ORDER BY year
        """, [festival_key])
        return out

    def get_edition(self, edition_key: str) -> dict[str, Any] | None:
        e = _rows(self.conn, """
            SELECT edition_key, festival_key, year, start_date, end_date,
                   venue_name, location_city, location_region, location_country,
                   date_precision, source_url, evidence
            FROM core.festival_editions WHERE edition_key = ?
        """, [edition_key])
        if not e:
            return None
        out = dict(e[0])
        out["lineup"] = self.get_lineup(edition_key)
        out["billing"] = self.get_billing(edition_key)
        return out

    def get_lineup(self, edition_key: str) -> list[dict[str, Any]]:
        return _rows(self.conn, """
            SELECT slot_key, artist_key, artist_name, performance_status,
                   identity_confidence, source_url
            FROM core.lineup_slots WHERE edition_key = ? ORDER BY artist_name
        """, [edition_key])

    def get_billing(self, edition_key: str) -> list[dict[str, Any]]:
        return _rows(self.conn, """
            SELECT observation_id, raw_artist_name, billing_context, printed_order,
                   printed_tier, billing_group, headline_flag, source_provider,
                   source_url, evidence_class, notes
            FROM core.festival_billing_observations
            WHERE edition_key = ? ORDER BY printed_tier, printed_order
        """, [edition_key])


# ---------------------------------------------------------------------------
# Descriptive intelligence (no composite score).
# ---------------------------------------------------------------------------
def _artist_match(name: str) -> str:
    return name.strip().lower()


def billing_trajectory(conn, artist_name: str) -> list[dict[str, Any]]:
    """Observed billing trajectory for an artist across festival editions.

    One row per source-specific billing observation, ordered by edition year,
    so a reader can see UNDER-CARD -> MID -> DIRECT SUPPORT -> HEADLINER only
    where the underlying observations actually support it.
    """
    return _rows(conn, """
        SELECT b.festival_key, f.name AS festival_name, b.edition_key,
               e.year, b.printed_tier, b.billing_group, b.billing_context,
               b.source_provider, b.source_url, b.evidence_class, b.notes
        FROM core.festival_billing_observations b
        JOIN core.festivals f ON f.festival_key = b.festival_key
        JOIN core.festival_editions e ON e.edition_key = b.edition_key
        WHERE lower(b.raw_artist_name) = ?
        ORDER BY e.year, b.printed_tier
    """, [_artist_match(artist_name)])


def co_occurrence(conn, artist_name: str, limit: int = 25) -> list[dict[str, Any]]:
    """Artists who co-appear with ``artist_name`` across festival editions."""
    return _rows(conn, """
        SELECT other.artist_name, COUNT(DISTINCT other.edition_key) AS shared_editions,
               MIN(e.year) AS first_year, MAX(e.year) AS last_year
        FROM core.lineup_slots target
        JOIN core.lineup_slots other
          ON other.edition_key = target.edition_key
         AND lower(other.artist_name) != lower(target.artist_name)
        JOIN core.festival_editions e ON e.edition_key = other.edition_key
        WHERE lower(target.artist_name) = ?
        GROUP BY other.artist_name
        ORDER BY shared_editions DESC, other.artist_name
        LIMIT ?
    """, [_artist_match(artist_name), limit])


def relationship_graph(conn, artist_name: str) -> dict[str, Any]:
    """Artist -> festivals, editions, co-billed artists (each edge evidenced)."""
    appearances = _rows(conn, """
        SELECT l.festival_key, f.name AS festival_name, l.edition_key, e.year,
               l.performance_status, l.source_url
        FROM core.lineup_slots l
        JOIN core.festivals f ON f.festival_key = l.festival_key
        JOIN core.festival_editions e ON e.edition_key = l.edition_key
        WHERE lower(l.artist_name) = ? ORDER BY e.year
    """, [_artist_match(artist_name)])
    return {
        "artist_name": artist_name,
        "festival_appearances": appearances,
        "co_billed_artists": co_occurrence(conn, artist_name),
    }
