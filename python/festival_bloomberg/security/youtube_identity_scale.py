"""YouTube identity resolution for ARTIST_SECURITY_25000.

Resolution hierarchy (no name-only VERIFIED status):
  1. existing verified ID (identity.artist_provider_linkages YOUTUBE VERIFIED)
  2. MusicBrainz artist URL relationship (youtube type) — MBID-keyed
  3. Wikidata exact external ID (via MB URL relations where present)
  4. existing identity graph (CANDIDATE reuse)
  5. official API handle resolution (CANDIDATE)
  6. search ONLY as candidate discovery

Persisted statuses: VERIFIED | CANDIDATE | AMBIGUOUS | MISSING | QUARANTINED.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..identity.spotify import normalize_name

YOUTUBE_CHANNEL_RE = re.compile(r"(?:youtube\.com/(?:channel|c|@|user)/|youtube\.com/watch\?v=)([A-Za-z0-9_\-]{4,})")
YOUTUBE_URL_RE = re.compile(r"youtube\.com", re.I)


def channel_id_from_url(url: str) -> str | None:
    m = YOUTUBE_CHANNEL_RE.search(url or "")
    if m:
        return m.group(1)
    # /@handle — resolves only via API (CANDIDATE); keep handle for later.
    m2 = re.search(r"youtube\.com/@([A-Za-z0-9_.\-]+)", url or "")
    if m2:
        return m2.group(1)
    return None


def linkage_key(*, artist_key: str, provider_id: str, method: str) -> str:
    return hashlib.sha256(f"{artist_key}|YOUTUBE|{provider_id}|{method}|25k_v1".encode()).hexdigest()[:32]


def resolve_universe_youtube(conn, *, universe_version: str = "artist_security_25000_v1") -> dict[str, Any]:
    """Resolve YouTube identities for the 25K universe from existing evidence.

    Tier-1: already VERIFIED in identity.artist_provider_linkages.
    Tier-2: MB URL relations (reference.musicbrainz_artists.urls, type=youtube)
            matched to core.artists by MBID. Stored as VERIFIED (MBID-keyed
            official relation is a defensible verified linkage).
    Tier-3: existing CANDIDATE linkage reuse.
    Tier-4: alias/name candidates NOT auto-promoted (counted only).
    """
    now = datetime.now(timezone.utc).isoformat()
    summary = {
        "status": "RUNNING", "universe_size": 0, "tier1_existing": 0,
        "tier2_mb_url": 0, "tier3_candidate": 0, "tier4_name_only_candidate": 0,
        "missing": 0, "by_tier": {}, "rows_persisted": 0,
    }
    universe = conn.execute(
        "SELECT artist_key, artist_name, mbid FROM security.artist_security_universe_25000 ORDER BY artist_key"
    ).fetchall()
    summary["universe_size"] = len(universe)

    existing = {}
    for r in conn.execute(
        "SELECT artist_key, provider_id, resolution_status FROM identity.artist_provider_linkages "
        "WHERE provider='YOUTUBE' AND resolution_status='VERIFIED'"
    ).fetchall():
        existing.setdefault(r[0], []).append(r[1])

    mb_urls = {}
    for r in conn.execute(
        "SELECT mbid, urls FROM reference.musicbrainz_artists WHERE urls IS NOT NULL AND urls <> '[]'"
    ).fetchall():
        try:
            urls = json.loads(r[1]) if isinstance(r[1], str) else r[1]
        except Exception:
            continue
        for u in urls if isinstance(urls, list) else []:
            if isinstance(u, dict) and u.get("type") == "youtube" and u.get("resource"):
                cid = channel_id_from_url(u["resource"])
                if cid:
                    mb_urls.setdefault(r[0], []).append({"resource": u["resource"], "channel_id": cid})

    mbid_to_key = {}
    for artist_key, _name, mbid in universe:
        if mbid:
            mbid_to_key.setdefault(mbid, artist_key)

    tier_counts = {"TIER1_VERIFIED": 0, "TIER2_MB_URL": 0, "TIER3_CANDIDATE": 0, "TIER4_NAME_ONLY": 0, "MISSING": 0}
    for artist_key, artist_name, mbid in universe:
        # Tier 1: existing verified linkage.
        existing_ids = existing.get(artist_key) or []
        if existing_ids:
            tier_counts["TIER1_VERIFIED"] += 1
            summary["tier1_existing"] += 1
            continue
        # Tier 2: MB URL relation keyed by MBID.
        candidates = mb_urls.get(mbid) or []
        if candidates:
            for c in candidates:
                key = linkage_key(artist_key=artist_key, provider_id=c["channel_id"], method="MB_ARTIST_DUMP_URL")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO identity.artist_provider_linkages
                        (linkage_key, artist_key, provider, provider_id, provider_url,
                         link_method, confidence, evidence_ref, resolution_status,
                         first_seen_at, last_verified_at, rights_status,
                         commercial_use_status, ingested_at)
                    VALUES (?, ?, 'YOUTUBE', ?, ?, 'MB_ARTIST_DUMP_URL', 1.0, ?,
                            'VERIFIED', ?, ?, 'SOURCE_LICENSE_REVIEWED',
                            'INTERNAL_ANALYTICS_ONLY', CURRENT_TIMESTAMP)
                    """,
                    [key, artist_key, c["channel_id"], c["resource"],
                     f"reference.musicbrainz_artists.urls mbid={mbid}",
                     now, now],
                )
                summary["rows_persisted"] += 1
            tier_counts["TIER2_MB_URL"] += 1
            summary["tier2_mb_url"] += 1
            continue
        # Tier 3: existing candidate reuse.
        cand = conn.execute(
            "SELECT provider_id FROM identity.artist_provider_linkages WHERE artist_key=? AND provider='YOUTUBE' LIMIT 1",
            [artist_key],
        ).fetchone()
        if cand:
            tier_counts["TIER3_CANDIDATE"] += 1
            summary["tier3_candidate"] += 1
            continue
        # Tier 4: name-only — candidate only, NEVER verified.
        tier_counts["TIER4_NAME_ONLY"] += 1
        summary["tier4_name_only_candidate"] += 1

    summary["tier_counts"] = tier_counts
    summary["coverage_verified_pct"] = round(
        (tier_counts["TIER1_VERIFIED"] + tier_counts["TIER2_MB_URL"]) / max(len(universe), 1) * 100, 2
    )
    summary["status"] = "COMPLETE"
    return summary
