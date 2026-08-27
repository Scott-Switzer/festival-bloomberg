"""Spotify CONSERVATIVE catalog adapter — identity + catalog evidence only.

Milestone OPEN_ARTIST_MARKET_DATA_V1 — SOURCE 5.

Spotify's 2026 Development Mode removed artist followers/popularity and bulk
artist endpoints, and Spotify prohibits using its content to train ML. This
module therefore NEVER builds core demand factors from Spotify. It records:

* identity/catalog metadata the API still returns (artist id, uri, external
  urls, genres when present, popularity when present — with the field's
  presence explicitly recorded so downstream code cannot build around a
  removed field);
* the API MODE actually observed (DEVELOPMENT / EXTENDED_QUOTA / UNKNOWN) so
  the rights/commercial state travels with the row.

Each artist yields one ``metrics.artist_attention_observations`` row
(metric_kind ``SPOTIFY_CATALOG_IDENTITY``) with the JSON payload in
``raw_response_json`` and the mode + fields_present in provenance. No key →
NOT_CONFIGURED, fails closed, never a fabricated value.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from ..acquisition.contracts import utc_now
from ..identity.spotify import normalize_name

SOURCE_SYSTEM = "spotify"
METRIC_VERSION = "spotify_catalog_identity_v1"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

#: Fields the 2026 API may or may not return — presence is recorded per row.
CATALOG_FIELDS = (
    "id", "name", "uri", "external_urls", "images", "type",
    "genres", "popularity", "followers",
)


def artist_key_for(name: str) -> str:
    return f"name::{normalize_name(name)}"


def catalog_key(*, artist_key: str, spotify_id: str, day: str) -> str:
    material = "|".join([artist_key, SOURCE_SYSTEM, "catalog_identity", spotify_id, day, METRIC_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def infer_api_mode(payload: dict[str, Any] | None, http_status: int | None) -> str:
    """Record the observed API mode.

    Development Mode commonly surfaces quota/scope errors with specific
    markers; EXTENDED_QUOTA is reported by Meta-style APIs via headers. We are
    conservative: without positive evidence we record UNKNOWN, and we never
    assume EXTENDED_QUOTA.
    """
    if payload is None:
        if http_status == 401 or http_status == 403:
            return "DEVELOPMENT_OR_SCOPE_LIMITED"
        return "UNKNOWN"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        msg = error["message"].lower()
        if "extended quota" in msg:
            return "EXTENDED_QUOTA"
        if "development mode" in msg:
            return "DEVELOPMENT"
    if http_status == 401 or http_status == 403:
        return "DEVELOPMENT_OR_SCOPE_LIMITED"
    return "UNKNOWN"


def build_catalog_observation(
    *,
    artist_name: str,
    artist_key: str,
    spotify_id: str,
    day: str,
    retrieved_at: str,
    status: str,
    raw_payload: dict[str, Any] | None,
    api_mode: str,
    source_url: str,
) -> dict[str, Any]:
    fields_present: list[str] = []
    if raw_payload:
        fields_present = sorted(f for f in CATALOG_FIELDS if f in raw_payload)
    return {
        "observation_key": catalog_key(artist_key=artist_key, spotify_id=spotify_id, day=day),
        "artist_key": artist_key,
        "festival_key": None,
        "edition_key": None,
        "edition_year": None,
        "source_system": SOURCE_SYSTEM,
        "metric_kind": "SPOTIFY_CATALOG_IDENTITY",
        "project": None,
        "access_method": "public_api",
        "agent": "user",
        "article_title": artist_name.strip() or None,
        "granularity": "daily",
        "period_start": day,
        "period_end": day,
        "value": None,
        "value_sum": None,
        "value_unit": None,
        "status": status,
        "error_code": None if status == "ok" else status,
        "error_message": None,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "raw_response_json": json.dumps(raw_payload, default=str) if raw_payload is not None else None,
        "provenance_json": json.dumps({
            "source_system": SOURCE_SYSTEM,
            "endpoint": "artists/{id}",
            "api_mode": api_mode,
            "fields_present": fields_present,
            "semantics": "IDENTITY_AND_CATALOG_ONLY; never a demand factor; "
                         "never used for ML training per Spotify terms",
        }, default=str),
        "metric_version": METRIC_VERSION,
    }


def persist_catalog(conn, row: dict[str, Any]) -> int:
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


def _spotify_get(transport, url: str, token: str) -> tuple[int, dict[str, Any] | None]:
    from ..acquisition.transport import TransportError

    try:
        response = transport.request(
            "GET", url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    except TransportError as exc:
        return -1, {"error": {"message": str(exc)}}
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response.status, payload


def _get_token(transport, client_id: str | None, client_secret: str | None) -> str | None:
    import base64
    from urllib.parse import urlencode

    if not client_id or not client_secret:
        return None
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    try:
        response = transport.request(
            "POST", "https://accounts.spotify.com/api/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
        )
    except Exception:
        return None
    if response.status != 200:
        return None
    try:
        return response.json().get("access_token")
    except ValueError:
        return None


def collect_artist_catalog(
    conn,
    transport,
    *,
    artists: list[dict[str, Any]],
    spotify_id_by_key: dict[str, str] | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    """Conservative Spotify catalog/identity collection.

    ``artists`` are dicts with ``artist_name`` + ``artist_key``.
    ``spotify_id_by_key`` maps canonical artist_key → spotify id (from
    core.artists.spotify_id / entity_external_ids) — artists without an id are
    recorded as ``missing`` (never fabricated).

    Returns a summary. A missing/invalid credential is NOT_CONFIGURED and is
    not an application failure.
    """
    if not client_id or not client_secret:
        return {
            "status": "NOT_CONFIGURED",
            "detail": "SPOTIFY_CLIENT_ID/SECRET not set — identity+catalog only, skipped",
            "artists_eligible": len(artists),
            "rows_persisted": 0,
        }
    token = _get_token(transport, client_id, client_secret)
    if token is None:
        return {
            "status": "NOT_CONFIGURED",
            "detail": "token exchange failed with configured credentials",
            "artists_eligible": len(artists),
            "rows_persisted": 0,
        }
    day = snapshot_date or date.today().isoformat()
    retrieved_at = utc_now().isoformat()
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_eligible": len(artists),
        "artists_resolved": 0,
        "artists_missing": 0,
        "artists_error": 0,
        "rows_persisted": 0,
        "api_modes": {},
        "snapshot_date": day,
    }
    for artist in artists:
        name = (artist.get("artist_name") or "").strip()
        artist_key = artist.get("artist_key") or artist_key_for(name)
        spotify_id = (spotify_id_by_key or {}).get(artist_key)
        if not spotify_id:
            summary["artists_missing"] += 1
            row = build_catalog_observation(
                artist_name=name, artist_key=artist_key, spotify_id="", day=day,
                retrieved_at=retrieved_at, status="missing", raw_payload=None,
                api_mode="UNKNOWN", source_url="",
            )
            summary["rows_persisted"] += persist_catalog(conn, row)
            continue
        url = f"{SPOTIFY_API_BASE}/artists/{spotify_id}"
        http_status, payload = _spotify_get(transport, url, token)
        mode = infer_api_mode(payload, http_status)
        summary["api_modes"][mode] = summary["api_modes"].get(mode, 0) + 1
        if http_status == 404:
            summary["artists_missing"] += 1
            status = "missing"
        elif http_status == 200 and payload:
            summary["artists_resolved"] += 1
            status = "ok"
        elif http_status in (401, 403):
            summary["status"] = "NOT_CONFIGURED"
            summary["detail"] = f"spotify returned http {http_status} ({mode})"
            break
        elif http_status == 429:
            summary["status"] = "RATE_LIMITED_STOPPED"
            summary["detail"] = "spotify rate limited"
            break
        else:
            summary["artists_error"] += 1
            status = "error"
        row = build_catalog_observation(
            artist_name=name, artist_key=artist_key, spotify_id=spotify_id, day=day,
            retrieved_at=retrieved_at, status=status, raw_payload=payload,
            api_mode=mode, source_url=url,
        )
        summary["rows_persisted"] += persist_catalog(conn, row)
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    return summary
