"""Deterministic Ticketmaster attraction -> canonical artist resolution.

Ticketmaster attractions are NOT guaranteed to map 1:1 to musical artists
(festival names, tour packages, tribute acts, comedians, dance parties...).
This module resolves them against the canonical artist master + the compact
MusicBrainz reference layer WITHOUT ever auto-merging:

- exact external ID -> existing canonical mapping -> MB exact name ->
  MB exact alias -> normalized exact -> multi-signal -> fuzzy candidate
- every outcome is recorded in ``identity.ticketmaster_artist_resolutions``
  with a status that distinguishes MATCHED_ARTIST / MATCHED_EVENT_OR_PACKAGE /
  AMBIGUOUS / NO_MATCH / REJECTED_NON_ARTIST.
- special-attraction classification keeps non-artist strings OUT of
  core.artists (festival names, "&" collaboration billings, tribute acts...).

NO LLM AUTO-MERGE: an LLM may rank/explain candidates later, but this module
never creates canonical identity or overwrites an external ID on its own.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..identity.spotify import normalize_name

SOFTWARE_VERSION = "ticketmaster_resolution_v1"

#: Special-attraction signal -> classification (conservative; name-only hits
#: are classified as candidates, not merged identities).
SPECIAL_SIGNALS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfestival\b", re.I), "FESTIVAL_NAME"),
    (re.compile(r"\btour\b", re.I), "TOUR_PACKAGE"),
    (re.compile(r"\bpresents\b|presents$", re.I), "PRESENTATION"),
    (re.compile(r"\b& .*\b", re.I), "COLLABORATION_BILLING"),
    (re.compile(r"\band .*\b", re.I), "COLLABORATION_BILLING"),
    (re.compile(r"\btribute\b", re.I), "TRIBUTE_ACT"),
    (re.compile(r"\bcover\b", re.I), "COVER_BAND"),
    (re.compile(r"\bdj set\b|\bdj night\b|\blive set\b", re.I), "DJ_EVENT"),
    (re.compile(r"\bcomedy\b|\bcomedian\b", re.I), "COMEDIAN"),
    (re.compile(r"\bnight\b|\bparty\b", re.I), "DANCE_PARTY"),
    (re.compile(r"\bvs\.?\b|\bversus\b", re.I), "COLLABORATION_BILLING"),
    (re.compile(r"\bexperience\b|\bsymphony\b|\borchestra\b|\bensemble\b", re.I), "SPECIAL_EVENT"),
]


def classify_special(attraction_name: str) -> str | None:
    """Classify a name as a non-plain-artist special attraction, or None.

    Conservative: only explicit linguistic signals trigger a classification;
    a plain name like "Aerosmith" returns None (a real artist candidate).
    """
    if not attraction_name:
        return None
    for pattern, kind in SPECIAL_SIGNALS:
        if pattern.search(attraction_name):
            return kind
    return None


def is_plain_artist_name(name: str) -> bool:
    """True when nothing signals a non-artist special attraction."""
    return classify_special(name) is None


def resolution_key(attraction_id: str | None, attraction_name: str, method: str, knowledge_time: str) -> str:
    material = "|".join([attraction_id or "none", normalize_name(attraction_name), method, knowledge_time[:10]])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def fetch_attraction_universe(conn) -> list[dict[str, Any]]:
    """Distinct Ticketmaster attractions from the live event snapshot corpus.

    ``attractions`` is a JSON array of {id, name, ...} objects; also fall back
    to the single ``artist_name`` column when attractions are missing.
    """
    out: dict[str, dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT platform_object_id, attractions, artist_name, retrieved_at
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
        """
    ).fetchall()
    for _event_id, attractions, artist_name, retrieved_at in rows:
        if attractions:
            try:
                items = json.loads(attractions) if isinstance(attractions, str) else attractions
            except (ValueError, TypeError):
                items = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("attraction_name") or ""
                aid = item.get("id") or item.get("ticketmaster_attraction_id")
                if not name:
                    continue
                # Dedupe by the provider's own attraction ID when present:
                # two distinct attractions sharing a normalized name must not
                # collapse into one record (identity-resolution hazard).
                key = f"id::{aid}" if aid else f"name::{normalize_name(name)}"
                existing = out.get(key)
                if existing is None:
                    out[key] = {
                        "attraction_id": aid,
                        "attraction_name": name,
                        "first_observed_at": retrieved_at,
                        "last_observed_at": retrieved_at,
                    }
                else:
                    if (retrieved_at or "") > (existing["last_observed_at"] or ""):
                        existing["last_observed_at"] = retrieved_at
        elif artist_name:
            key = f"name::{normalize_name(artist_name)}"
            if key and key not in out:
                out[key] = {
                    "attraction_id": None,
                    "attraction_name": artist_name,
                    "first_observed_at": retrieved_at,
                    "last_observed_at": retrieved_at,
                }
    return sorted(out.values(), key=lambda r: r["attraction_name"].lower())


