"""Deterministic Spotify identity resolution.

This module maps a LOCAL artist name string to Spotify catalog candidates
using ONLY deterministic name normalization + similarity. It never force-merges
an identity: every outcome is recorded as a resolution row (EXACT /
HIGH_CONFIDENCE / AMBIGUOUS / NO_MATCH) in
``identity.spotify_artist_resolutions``, and only EXACT normalized-name
matches are additionally written as ``core.entity_external_ids`` rows keyed by
the documented ``name::<normalized_name>`` fallback. A model (NVIDIA/DeepSeek)
may RANK ambiguous candidates later, but this module never commits a merge.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any

SOFTWARE_VERSION = "spotify-identity-v1"

#: Rights posture for Spotify Dev Mode identity/catalog lookups.
RIGHTS_STATUS = "RESEARCH_ONLY"
COMMERCIAL_USE_STATUS = "PROTOTYPE_ONLY"


def normalize_name(name: str) -> str:
    """Deterministic artist-name normalization for matching.

    Lowercase, strip punctuation, collapse whitespace, drop a leading "the ".
    """
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    n = re.sub(r"\s+", " ", n).strip()
    while n.startswith("the "):
        n = n[4:]
    return n


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def classify(local_name: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify Spotify candidates against a local name.

    Returns one dict per candidate, or a single ``NO_MATCH`` dict when the
    search returned no candidates. Never mutates the input.
    """
    local_norm = normalize_name(local_name)
    if not candidates:
        return [{
            "spotify_id": None,
            "spotify_name": None,
            "resolution_status": "NO_MATCH",
            "match_similarity": None,
        }]
    scored = []
    for c in candidates:
        spotify_name = c.get("name") or ""
        spotify_norm = normalize_name(spotify_name)
        sim = _similarity(local_norm, spotify_norm)
        scored.append((c, sim, spotify_norm))
    out = []
    for c, sim, spotify_norm in scored:
        if local_norm and spotify_norm and spotify_norm == local_norm:
            status = "EXACT"
        elif sim >= 0.9:
            status = "HIGH_CONFIDENCE"
        else:
            status = "AMBIGUOUS"
        out.append({
            "spotify_id": c.get("id"),
            "spotify_name": c.get("name"),
            "spotify_uri": c.get("uri"),
            "spotify_url": (c.get("external_urls") or {}).get("spotify"),
            "resolution_status": status,
            "match_similarity": sim,
        })
    return out


def resolution_key(source_table: str, local_name: str, spotify_id: str | None, retrieved_at: str) -> str:
    material = f"{source_table}|{normalize_name(local_name)}|{spotify_id or 'NONE'}|{retrieved_at}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def external_id_key(spotify_id: str) -> str:
    return hashlib.sha256(f"spotify:{spotify_id}".encode("utf-8")).hexdigest()


def entity_name_key(normalized_name: str) -> str:
    """Documented fallback artist_key: ``name::<normalized_name>``."""
    return f"name::{normalized_name}"


def build_rows(
    source_table: str,
    local_name: str,
    candidates: list[dict[str, Any]],
    retrieved_at: str,
) -> list[dict[str, Any]]:
    """Build append-only resolution rows (one per candidate, or NO_MATCH)."""
    rows = []
    for r in classify(local_name, candidates):
        rows.append({
            "resolution_key": resolution_key(source_table, local_name, r["spotify_id"], retrieved_at),
            "local_artist_name": local_name,
            "normalized_local_name": normalize_name(local_name),
            "source_table": source_table,
            "spotify_id": r["spotify_id"],
            "spotify_name": r["spotify_name"],
            "spotify_uri": r["spotify_uri"],
            "spotify_url": r["spotify_url"],
            "resolution_status": r["resolution_status"],
            "match_method": "deterministic_normalized_name",
            "match_similarity": r["match_similarity"],
            "match_features": None,
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "rights_status": RIGHTS_STATUS,
            "commercial_use_status": COMMERCIAL_USE_STATUS,
            "software_version": SOFTWARE_VERSION,
        })
    return rows


def persist_resolutions(conn, rows: list[dict[str, Any]]) -> int:
    """Insert resolution rows (idempotent by resolution_key). Returns inserted count."""
    inserted = 0
    for r in rows:
        exists = conn.execute(
            "SELECT 1 FROM identity.spotify_artist_resolutions WHERE resolution_key = ?",
            [r["resolution_key"]],
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO identity.spotify_artist_resolutions
                (resolution_key, local_artist_name, normalized_local_name,
                 source_table, spotify_id, spotify_name, spotify_uri, spotify_url,
                 resolution_status, match_method, match_similarity, match_features,
                 retrieved_at, knowledge_time, rights_status,
                 commercial_use_status, software_version, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                r["resolution_key"], r["local_artist_name"], r["normalized_local_name"],
                r["source_table"], r["spotify_id"], r["spotify_name"], r["spotify_uri"],
                r["spotify_url"], r["resolution_status"], r["match_method"],
                r["match_similarity"], r["match_features"], r["retrieved_at"],
                r["knowledge_time"], r["rights_status"], r["commercial_use_status"],
                r["software_version"],
            ],
        )
        inserted += 1
    return inserted


def persist_exact_external_ids(conn, rows: list[dict[str, Any]]) -> int:
    """Write EXACT resolutions to core.entity_external_ids (append-only)."""
    inserted = 0
    for r in rows:
        if r["resolution_status"] != "EXACT" or not r["spotify_id"]:
            continue
        key = external_id_key(r["spotify_id"])
        exists = conn.execute(
            "SELECT 1 FROM core.entity_external_ids WHERE external_id_key = ?", [key]
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO core.entity_external_ids
                (external_id_key, entity_type, entity_key, id_type, id_value,
                 url, is_primary, confidence, source_system, evidence_url, ingested_at)
            VALUES (?, 'artist', ?, 'spotify', ?, ?, FALSE, 1.0, 'spotify', ?, CURRENT_TIMESTAMP)
            """,
            [
                key,
                entity_name_key(r["normalized_local_name"]),
                r["spotify_id"],
                r["spotify_url"],
                r["spotify_url"],
            ],
        )
        inserted += 1
    return inserted
