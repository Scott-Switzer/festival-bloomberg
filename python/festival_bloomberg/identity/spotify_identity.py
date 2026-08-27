"""ARTIST_SECURITY_1000_SCALE_V1 — P4: Spotify identity repair + catalog.

The pilot exposed the weakness this module fixes: Spotify IDs exist in the
lake keyed by ``name::<normalized_name>`` (the legacy fallback key), so they
do NOT join cleanly to the MBID-centered security universe.

Repair path (strict, never a silent name merge):
1. LAKE_NAME_JOIN — lake Spotify IDs are joined to the universe by normalized
   name. Name match generates a CANDIDATE linkage only (resolution_status
   CANDIDATE, confidence < 1.0). If one normalized name maps to more than one
   distinct Spotify ID, the candidates are marked AMBIGUOUS and FAIL CLOSED.
   These are never promoted to VERIFIED without a second evidence source.
2. PROVIDER_SEARCH_CANDIDATE — a real Spotify search for artists without a
   lake ID. Candidates are classified by exact normalized-name match
   (EXACT -> CANDIDATE linkage) or similarity (AMBIGUOUS). Never verified.
3. CATALOG — where a Spotify ID exists, the artists/{id}/albums endpoint is
   read for catalog structure (albums vs singles, release dates, total
   tracks, album group) and persisted as SPOTIFY_CATALOG_RELEASE rows with
   fields_present recorded. No popularity/followers proxy is ever built.

Rights: Spotify Dev Mode — identity/catalog only, RESEARCH_ONLY,
PROTOTYPE_ONLY, never used for ML training per Spotify terms.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from ..acquisition.contracts import AcquisitionRequest, utc_now
from ..acquisition.providers.spotify import SpotifyProvider
from .artist_master import artist_key_for
from .identity_master import _linkage, persist_linkages
from .spotify import classify, normalize_name, persist_exact_external_ids, persist_resolutions

SOFTWARE_VERSION = "artist_spotify_identity_v1"

#: Rights posture for Spotify Dev Mode identity/catalog lookups.
RIGHTS_STATUS = "RESEARCH_ONLY"
COMMERCIAL_USE_STATUS = "PROTOTYPE_ONLY"


# ---------------------------------------------------------------------------
# 1. Lake name-join repair (candidate only, ambiguity fails closed)
# ---------------------------------------------------------------------------

def lake_spotify_candidates(conn, *, universe: list[dict[str, Any]]) -> dict[str, Any]:
    """Join lake Spotify IDs (name::-keyed) to the MBID universe by name.

    Every matched pair becomes a CANDIDATE linkage. A normalized name mapping
    to MULTIPLE distinct Spotify IDs is AMBIGUOUS (fails closed). Returns
    linkage rows + a summary.
    """
    # name::<normalized> -> spotify id(s) from the lake
    lake_rows = conn.execute(
        """
        SELECT entity_key, id_value, url
        FROM core.entity_external_ids
        WHERE id_type = 'spotify' AND entity_key LIKE 'name::%'
        """
    ).fetchall()
    by_normalized: dict[str, list[dict[str, Any]]] = {}
    for entity_key, id_value, url in lake_rows:
        if not id_value:
            continue
        norm = entity_key[6:] if entity_key.startswith("name::") else normalize_name(entity_key)
        by_normalized.setdefault(norm, []).append({"id": str(id_value), "url": url})

    now = datetime.now(timezone.utc).isoformat()
    linkages: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"candidates": 0, "ambiguous": 0, "no_lake_id": 0}
    for artist in universe:
        artist_key = artist["artist_key"]
        if not artist_key.startswith("mbid::"):
            continue
        name = artist.get("artist_name") or ""
        norm = normalize_name(name)
        matches = by_normalized.get(norm, [])
        if not matches:
            summary["no_lake_id"] += 1
            continue
        distinct_ids = {m["id"] for m in matches}
        if len(distinct_ids) > 1:
            summary["ambiguous"] += 1
            for m in matches:
                linkages.append(_linkage(
                    artist_key=artist_key, provider="SPOTIFY",
                    provider_id=m["id"], provider_url=m.get("url"),
                    link_method="LAKE_NAME_JOIN",
                    confidence=0.5,
                    resolution_status="AMBIGUOUS",
                    evidence_ref=m.get("url"),
                    notes=f"normalized name {norm!r} maps to multiple lake spotify ids",
                    first_seen_at=now,
                ))
            continue
        m = matches[0]
        summary["candidates"] += 1
        linkages.append(_linkage(
            artist_key=artist_key, provider="SPOTIFY",
            provider_id=m["id"], provider_url=m.get("url"),
            link_method="LAKE_NAME_JOIN",
            confidence=0.8,
            resolution_status="CANDIDATE",
            evidence_ref=m.get("url"),
            notes="name-join from lake spotify id; requires second source to verify",
            first_seen_at=now,
        ))
    return linkages, summary


# ---------------------------------------------------------------------------
# 2. Spotify search candidates (real API; candidates + ambiguity fail closed)
# ---------------------------------------------------------------------------

def search_spotify_candidates(
    conn,
    transport,
    *,
    universe: list[dict[str, Any]],
    client_id: str | None,
    client_secret: str | None,
    max_records: int = 10,
    include_catalog: bool = True,
) -> dict[str, Any]:
    """Real Spotify search → candidate resolutions + CANDIDATE linkages.

    Searches only artists WITHOUT an existing verified/candidate spotify
    linkage. Uses the existing deterministic ``spotify.classify`` (EXACT /
    HIGH_CONFIDENCE / AMBIGUOUS / NO_MATCH) and persists resolution rows to
    ``identity.spotify_artist_resolutions``. EXACT matches become CANDIDATE
    linkages (still not verified without a second source). No credentials →
    NOT_CONFIGURED, fails closed.
    """
    if not client_id or not client_secret:
        return {
            "status": "NOT_CONFIGURED",
            "detail": "SPOTIFY_CLIENT_ID/SECRET not set",
            "artists_eligible": 0,
            "rows_persisted": 0,
            "linkages": 0,
        }
    provider = SpotifyProvider(transport=transport)
    # env injection for the provider's secret() lookups
    import os

    provider.env = {
        **dict(os.environ),
        "SPOTIFY_CLIENT_ID": client_id,
        "SPOTIFY_CLIENT_SECRET": client_secret,
    }

    existing = conn.execute(
        """
        SELECT artist_key FROM identity.artist_provider_linkages
        WHERE provider = 'SPOTIFY' AND resolution_status IN ('VERIFIED', 'CANDIDATE')
        """
    ).fetchall()
    have_spotify = {r[0] for r in existing}

    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_eligible": 0,
        "artists_searched": 0,
        "resolutions_persisted": 0,
        "linkages_persisted": 0,
        "no_results": 0,
        "error": 0,
        "rate_limited": False,
        "snapshot_date": date.today().isoformat(),
    }
    now = datetime.now(timezone.utc).isoformat()
    linkage_rows: list[dict[str, Any]] = []
    for artist in universe:
        artist_key = artist["artist_key"]
        name = artist.get("artist_name") or ""
        if artist_key in have_spotify or not name.strip():
            continue
        summary["artists_eligible"] += 1
        req = AcquisitionRequest.new(
            entity_id=artist_key,
            entity_type="artist",
            platform="spotify",
            query=name,
            operation="SEARCH_ARTISTS",
            external_id=artist_key,
            max_records=max_records,
            commercial_context="research",
        )
        result = provider.acquire(req)
        if result.status.value == "RATE_LIMITED":
            summary["rate_limited"] = True
            summary["status"] = "RATE_LIMITED_STOPPED"
            break
        if result.status.value != "SUCCESS":
            summary["error"] += 1
            if result.status.value in ("NO_RESULTS",):
                summary["no_results"] += 1
            continue
        summary["artists_searched"] += 1
        candidates = [
            {
                "id": r.get("spotify_id"),
                "name": r.get("name"),
                "uri": r.get("uri"),
                "external_urls": r.get("external_urls") or {},
            }
            for r in result.records
            if r.get("spotify_id")
        ]
        resolved = classify(name, candidates)
        rows = []
        for r in resolved:
            rows.append({
                "resolution_key": hashlib.sha256(
                    f"{artist_key}|{r['spotify_id'] or 'NONE'}|{now}".encode("utf-8")
                ).hexdigest()[:32],
                "local_artist_name": name,
                "normalized_local_name": normalize_name(name),
                "source_table": artist_key,
                "spotify_id": r["spotify_id"],
                "spotify_name": r["spotify_name"],
                "spotify_uri": r["spotify_uri"],
                "spotify_url": r["spotify_url"],
                "resolution_status": r["resolution_status"],
                "match_method": "deterministic_normalized_name",
                "match_similarity": r["match_similarity"],
                "match_features": None,
                "retrieved_at": now,
                "knowledge_time": now,
                "rights_status": RIGHTS_STATUS,
                "commercial_use_status": COMMERCIAL_USE_STATUS,
                "software_version": SOFTWARE_VERSION,
            })
        summary["resolutions_persisted"] += persist_resolutions(conn, rows)
        summary["linkages_persisted"] += persist_exact_external_ids(conn, rows)
        for r in rows:
            if r["resolution_status"] == "EXACT" and r["spotify_id"]:
                linkage_rows.append(_linkage(
                    artist_key=artist_key, provider="SPOTIFY",
                    provider_id=r["spotify_id"], provider_url=r["spotify_url"],
                    link_method="PROVIDER_SEARCH_CANDIDATE",
                    confidence=0.85,
                    resolution_status="CANDIDATE",
                    evidence_ref=r["spotify_url"],
                    notes="EXACT normalized-name spotify search match; requires second source to verify",
                    first_seen_at=now,
                ))
            elif r["resolution_status"] == "AMBIGUOUS" and r["spotify_id"]:
                linkage_rows.append(_linkage(
                    artist_key=artist_key, provider="SPOTIFY",
                    provider_id=r["spotify_id"], provider_url=r["spotify_url"],
                    link_method="PROVIDER_SEARCH_CANDIDATE",
                    confidence=0.4,
                    resolution_status="AMBIGUOUS",
                    evidence_ref=r["spotify_url"],
                    notes="ambiguous spotify search match; failed closed",
                    first_seen_at=now,
                ))
    persist = persist_linkages(conn, linkage_rows)
    summary["linkages_persisted"] += persist["inserted"]
    summary["status"] = "COMPLETE"
    return summary


# ---------------------------------------------------------------------------
# 3. Catalog collection (albums/singles/release dates) — not demand
# ---------------------------------------------------------------------------

CATALOG_RELEASE_FIELDS = (
    "id", "name", "album_type", "release_date", "release_date_precision",
    "total_tracks", "external_urls", "uri", "type",
)


def _albums_for_artist(conn, transport, *, artist_key: str, spotify_id: str,
                       token: str) -> tuple[list[dict[str, Any]], str | None]:
    from ..acquisition.transport import TransportError

    url = f"https://api.spotify.com/v1/artists/{spotify_id}/albums?limit=50&include_groups=album,single"
    try:
        response = transport.request(
            "GET", url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout_seconds=30.0,
        )
    except TransportError as exc:
        return [], f"network:{exc}"
    if response.status == 429:
        return [], "rate_limited"
    if response.status != 200:
        return [], f"http_{response.status}"
    try:
        payload = response.json()
    except ValueError:
        return [], "schema_invalid"
    return (payload.get("items") or []), None


def _catalog_observation(
    *,
    artist_key: str,
    spotify_id: str,
    day: str,
    retrieved_at: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    present = sorted(f for f in CATALOG_RELEASE_FIELDS if f in item)
    release_date = (item.get("release_date") or "")[:10] or None
    obs_key = hashlib.sha256(
        f"{artist_key}|spotify|catalog_release|{spotify_id}|{item.get('id')}|{day}|{SOFTWARE_VERSION}".encode("utf-8")
    ).hexdigest()[:32]
    return {
        "observation_key": obs_key,
        "artist_key": artist_key,
        "festival_key": None,
        "edition_key": None,
        "edition_year": None,
        "source_system": "spotify",
        "metric_kind": "SPOTIFY_CATALOG_RELEASE",
        "project": None,
        "access_method": "public_api",
        "agent": "user",
        "article_title": item.get("name"),
        "granularity": "release",
        "period_start": release_date,
        "period_end": release_date,
        "value": item.get("total_tracks"),
        "value_sum": item.get("total_tracks"),
        "value_unit": "tracks",
        "status": "ok",
        "error_code": None,
        "error_message": None,
        "source_url": (item.get("external_urls") or {}).get("spotify"),
        "retrieved_at": retrieved_at,
        "raw_response_json": json.dumps(item, default=str),
        "provenance_json": json.dumps({
            "source_system": "spotify",
            "endpoint": "artists/{id}/albums",
            "api_mode": "dev_mode",
            "fields_present": present,
            "album_type": item.get("album_type"),
            "release_date_precision": item.get("release_date_precision"),
            "semantics": "CATALOG_STRUCTURE_ONLY; never a demand factor; never used for ML training",
        }, default=str),
        "metric_version": SOFTWARE_VERSION,
    }


def persist_catalog_observation(conn, row: dict[str, Any]) -> int:
    exists = conn.execute(
        "SELECT 1 FROM metrics.artist_attention_observations WHERE observation_key = ?",
        [row["observation_key"]],
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO metrics.artist_attention_observations
            (observation_key, artist_key, festival_key, edition_key, edition_year,
             source_system, metric_kind, project, access_method, agent, article_title,
             granularity, period_start, period_end, value, value_sum, value_unit,
             status, error_code, error_message, source_url, retrieved_at,
             raw_response_json, provenance_json, metric_version, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            row["observation_key"], row["artist_key"], row["festival_key"], row["edition_key"],
            row["edition_year"], row["source_system"], row["metric_kind"], row["project"],
            row["access_method"], row["agent"], row["article_title"], row["granularity"],
            row["period_start"], row["period_end"], row["value"], row["value_sum"],
            row["value_unit"], row["status"], row["error_code"], row["error_message"],
            row["source_url"], row["retrieved_at"], row["raw_response_json"],
            row["provenance_json"], row["metric_version"],
        ],
    )
    return 1


def collect_catalog_for_universe(
    conn,
    transport,
    *,
    universe: list[dict[str, Any]],
    client_id: str | None,
    client_secret: str | None,
) -> dict[str, Any]:
    """Read album/single catalog for every artist with a Spotify linkage.

    Catalog structure only: release dates, album vs single, total tracks.
    No popularity/followers, no demand proxy. Fails closed without creds.
    """
    if not client_id or not client_secret:
        return {
            "status": "NOT_CONFIGURED",
            "detail": "SPOTIFY_CLIENT_ID/SECRET not set",
            "artists_eligible": 0,
            "rows_persisted": 0,
        }
    from ..attention.spotify_catalog import _get_token

    token = _get_token(transport, client_id, client_secret)
    if token is None:
        return {
            "status": "NOT_CONFIGURED",
            "detail": "spotify token exchange failed",
            "artists_eligible": 0,
            "rows_persisted": 0,
        }
    rows = conn.execute(
        """
        SELECT artist_key, provider_id
        FROM identity.artist_provider_linkages
        WHERE provider = 'SPOTIFY' AND resolution_status = 'VERIFIED'
        """
    ).fetchall()
    verified = {r[0]: r[1] for r in rows}
    # also allow CANDIDATE lake-name joins (they carry a real catalog id)
    cand = conn.execute(
        """
        SELECT artist_key, provider_id
        FROM identity.artist_provider_linkages
        WHERE provider = 'SPOTIFY' AND resolution_status = 'CANDIDATE'
          AND link_method = 'LAKE_NAME_JOIN'
        """
    ).fetchall()
    for artist_key, pid in cand:
        verified.setdefault(artist_key, pid)

    day = date.today().isoformat()
    retrieved_at = utc_now().isoformat()
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_eligible": len(verified),
        "artists_with_catalog": 0,
        "releases_persisted": 0,
        "rate_limited": False,
        "errors": 0,
        "snapshot_date": day,
    }
    for artist_key, spotify_id in sorted(verified.items()):
        items, err = _albums_for_artist(conn, transport, artist_key=artist_key,
                                        spotify_id=spotify_id, token=token)
        if err == "rate_limited":
            summary["rate_limited"] = True
            summary["status"] = "RATE_LIMITED_STOPPED"
            break
        if err:
            summary["errors"] += 1
            continue
        if not items:
            continue
        summary["artists_with_catalog"] += 1
        for item in items:
            row = _catalog_observation(
                artist_key=artist_key, spotify_id=spotify_id,
                day=day, retrieved_at=retrieved_at, item=item,
            )
            summary["releases_persisted"] += persist_catalog_observation(conn, row)
    summary["status"] = "COMPLETE"
    return summary


def run_spotify_identity(
    conn,
    transport,
    *,
    universe: list[dict[str, Any]],
    client_id: str | None,
    client_secret: str | None,
    include_catalog: bool = True,
) -> dict[str, Any]:
    """Full P4 pass: lake repair → search candidates → catalog."""
    lake_linkages, lake_summary = lake_spotify_candidates(conn, universe=universe)
    lake_persist = persist_linkages(conn, lake_linkages)
    search = search_spotify_candidates(
        conn, transport, universe=universe,
        client_id=client_id, client_secret=client_secret,
    )
    catalog: dict[str, Any] = {"status": "SKIPPED"}
    if include_catalog:
        catalog = collect_catalog_for_universe(
            conn, transport, universe=universe,
            client_id=client_id, client_secret=client_secret,
        )
    return {
        "status": "COMPLETE",
        "lake_name_join": {**lake_summary, "persisted": lake_persist},
        "search_candidates": search,
        "catalog": catalog,
        "software_version": SOFTWARE_VERSION,
    }