def _mb_exact_name_candidates(conn, normalized: str) -> list[tuple[str, str, str]]:
    """(artist_key, mbid, name) rows matching an exact reference name."""
    rows = conn.execute(
        """
        SELECT artist_key, musicbrainz_id, name FROM core.artists
        WHERE normalized_name = ?
        """,
        [normalized],
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _mb_reference_name_candidates(conn, normalized: str) -> list[tuple[str, str]]:
    """(mbid, name) rows from the compact reference layer (exact name)."""
    rows = conn.execute(
        """
        SELECT mbid, name FROM reference.musicbrainz_artists
        WHERE normalized_name = ?
        """,
        [normalized],
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _mb_alias_candidates(conn, normalized: str) -> list[tuple[str, str, str]]:
    """(artist_key, mbid, alias) from canonical alias index (exact alias).

    The MBID comes from ``core.artists.musicbrainz_id`` — never from the alias
    row itself, which only stores the internal artist_key.
    """
    rows = conn.execute(
        """
        SELECT a.artist_key, ar.musicbrainz_id, a.alias
        FROM core.artist_aliases a
        JOIN core.artists ar ON ar.artist_key = a.artist_key
        WHERE a.normalized_alias = ?
        """,
        [normalized],
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _fuzzy_candidates(conn, name: str, normalized: str, limit: int = 8) -> list[dict[str, Any]]:
    """Fuzzy candidate retrieval (contains/substring + normalized edit hints).

    Returns candidates for the LLM to RANK — never an auto-merge. This uses
    cheap substring retrieval so a ranking step has something to work with.
    """
    if len(normalized) < 3:
        return []
    like = f"%{normalized}%"
    rows = conn.execute(
        """
        SELECT mbid, name FROM reference.musicbrainz_artists
        WHERE normalized_name LIKE ? OR name LIKE ?
        LIMIT ?
        """,
        [like, like, limit],
    ).fetchall()
    return [{"mbid": r[0], "name": r[1]} for r in rows]


def resolve_attraction(
    conn,
    *,
    attraction_name: str,
    attraction_id: str | None = None,
    knowledge_time: str,
) -> dict[str, Any]:
    """Resolve ONE Ticketmaster attraction to a canonical artist.

    Resolution ladder:
      A. exact external ID (attraction_id already mapped)
      B. existing canonical entity mapping (by name)
      C. MusicBrainz exact canonical name
      D. MusicBrainz exact alias
      E. normalized exact (reference layer)
      F. multi-signal (name + existing spotify/id corroboration)
      G. fuzzy candidate retrieval (LLM may rank; never auto-merge)
      H. ambiguous / unmatched

    Returns a resolution record (status, method, artist_key/mbid or None).
    """
    normalized = normalize_name(attraction_name)
    special = classify_special(attraction_name)

    # A. exact external ID mapping.
    if attraction_id:
        row = conn.execute(
            """
            SELECT entity_key, id_value FROM core.entity_external_ids
            WHERE id_type = 'ticketmaster' AND id_value = ?
            """,
            [attraction_id],
        ).fetchone()
        if row:
            return {
                "resolution_status": "MATCHED_ARTIST",
                "match_method": "EXACT_EXTERNAL_ID",
                "artist_key": row[0],
                "artist_mbid": None,
                "matched_name": attraction_name,
                "match_similarity": 1.0,
                "match_features": {"attraction_id": attraction_id},
                "special_classification": special,
            }

    # B. existing canonical mapping by normalized name.
    canonical = _mb_exact_name_candidates(conn, normalized)
    if canonical:
        if len(canonical) == 1:
            key, mbid, name = canonical[0]
            return {
                "resolution_status": "MATCHED_ARTIST",
                "match_method": "EXISTING_MAPPING",
                "artist_key": key, "artist_mbid": mbid, "matched_name": name,
                "match_similarity": 1.0,
                "match_features": {"matched_via": "canonical_name"},
                "special_classification": special,
            }
        return {
            "resolution_status": "AMBIGUOUS",
            "match_method": "EXISTING_MAPPING",
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"canonical_candidates": canonical},
            "special_classification": special,
        }

    # C/D/E. reference layer exact name / alias / normalized.
    ref = _mb_reference_name_candidates(conn, normalized)
    aliases = _mb_alias_candidates(conn, normalized)
    if ref or aliases:
        if len(ref) == 1 and not aliases:
            mbid, name = ref[0]
            return {
                "resolution_status": "MATCHED_ARTIST",
                "match_method": "MB_EXACT_NAME",
                "artist_key": None, "artist_mbid": mbid, "matched_name": name,
                "match_similarity": 1.0,
                "match_features": {"matched_via": "reference_name"},
                "special_classification": special,
            }
        if len(aliases) == 1 and not ref:
            akey, mbid, alias = aliases[0]
            return {
                "resolution_status": "MATCHED_ARTIST",
                "match_method": "MB_EXACT_ALIAS",
                "artist_key": akey, "artist_mbid": mbid, "matched_name": alias,
                "match_similarity": 1.0,
                "match_features": {"matched_via": "canonical_alias"},
                "special_classification": special,
            }
        return {
            "resolution_status": "AMBIGUOUS",
            "match_method": "MB_EXACT_NAME",
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"reference_candidates": ref, "alias_candidates": aliases},
            "special_classification": special,
        }

    # F. multi-signal: a spotify external ID exists for this normalized name.
    spot = conn.execute(
        """
        SELECT entity_key, id_value FROM core.entity_external_ids
        WHERE id_type = 'spotify' AND entity_key = ?
        """,
        [f"name::{normalized}"],
    ).fetchall()
    if len(spot) == 1:
        return {
            "resolution_status": "MATCHED_ARTIST",
            "match_method": "MULTI_SIGNAL",
            "artist_key": spot[0][0], "artist_mbid": None, "matched_name": attraction_name,
            "match_similarity": 0.95,
            "match_features": {"spotify_id": spot[0][1]},
            "special_classification": special,
        }
    if len(spot) > 1:
        return {
            "resolution_status": "AMBIGUOUS",
            "match_method": "MULTI_SIGNAL",
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"spotify_candidates": [s[1] for s in spot]},
            "special_classification": special,
        }

    # G. fuzzy candidate retrieval (never a merge by itself).
    candidates = _fuzzy_candidates(conn, attraction_name, normalized)
    if candidates:
        return {
            "resolution_status": "AMBIGUOUS",
            "match_method": "FUZZY_CANDIDATE",
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"fuzzy_candidates": candidates},
            "special_classification": special,
        }

    # H. unmatched.
    status = "REJECTED_NON_ARTIST" if special else "NO_MATCH"
    return {
        "resolution_status": status,
        "match_method": None,
        "artist_key": None, "artist_mbid": None, "matched_name": None,
        "match_similarity": None,
        "match_features": {"special_classification": special},
        "special_classification": special,
    }


def persist_resolution(
    conn,
    *,
    attraction_name: str,
    attraction_id: str | None,
    source_table: str,
    knowledge_time: str,
    result: dict[str, Any],
) -> int:
    """Persist one resolution row (idempotent by key). Returns 1 if new."""
    key = resolution_key(attraction_id, attraction_name, result.get("match_method") or "NO_MATCH", knowledge_time)
    exists = conn.execute(
        "SELECT 1 FROM identity.ticketmaster_artist_resolutions WHERE resolution_key = ?",
        [key],
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO identity.ticketmaster_artist_resolutions
            (resolution_key, attraction_id, attraction_name, normalized_name,
             artist_key, artist_mbid, matched_name, resolution_status,
             match_method, match_similarity, match_features,
             special_classification, source_table, knowledge_time,
             software_version, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            key, attraction_id, attraction_name, normalize_name(attraction_name),
            result.get("artist_key"), result.get("artist_mbid"), result.get("matched_name"),
            result.get("resolution_status"), result.get("match_method"),
            result.get("match_similarity"),
            json.dumps(result.get("match_features"), default=str),
            result.get("special_classification"), source_table, knowledge_time,
            SOFTWARE_VERSION,
        ],
    )
    return 1


def resolve_attraction_universe(
    conn,
    *,
    source_table: str = "events.provider_event_snapshots",
    knowledge_time: str | None = None,
) -> dict[str, Any]:
    """Resolve the full distinct-attraction universe deterministically."""
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    attractions = fetch_attraction_universe(conn)
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "attractions_total": len(attractions),
        "by_status": {},
        "by_method": {},
        "by_special": {},
        "rows_persisted": 0,
    }
    for a in attractions:
        result = resolve_attraction(
            conn,
            attraction_name=a["attraction_name"],
            attraction_id=a["attraction_id"],
            knowledge_time=knowledge_time,
        )
        summary["rows_persisted"] += persist_resolution(
            conn,
            attraction_name=a["attraction_name"],
            attraction_id=a["attraction_id"],
            source_table=source_table,
            knowledge_time=knowledge_time,
            result=result,
        )
        status = result["resolution_status"]
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        method = result.get("match_method") or "NONE"
        summary["by_method"][method] = summary["by_method"].get(method, 0) + 1
        special = result.get("special_classification")
        if special:
            summary["by_special"][special] = summary["by_special"].get(special, 0) + 1
    summary["status"] = "COMPLETE"
    return summary


# ---------------------------------------------------------------------------
# Identity QA (phase 14): a deterministic sample + honest metrics.
# ---------------------------------------------------------------------------
QA_SAMPLE = [
    ("Bruce Springsteen", "Bruce Springsteen"),          # plain exact
    ("The E Street Band", "E Street Band"),              # leading-the normalization
    ("Die Ärzte", "Die Ärzte"),                          # diacritics preserved
    ("KISS", "KISS"),                                    # common-word band name
    ("“Weird Al” Yankovic", "Weird Al Yankovic"),        # punctuation
    ("Trans‐Siberian Orchestra", "Trans-Siberian Orchestra"),  # unicode dash
    ("Taylor Swift", "Taylor Swift"),
    ("Bad Bunny", "Bad Bunny"),
    ("Billie Eilish", "Billie Eilish"),
    ("Coachella Music Festival", None),                  # festival name -> special
    ("Aerosmith & Journey Tour", None),                  # tour package / collab
    ("DJ Khaled", "DJ Khaled"),                          # DJ artist (plain)
    ("Tribute to Queen", None),                          # tribute act
]


def run_identity_qa(conn, *, knowledge_time: str | None = None) -> dict[str, Any]:
    """Deterministic QA over the sample pairs; reports precision-like metrics.

    A sample item is counted correct when the resolution is MATCHED_ARTIST
    with the expected MBID/name, or when a special attraction is correctly
    classified as non-artist (REJECTED/AMBIGUOUS).
    """
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    results = []
    correct = 0
    total = len(QA_SAMPLE)
    for name, expected in QA_SAMPLE:
        result = resolve_attraction(
            conn, attraction_name=name, knowledge_time=knowledge_time
        )
        if expected is None:
            # Special/non-artist: correct when NOT matched as a plain artist.
            ok = result["resolution_status"] in ("REJECTED_NON_ARTIST", "AMBIGUOUS", "NO_MATCH") \
                and result.get("special_classification") is not None
        else:
            # Verify the resolved identity actually IS the expected artist,
            # not merely that *some* artist matched: compare the returned
            # canonical name against the expected name.
            ok = (
                result["resolution_status"] == "MATCHED_ARTIST"
                and result.get("matched_name") is not None
                and normalize_name(str(result["matched_name"])) == normalize_name(expected)
            )
        if ok:
            correct += 1
        results.append({
            "attraction_name": name,
            "expected": expected,
            "status": result["resolution_status"],
            "method": result.get("match_method"),
            "matched_name": result.get("matched_name"),
            "artist_mbid": result.get("artist_mbid"),
            "correct": ok,
        })
    precision = correct / total if total else 0.0
    return {
        "sample_size": total,
        "reviewed": total,
        "correct": correct,
        "precision": round(precision, 4),
        "false_positive_rate": round(1.0 - precision, 4) if total else 0.0,
        "by_status": {},
        "results": results,
    }
