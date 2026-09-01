"""Identity Graph V2: deterministic, fail-closed provider identity evidence.

The builder never writes the canonical warehouse.  It accepts rows already
read from ``core.artists``, ``core.entity_external_ids`` and
``identity.artist_provider_linkages`` plus optional Wikidata generation rows.
Every source row remains evidence; only the derived edge status is reduced.
The broader canonical scope is deliberately an in-memory/read-only result;
callers must establish an explicit memory/disk budget before materializing it.

No name-only merge is performed.  A provider ID shared by artists, or more
than one provider ID for one artist/provider, is never promoted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

RUN_VERSION = "identity_graph_v2"
STATUSES = (
    "VERIFIED_EXACT", "SUPPORTED_MULTI_SOURCE", "CANDIDATE",
    "AMBIGUOUS", "CONFLICT", "MISSING",
)
CANONICAL_SCOPE = "CANONICAL_25K"
BROAD_SCOPE = "BROADER_CANONICAL"
DEFAULT_RIGHTS = "TERMS_REVIEW_REQUIRED"
DEFAULT_COMMERCIAL = "PROTOTYPE_ONLY"
DEFAULT_KNOWLEDGE_BASIS = "SOURCE_ROW_KNOWLEDGE_TIME_ONLY"
DEFAULT_MAX_ARTISTS = 25_000
DEFAULT_MAX_EVIDENCE = 500_000
DEFAULT_MAX_EDGES = 250_000
DEFAULT_MAX_WIKIDATA_ROWS = 250_000
DEFAULT_MAX_WIKIDATA_BYTES = 2_000_000_000
_RIGHTS_ORDER = {
    "RIGHTS_BLOCKED": 0, "TERMS_REVIEW_REQUIRED": 1,
    "OPEN_WITH_ATTRIBUTION": 2, "OPEN_DATA_AGGREGATED": 3,
    "OPEN_COMMERCIAL_OK": 4, "CC0": 4,
}
_COMMERCIAL_ORDER = {
    "DISALLOWED": 0, "TERMS_REVIEW_REQUIRED": 1,
    "RESEARCH_ONLY": 2, "PROTOTYPE_ONLY": 3, "ALLOWED": 4,
    "OPEN_COMMERCIAL_OK": 4,
}

# Existing providers are retained in the V2 graph, even where coverage is
# sparse.  Provider names are normalized to uppercase in all outputs.
PROVIDERS = (
    "MUSICBRAINZ", "WIKIDATA", "YOUTUBE", "SPOTIFY", "DISCOGS", "ISNI",
    "VIAF", "TICKETMASTER", "OFFICIAL_WEBSITE", "LISTENBRAINZ",
    "WIKIPEDIA", "SOUNDCLOUD", "APPLE_MUSIC", "BANDCAMP", "SONGKICK",
    "BANDSINTOWN", "SETLISTFM", "ALLMUSIC", "LASTFM", "MYSPACE", "IPI",
)

ID_TYPE_TO_PROVIDER = {
    "musicbrainz": "MUSICBRAINZ", "mbid": "MUSICBRAINZ",
    "wikidata": "WIKIDATA", "qid": "WIKIDATA",
    "youtube": "YOUTUBE", "youtube_channel": "YOUTUBE", "youtube_channel_id": "YOUTUBE",
    "spotify": "SPOTIFY", "spotify_artist": "SPOTIFY", "spotify_artist_id": "SPOTIFY",
    "discogs": "DISCOGS", "discogs_artist": "DISCOGS", "discogs_artist_id": "DISCOGS", "isni": "ISNI",
    "viaf": "VIAF", "ticketmaster": "TICKETMASTER",
    "official_website": "OFFICIAL_WEBSITE", "official_homepage": "OFFICIAL_WEBSITE",
    "website": "OFFICIAL_WEBSITE",
    "listenbrainz": "LISTENBRAINZ", "wikipedia": "WIKIPEDIA",
    "soundcloud": "SOUNDCLOUD", "apple_music": "APPLE_MUSIC",
    "bandcamp": "BANDCAMP", "songkick": "SONGKICK",
    "bandsintown": "BANDSINTOWN", "setlistfm": "SETLISTFM",
    "allmusic": "ALLMUSIC", "lastfm": "LASTFM", "myspace": "MYSPACE", "ipi": "IPI",
}
WIKIDATA_PROPERTY_TO_PROVIDER = {
    "P434": "MUSICBRAINZ", "P1902": "SPOTIFY", "P2397": "YOUTUBE",
    "P1953": "DISCOGS", "P213": "ISNI", "P214": "VIAF",
    "P856": "OFFICIAL_WEBSITE",
}

PROVIDER_COLUMNS = {
    "musicbrainz_id": "MUSICBRAINZ", "wikidata_id": "WIKIDATA",
    "youtube_channel_id": "YOUTUBE", "spotify_id": "SPOTIFY",
    "discogs_id": "DISCOGS", "isni": "ISNI", "viaf_id": "VIAF",
    "ticketmaster_id": "TICKETMASTER", "official_website": "OFFICIAL_WEBSITE",
    "official_homepage": "OFFICIAL_WEBSITE",
    "ipi": "IPI",
    "listenbrainz_id": "LISTENBRAINZ", "wikipedia_id": "WIKIPEDIA",
    "soundcloud_id": "SOUNDCLOUD", "apple_music_id": "APPLE_MUSIC",
    "bandcamp_id": "BANDCAMP", "songkick_id": "SONGKICK",
    "bandsintown_id": "BANDSINTOWN", "setlistfm_id": "SETLISTFM",
}

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_QID = re.compile(r"^Q[1-9][0-9]*$", re.I)
_YOUTUBE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_SPOTIFY = re.compile(r"^[A-Za-z0-9]{22}$")
_DIGITS = re.compile(r"^[0-9]+$")
_TICKETMASTER = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
_ISNI = re.compile(r"^[0-9]{15}[0-9X]$")
_RFC3339_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)$"
)


@dataclass(frozen=True)
class Evidence:
    artist_key: str
    provider: str
    provider_id: str
    source_table: str
    source_ref: str | None = None
    source_url: str | None = None
    evidence_kind: str = "EXACT_EXTERNAL_ID"
    candidate: bool = False
    trusted: bool = False
    claimed_status: str | None = None
    source_system: str | None = None
    source_version: str | None = None
    source_checksum: str | None = None
    retrieved_at: str | None = None
    rights_status: str = DEFAULT_RIGHTS
    commercial_use_status: str = DEFAULT_COMMERCIAL
    knowledge_time: str | None = None
    payload: Mapping[str, Any] | None = None
    trust_class: str = "CANDIDATE"
    resolution_basis: str = "UNTRUSTED_SOURCE_STATUS"

    def key(self) -> str:
        return stable_key("evidence", asdict(self))


def stable_key(prefix: str, value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()}"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _provider(value: Any) -> str | None:
    value = _text(value)
    return value.upper() if value else None


def _as_of(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_utc(value: Any) -> datetime | None:
    text = _text(value)
    if not text or not _RFC3339_UTC.fullmatch(text):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


_DUCKDB_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?$"
)


def _as_rfc3339_utc(value: Any) -> str | None:
    """Normalize a knowledge time to RFC3339 UTC text.

    Accepts datetime objects (DuckDB TIMESTAMP columns return naive
    ``datetime.datetime`` values whose ``str()`` is ``YYYY-MM-DD HH:MM:SS``
    rather than RFC3339), DuckDB-style timestamp strings, and RFC3339 UTC
    strings.  Timezone-naive values are interpreted as UTC, matching the
    pipeline-wide ingestion convention.  Any other value returns None so the
    fail-closed knowledge-time gate can reject it.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    text = _text(value)
    if not text:
        return None
    if _RFC3339_UTC.fullmatch(text):
        return text
    if _DUCKDB_TIMESTAMP.fullmatch(text):
        return text.replace(" ", "T", 1) + "Z"
    return None


def _timestamp_text(value: Any) -> str | None:
    """Best-effort RFC3339 text for audit fields; never drops a non-empty value."""
    normalized = _as_rfc3339_utc(value)
    if normalized is not None:
        return normalized
    return _text(value)


def _knowledge_time_text(value: Any) -> str | None:
    """Knowledge-time text that preserves invalid values for fail-closed review.

    Absent knowledge (None) returns None and is admitted without a PIT cutoff.
    A value that cannot be normalized to RFC3339 UTC returns its raw text so
    the caller's knowledge-time gate rejects it as INVALID_KNOWLEDGE_TIME rather
    than silently treating a malformed timestamp as "no knowledge time".
    """
    if value is None:
        return None
    normalized = _as_rfc3339_utc(value)
    if normalized is not None:
        return normalized
    return _text(value)


def _conservative(values: Iterable[str | None], *, rights: bool) -> str:
    default = DEFAULT_RIGHTS if rights else DEFAULT_COMMERCIAL
    order = _RIGHTS_ORDER if rights else _COMMERCIAL_ORDER
    clean = [_text(value) for value in values]
    # Unknown/missing evidence is never permission to relax a restrictive
    # claim; explicit blocked/disallowed values dominate all other values.
    if rights and "RIGHTS_BLOCKED" in clean:
        return "RIGHTS_BLOCKED"
    if not rights and "DISALLOWED" in clean:
        return "DISALLOWED"
    if not clean or any(value not in order for value in clean if value is not None):
        return default
    return min((value for value in clean if value is not None), key=lambda value: order[value], default=default)


def _minimal_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Bound audit payloads; never duplicate full source rows in every edge."""
    allowed = (
        "id_type", "id_value", "provider", "provider_id", "resolution_status",
        "resolution_method", "link_method", "entity_type", "entity_key",
        "artist_key", "source", "source_system", "evidence_ref", "source_ref",
        "knowledge_time", "ingested_at", "last_verified_at", "rights_status", "commercial_use_status",
    )
    return {key: row[key] for key in allowed if key in row and row[key] is not None}


def normalize_provider_id(provider: str, value: Any) -> str | None:
    """Normalize only lossless provider-ID formatting; reject malformed IDs."""
    provider = provider.upper()
    value = _text(value)
    if not value:
        return None
    if provider in {"ISNI"}:
        value = re.sub(r"[ -]", "", value).upper()
    if provider == "OFFICIAL_WEBSITE":
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return value.rstrip("/")
    if provider in {"MUSICBRAINZ", "LISTENBRAINZ"}:
        return value.lower() if _UUID.fullmatch(value) else None
    if provider == "WIKIDATA":
        return value.upper() if _QID.fullmatch(value) else None
    if provider == "YOUTUBE":
        return value if _YOUTUBE.fullmatch(value) else None
    if provider == "SPOTIFY":
        return value if _SPOTIFY.fullmatch(value) else None
    if provider == "ISNI":
        return value if _ISNI.fullmatch(value) else None
    if provider in {"DISCOGS", "VIAF", "APPLE_MUSIC", "SONGKICK"}:
        return value if _DIGITS.fullmatch(value) else None
    if provider in {"TICKETMASTER", "SETLISTFM"}:
        return value if _TICKETMASTER.fullmatch(value) else None
    if provider in {"WIKIPEDIA", "SOUNDCLOUD", "BANDCAMP", "BANDSINTOWN"}:
        return value if 1 <= len(value) <= 256 and "\n" not in value else None
    return value if len(value) <= 256 else None


def _row_ref(row: Mapping[str, Any], fallback: str) -> str:
    for name in ("evidence_ref", "source_ref", "evidence_url", "source_url", "url", "external_id_key", "linkage_key", "id_value"):
        value = _text(row.get(name))
        if value:
            return value
    return fallback


def _evidence_from_row(
    row: Mapping[str, Any], *, source_table: str, artist_key: str,
    provider: str, provider_id: Any, evidence_kind: str, candidate: bool = False,
) -> tuple[Evidence | None, str | None]:
    normalized = normalize_provider_id(provider, provider_id)
    if not normalized:
        return None, _text(provider_id)
    claimed_status = _text(row.get("resolution_status"))
    claimed_upper = (claimed_status or "").upper()
    native_mbid = (
        provider == "MUSICBRAINZ"
        and source_table == "core.artists"
        and normalize_provider_id("MUSICBRAINZ", row.get("musicbrainz_id")) == normalized
    )
    source_system = _text(row.get("source_system") or row.get("source"))
    source_lower = (source_system or "").lower().replace("_", "-")
    crowd_curated = source_lower in {
        "crowd", "crowd-curated", "crowdsourced", "community", "community-curated",
    } or "crowd" in source_lower
    knowledge_time = _knowledge_time_text(
        row.get("knowledge_time") or row.get("last_verified_at") or row.get("ingested_at")
    )
    trusted = (native_mbid or (knowledge_time is not None and claimed_upper in {
        "API_VERIFIED", "VERIFIED", "VERIFIED_EXACT", "EXACT", "TRUSTED", "CANONICAL"
    })) and not crowd_curated and not candidate
    if native_mbid:
        trust_class, resolution_basis = "CANONICAL_NATIVE", "CANONICAL_NATIVE_MBID"
    elif crowd_curated or claimed_upper == "CROWD_CURATED_REFERENCE":
        trust_class, resolution_basis = "CURATED_REFERENCE", "CURATED_REFERENCE_NOT_EXACT"
    elif source_table == "core.entity_external_ids" and provider == "MUSICBRAINZ":
        trust_class, resolution_basis = "MB_OFFICIAL_LINK", "MUSICBRAINZ_OFFICIAL_REFERENCE"
    elif source_table == "wikidata_generation_parquet":
        trust_class, resolution_basis = "WIKIDATA_LINK", "WIKIDATA_PROPERTY_LINK"
    elif claimed_upper == "API_VERIFIED":
        trust_class, resolution_basis = "API_VERIFIED", "EXPLICIT_API_VERIFIED_STATUS"
    elif source_table == "identity.artist_provider_linkages" and trusted:
        trust_class, resolution_basis = "VERIFIED_LINKAGE", "EXPLICIT_TRUSTED_OR_EXACT_STATUS"
    elif trusted:
        trust_class, resolution_basis = "SOURCE_VERIFIED", "EXPLICIT_TRUSTED_OR_EXACT_STATUS"
    else:
        trust_class, resolution_basis = "CANDIDATE", "SOURCE_NOT_TRUSTED"
    return Evidence(
        artist_key=artist_key, provider=provider, provider_id=normalized,
        source_table=source_table,
        source_ref=_row_ref(row, f"{source_table}:{artist_key}:{normalized}"),
        source_url=_text(row.get("url") or row.get("provider_url") or row.get("evidence_url")),
        evidence_kind=evidence_kind,
        candidate=candidate or not trusted or claimed_upper in {"CANDIDATE", "AMBIGUOUS"},
        trusted=trusted,
        claimed_status=claimed_status,
        source_system=source_system,
        source_version=_text(row.get("source_version")),
        source_checksum=_text(row.get("source_checksum") or row.get("checksum") or row.get("sha256")),
        retrieved_at=_timestamp_text(row.get("retrieved_at") or row.get("source_retrieved_at")),
        rights_status=_text(row.get("rights_status")) or DEFAULT_RIGHTS,
        commercial_use_status=_text(row.get("commercial_use_status")) or DEFAULT_COMMERCIAL,
        knowledge_time=knowledge_time,
        payload=_minimal_payload(row),
        trust_class=trust_class, resolution_basis=resolution_basis,
    ), None


def _invalid_record(
    row: Mapping[str, Any], *, source_table: str, artist_key: str,
    provider: str, provider_id: Any,
) -> dict[str, Any]:
    """Retain rejected claims for audit without admitting them as edges."""
    raw = _text(provider_id) or "<NULL>"
    return {
        "artist_key": artist_key, "provider": provider, "provider_id": raw,
        "source_table": source_table,
        "claimed_status": _text(row.get("resolution_status")),
        "source_ref": _row_ref(row, f"{source_table}:{artist_key}:{raw}"),
        "source_url": _text(row.get("url") or row.get("provider_url") or row.get("evidence_url")),
        "source_system": _text(row.get("source_system") or row.get("source")),
        "source_version": _text(row.get("source_version")),
        "source_checksum": _text(row.get("source_checksum") or row.get("checksum") or row.get("sha256")),
        "retrieved_at": _timestamp_text(row.get("retrieved_at") or row.get("source_retrieved_at")),
        "rights_status": _text(row.get("rights_status")) or DEFAULT_RIGHTS,
        "commercial_use_status": _text(row.get("commercial_use_status")) or DEFAULT_COMMERCIAL,
        "knowledge_time": _knowledge_time_text(
            row.get("knowledge_time") or row.get("last_verified_at") or row.get("ingested_at")
        ),
        "payload_json": _minimal_payload(row),
    }


def _discarded_record(
    row: Mapping[str, Any], *, source_table: str, artist_key: str | None,
    provider: str, provider_id: Any, reason: str,
) -> dict[str, Any]:
    raw = _text(provider_id) or "<NULL>"
    return {
        "artist_key": artist_key, "provider": provider or "UNKNOWN_PROVIDER",
        "provider_id": raw, "source_table": source_table, "reason": reason,
        "source_ref": _row_ref(row, f"{source_table}:{artist_key or '<unknown>'}:{raw}"),
        "source_url": _text(row.get("url") or row.get("provider_url") or row.get("evidence_url")),
        "source_system": _text(row.get("source_system") or row.get("source")),
        "source_version": _text(row.get("source_version")),
        "source_checksum": _text(row.get("source_checksum") or row.get("checksum") or row.get("sha256")),
        "retrieved_at": _timestamp_text(row.get("retrieved_at") or row.get("source_retrieved_at")),
        "rights_status": _text(row.get("rights_status")) or DEFAULT_RIGHTS,
        "commercial_use_status": _text(row.get("commercial_use_status")) or DEFAULT_COMMERCIAL,
        "knowledge_time": _knowledge_time_text(
            row.get("knowledge_time") or row.get("last_verified_at") or row.get("ingested_at")
        ),
        "payload_json": _minimal_payload(row),
    }


def _canonical_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for original in rows:
        row = dict(original)
        key = _text(row.get("artist_key") or row.get("entity_key"))
        if key:
            result[key].append(row)
    return [
        min(result[key], key=lambda row: json.dumps(row, sort_keys=True, default=str))
        for key in sorted(result)
    ]


def _scope_rows(
    rows: Sequence[Mapping[str, Any]], estate_rows: Sequence[Mapping[str, Any]],
    canonical_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select membership only from the explicit governed estate, never sorting."""
    estate_by_key: dict[str, dict[str, Any]] = {}
    for estate in estate_rows:
        key = _text(estate.get("artist_key") or estate.get("key"))
        if not key:
            raise ValueError("estate artist entries require artist_key/key")
        if key in estate_by_key and json.dumps(estate_by_key[key], sort_keys=True, default=str) != json.dumps(estate, sort_keys=True, default=str):
            raise ValueError(f"estate has conflicting duplicate artist key {key}")
        estate_by_key[key] = dict(estate)
    if len(estate_by_key) != canonical_limit:
        raise ValueError(f"governed estate has {len(estate_by_key)} artists; expected canonical_limit={canonical_limit}")
    row_by_key = {str(r.get("artist_key")): dict(r) for r in rows}
    missing = sorted(set(estate_by_key) - set(row_by_key))
    if missing:
        raise ValueError(f"governed estate artists missing from core.artists: {missing[:5]}")
    narrow: list[dict[str, Any]] = []
    for key in sorted(estate_by_key):
        row = dict(row_by_key[key])
        row["estate_tier"] = _text(estate_by_key[key].get("tier"))
        narrow.append(row)
    broad = [dict(r) for r in rows if str(r.get("artist_key")) not in estate_by_key]
    return narrow, broad


def _collect_evidence(
    artists: Sequence[Mapping[str, Any]], external_ids: Iterable[Mapping[str, Any]],
    linkages: Iterable[Mapping[str, Any]], wikidata_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[Evidence], list[dict[str, Any]], list[dict[str, Any]]]:
    artist_by_mbid = {
        normalized: _text(r.get("artist_key"))
        for r in artists
        if (normalized := normalize_provider_id("MUSICBRAINZ", r.get("musicbrainz_id")))
    }
    wikidata_rows = list(wikidata_rows)
    qid_to_mbids: dict[str, set[str]] = defaultdict(set)
    qid_mbid_has_pit_time: dict[tuple[str, str], bool] = defaultdict(bool)
    for row in wikidata_rows:
        qid = normalize_provider_id("WIKIDATA", row.get("qid") or row.get("wikidata_id") or row.get("QID"))
        property_name = (_text(row.get("external_id_property") or row.get("property")) or "").upper()
        property_value = _text(row.get("external_id_value"))
        direct_mbid = normalize_provider_id(
            "MUSICBRAINZ",
            row.get("P434") or row.get("p434") or row.get("musicbrainz_id") or row.get("mbid"),
        )
        property_mbid = normalize_provider_id("MUSICBRAINZ", property_value) if property_name == "P434" else None
        if qid and (direct_mbid or property_mbid):
            joined_mbid = direct_mbid or property_mbid
            qid_to_mbids[qid].add(joined_mbid)
            qid_mbid_has_pit_time[(qid, joined_mbid)] = (
                qid_mbid_has_pit_time[(qid, joined_mbid)]
                or _as_rfc3339_utc(
                    row.get("knowledge_time") or row.get("last_verified_at") or row.get("ingested_at")
                ) is not None
            )
    qid_to_mbid = {qid: next(iter(mbids)) for qid, mbids in qid_to_mbids.items() if len(mbids) == 1}
    qid_join_has_pit_time = {
        qid: qid_mbid_has_pit_time[(qid, mbid)] for qid, mbid in qid_to_mbid.items()
    }
    evidence: list[Evidence] = []
    invalid: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    known_artists = {str(r.get("artist_key")) for r in artists}
    for row in artists:
        artist_key = _text(row.get("artist_key"))
        if not artist_key:
            continue
        for column, provider in PROVIDER_COLUMNS.items():
            value = row.get(column)
            if value is None and provider == "OFFICIAL_WEBSITE":
                value = row.get("official_website")
            if value is None:
                continue
            item, raw = _evidence_from_row(row, source_table="core.artists", artist_key=artist_key, provider=provider, provider_id=value, evidence_kind="CANONICAL_COLUMN")
            if item:
                evidence.append(item)
            else:
                invalid.append(_invalid_record(row, source_table="core.artists", artist_key=artist_key, provider=provider, provider_id=raw))
    for row in external_ids:
        if str(row.get("entity_type", "artist")).lower() != "artist":
            continue
        artist_key = _text(row.get("entity_key"))
        raw_id_type = str(row.get("id_type", "")).strip().lower()
        provider = ID_TYPE_TO_PROVIDER.get(raw_id_type)
        if provider is None and raw_id_type.upper() in PROVIDERS:
            provider = raw_id_type.upper()
        if not artist_key or artist_key not in known_artists:
            discarded.append(_discarded_record(row, source_table="core.entity_external_ids", artist_key=artist_key, provider=provider or "UNKNOWN_PROVIDER", provider_id=row.get("id_value"), reason="UNKNOWN_ARTIST"))
            continue
        if not provider:
            discarded.append(_discarded_record(row, source_table="core.entity_external_ids", artist_key=artist_key, provider="UNKNOWN_PROVIDER", provider_id=row.get("id_value"), reason="UNKNOWN_PROVIDER"))
            continue
        item, raw = _evidence_from_row(row, source_table="core.entity_external_ids", artist_key=artist_key, provider=provider, provider_id=row.get("id_value"), evidence_kind="EXTERNAL_ID_MASTER")
        if item:
            evidence.append(item)
        else:
            invalid.append(_invalid_record(row, source_table="core.entity_external_ids", artist_key=artist_key, provider=provider, provider_id=raw))
    for row in linkages:
        artist_key = _text(row.get("artist_key"))
        provider = _provider(row.get("provider"))
        if not artist_key or artist_key not in known_artists:
            discarded.append(_discarded_record(row, source_table="identity.artist_provider_linkages", artist_key=artist_key, provider=provider or "UNKNOWN_PROVIDER", provider_id=row.get("provider_id"), reason="UNKNOWN_ARTIST"))
            continue
        if not provider or provider not in PROVIDERS:
            discarded.append(_discarded_record(row, source_table="identity.artist_provider_linkages", artist_key=artist_key, provider=provider or "UNKNOWN_PROVIDER", provider_id=row.get("provider_id"), reason="UNKNOWN_PROVIDER"))
            continue
        item, raw = _evidence_from_row(row, source_table="identity.artist_provider_linkages", artist_key=artist_key, provider=provider, provider_id=row.get("provider_id"), evidence_kind="PROVIDER_LINKAGE")
        if item:
            evidence.append(item)
        else:
            invalid.append(_invalid_record(row, source_table="identity.artist_provider_linkages", artist_key=artist_key, provider=provider, provider_id=raw))
    for row in wikidata_rows:
        qid = normalize_provider_id("WIKIDATA", row.get("qid") or row.get("wikidata_id") or row.get("QID"))
        property_name = (_text(row.get("external_id_property") or row.get("property")) or "").upper()
        property_value = row.get("external_id_value")
        mbid = normalize_provider_id(
            "MUSICBRAINZ",
            row.get("P434") or row.get("p434") or row.get("musicbrainz_id") or row.get("mbid"),
        ) or qid_to_mbid.get(_text(qid) or "")
        if not mbid and property_name == "P434":
            mbid = normalize_provider_id("MUSICBRAINZ", property_value)
        artist_key = artist_by_mbid.get(mbid)
        if not artist_key or not qid:
            discarded.append(_discarded_record(row, source_table="wikidata_generation_parquet", artist_key=artist_key, provider="WIKIDATA", provider_id=qid, reason="NO_UNIQUE_P434_MBID_JOIN"))
            continue
        join_is_candidate = not qid_join_has_pit_time.get(qid, False)
        # Deterministic per-claim reference.  The parquet rows carry no source
        # reference column, and the fallback (artist+provider_id) collides when
        # several Wikidata items claim the same provider value; including the
        # qid/property keeps each claim distinct evidence.
        claim_row = dict(row)
        claim_row["source_ref"] = (
            f"wikidata_generation_parquet:{qid}:{property_name}:{_text(property_value) or 'P434'}"
        )
        # The qid-to-artist link is established by the P434 property row and is
        # emitted exactly once per qid; the typed property rows below are
        # independent claims, not repetitions of the same qid evidence.
        if property_name == "P434" or not property_name:
            wikidata_item, raw = _evidence_from_row(claim_row, source_table="wikidata_generation_parquet", artist_key=artist_key, provider="WIKIDATA", provider_id=qid, evidence_kind="WIKIDATA_P434_MBID_JOIN", candidate=join_is_candidate)
            if wikidata_item:
                evidence.append(wikidata_item)
            else:
                invalid.append(_invalid_record(claim_row, source_table="wikidata_generation_parquet", artist_key=artist_key, provider="WIKIDATA", provider_id=raw))
        # The generation's artist_external_ids product carries one row per
        # Wikidata property.  Join through P434 first, then retain the typed
        # external ID as independent evidence for the canonical artist.
        provider = WIKIDATA_PROPERTY_TO_PROVIDER.get(property_name or "")
        if provider and property_value is not None:
            item, raw = _evidence_from_row(claim_row, source_table="wikidata_generation_parquet", artist_key=artist_key, provider=provider, provider_id=property_value, evidence_kind=f"WIKIDATA_{property_name}_MBID_JOIN", candidate=join_is_candidate)
            if item:
                evidence.append(item)
            else:
                invalid.append(_invalid_record(claim_row, source_table="wikidata_generation_parquet", artist_key=artist_key, provider=provider, provider_id=raw))
    evidence.sort(key=lambda item: item.key())
    invalid.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    discarded.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return evidence, invalid, discarded


def _status_for(artist: str, provider: str, values: Sequence[Evidence]) -> tuple[str, str | None, list[dict[str, Any]]]:
    by_id: dict[str, list[Evidence]] = defaultdict(list)
    for item in values:
        by_id[item.provider_id].append(item)
    if not by_id:
        return "MISSING", None, []
    if len(by_id) > 1:
        return "AMBIGUOUS", None, [asdict(item) | {"evidence_key": item.key()} for item in values]
    provider_id, items = next(iter(by_id.items()))
    claimed = {str(item.claimed_status or "").upper() for item in items}
    if "CONFLICT" in claimed:
        return "CONFLICT", provider_id, [asdict(item) | {"evidence_key": item.key()} for item in items]
    if "AMBIGUOUS" in claimed:
        return "AMBIGUOUS", provider_id, [asdict(item) | {"evidence_key": item.key()} for item in items]
    # A blocked resolution claim cannot be promoted by another row for the
    # same provider ID.  Keep the evidence rows, but surface the provider as
    # unresolved until a new governed run removes the block.
    if claimed & {"CONFLICT", "FAILED", "UNRESOLVED"}:
        if "CONFLICT" in claimed:
            return "CONFLICT", provider_id, [asdict(item) | {"evidence_key": item.key()} for item in items]
        return "MISSING", provider_id, [asdict(item) | {"evidence_key": item.key()} for item in items]
    trusted_items = [item for item in items if item.trusted]
    if not trusted_items:
        status = "CANDIDATE"
    elif len({item.source_system.casefold() for item in trusted_items if item.source_system}) >= 2:
        status = "SUPPORTED_MULTI_SOURCE"
    else:
        status = "VERIFIED_EXACT"
    return status, provider_id, [asdict(item) | {"evidence_key": item.key()} for item in items]


def build_graph(
    *, artists: Iterable[Mapping[str, Any]], external_ids: Iterable[Mapping[str, Any]] = (),
    linkages: Iterable[Mapping[str, Any]] = (), wikidata_rows: Iterable[Mapping[str, Any]] = (),
    estate_rows: Iterable[Mapping[str, Any]] | None = None,
    as_of: str = "1970-01-01T00:00:00+00:00", canonical_limit: int = 25_000,
    created_at: str | None = None,
    source_tables: Sequence[str] | None = None,
    source_artifacts: Sequence[Mapping[str, Any]] | None = None,
    max_evidence: int = DEFAULT_MAX_EVIDENCE, max_edges: int = DEFAULT_MAX_EDGES,
    available_broader_artist_count: int | None = None,
) -> dict[str, Any]:
    """Build a deterministic in-memory graph; never writes database or R2.

    The broader scope can be materially larger than the governed estate and
    is returned only for read-only analysis.  A caller must establish resource
    limits before materializing or persisting that result.
    """
    as_of_dt = _parse_utc(as_of)
    if as_of_dt is None:
        raise ValueError("as_of must be RFC3339 UTC")
    build_created_at = created_at or datetime.now(timezone.utc).isoformat()
    if _parse_utc(build_created_at) is None:
        raise ValueError("created_at must be RFC3339 UTC")
    if canonical_limit <= 0:
        raise ValueError("canonical_limit must be positive")
    if max_evidence <= 0 or max_edges <= 0:
        raise ValueError("max_evidence and max_edges must be positive")
    if estate_rows is None:
        raise ValueError("estate_rows is required; canonical membership cannot be inferred")
    # Materialize once so generators cannot change the source set between
    # evidence collection and deterministic run-id construction.
    artists = list(artists)
    external_ids = list(external_ids)
    linkages = list(linkages)
    wikidata_rows = list(wikidata_rows)
    estate_rows = list(estate_rows)
    canonical = _canonical_rows(artists)
    narrow, broad = _scope_rows(canonical, estate_rows, canonical_limit)
    all_rows = narrow + broad
    estimated_evidence = (
        sum(1 for row in all_rows for column in PROVIDER_COLUMNS if row.get(column) is not None)
        + len(external_ids) + len(linkages) + (2 * len(wikidata_rows))
    )
    if estimated_evidence > max_evidence:
        raise ValueError("evidence preflight exceeds max_evidence")
    # Wikidata property rows may inherit their artist join from a separate P434
    # row.  Apply the PIT cutoff before constructing that QID-to-MBID map so a
    # future or invalid P434 claim can never authorize an older property row.
    admissible_wikidata: list[dict[str, Any]] = []
    wikidata_discarded: list[dict[str, Any]] = []
    for row in wikidata_rows:
        knowledge_time = _knowledge_time_text(
            row.get("knowledge_time") or row.get("last_verified_at") or row.get("ingested_at")
        )
        if knowledge_time:
            parsed_knowledge = _parse_utc(knowledge_time)
            reason = (
                "INVALID_KNOWLEDGE_TIME" if parsed_knowledge is None
                else "FUTURE_KNOWLEDGE_TIME" if parsed_knowledge > as_of_dt
                else None
            )
            if reason:
                wikidata_discarded.append(_discarded_record(
                    row, source_table="wikidata_generation_parquet", artist_key=None,
                    provider="WIKIDATA",
                    provider_id=row.get("qid") or row.get("wikidata_id") or row.get("QID"),
                    reason=reason,
                ))
                continue
        admissible_wikidata.append(row)
    evidence, invalid, discarded = _collect_evidence(
        all_rows, external_ids, linkages, admissible_wikidata
    )
    discarded = wikidata_discarded + discarded
    admissible: list[Evidence] = []
    for item in evidence:
        if item.knowledge_time:
            parsed_knowledge = _parse_utc(item.knowledge_time)
            if parsed_knowledge is None:
                discarded.append(_discarded_record(item.payload or {}, source_table=item.source_table, artist_key=item.artist_key, provider=item.provider, provider_id=item.provider_id, reason="INVALID_KNOWLEDGE_TIME"))
                continue
            if parsed_knowledge > as_of_dt:
                discarded.append(_discarded_record(item.payload or {}, source_table=item.source_table, artist_key=item.artist_key, provider=item.provider, provider_id=item.provider_id, reason="FUTURE_KNOWLEDGE_TIME"))
                continue
        admissible.append(item)
    evidence = admissible
    if len(evidence) + len(invalid) + len(discarded) > max_evidence:
        raise ValueError("evidence preflight exceeds max_evidence")
    potential_edges = len({(item.artist_key, item.provider) for item in evidence})
    if potential_edges > max_edges:
        raise ValueError("edge preflight exceeds max_edges")
    providers = tuple(sorted(set(PROVIDERS) | {item.provider for item in evidence}))
    scope_by_artist = {str(r["artist_key"]): CANONICAL_SCOPE for r in narrow} | {str(r["artist_key"]): BROAD_SCOPE for r in broad}
    # Shared native IDs are conflicts for every claimant; no winner is chosen.
    shared: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in evidence:
        shared[(item.provider, item.provider_id)].add(item.artist_key)
    shared_conflicts = {key for key, artists_for_id in shared.items() if len(artists_for_id) > 1}
    by_artist_provider: dict[tuple[str, str], list[Evidence]] = defaultdict(list)
    for item in evidence:
        by_artist_provider[(item.artist_key, item.provider)].append(item)
    shared_evidence: dict[tuple[str, str], list[Evidence]] = defaultdict(list)
    for item in evidence:
        shared_evidence[(item.provider, item.provider_id)].append(item)
    source_names = list(source_tables or ["core.artists", "core.entity_external_ids", "identity.artist_provider_linkages"])
    artifact_rows = list(source_artifacts or [])
    available_broad_count = len(broad) if available_broader_artist_count is None else available_broader_artist_count
    run_material = {
        "version": RUN_VERSION, "as_of": as_of, "canonical_limit": canonical_limit,
        "artists": all_rows, "estate": estate_rows, "evidence": [asdict(item) for item in evidence], "invalid": invalid,
        "discarded": discarded,
        "source_tables": source_names, "source_artifacts": artifact_rows,
        "available_broader_artist_count": available_broad_count,
        "estate_identity": next((item.get("sha256") for item in artifact_rows if item.get("role") == "governed_estate"), stable_key("estate", estate_rows)),
    }
    run_key = stable_key("run", run_material)
    edge_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    conflict_rows: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    score_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    evidence_status_by_key: dict[str, str] = {}
    for row in all_rows:
        artist_key = str(row["artist_key"])
        scope = scope_by_artist[artist_key]
        statuses: dict[str, str] = {}
        for provider in providers:
            values = by_artist_provider.get((artist_key, provider), [])
            status, provider_id, item_rows = _status_for(artist_key, provider, values)
            if provider_id is not None and (provider, provider_id) in shared_conflicts:
                status = "CONFLICT"
            statuses[provider] = status
            score_counts[(scope, provider)][status] += 1
            if not values or status == "MISSING":
                # Missing providers are represented by the complete node
                # status map and scorecard, not a dense placeholder edge.
                for item in values:
                    evidence_status_by_key[item.key()] = status
                continue
            refs = sorted({str(item.get("source_ref")) for item in item_rows if item.get("source_ref")})
            keys = sorted(str(item["evidence_key"]) for item in item_rows)
            knowledge = sorted(str(item.get("knowledge_time")) for item in item_rows if item.get("knowledge_time"))
            source_systems = sorted({item.source_system for item in values if item.source_system})
            source_versions = sorted({item.source_version for item in values if item.source_version})
            source_checksums = sorted({item.source_checksum for item in values if item.source_checksum})
            retrieved = sorted({item.retrieved_at for item in values if item.retrieved_at})
            edge_rows.append({
                "edge_key": stable_key("edge", [run_key, artist_key, scope, provider, provider_id, status]),
                "run_key": run_key, "artist_key": artist_key, "scope": scope,
                "provider": provider, "provider_id": provider_id, "resolution_status": status,
                "evidence_keys": keys, "evidence_count": len(keys), "source_refs": refs,
                "rights_status": _conservative((item.rights_status for item in values), rights=True),
                "commercial_use_status": _conservative((item.commercial_use_status for item in values), rights=False),
                "knowledge_time": knowledge[-1] if knowledge else None,
                "source_system": source_systems[0] if len(source_systems) == 1 else ("MULTI" if source_systems else None),
                "source_version": source_versions[0] if len(source_versions) == 1 else ("MULTI" if source_versions else None),
                "source_checksum": source_checksums[0] if len(source_checksums) == 1 else ("MULTI" if source_checksums else None),
                "retrieved_at": retrieved[-1] if retrieved else None,
                "trust_class": "+".join(sorted({item.trust_class for item in values})),
                "resolution_basis": "+".join(sorted({item.resolution_basis for item in values})),
            })
            if len(edge_rows) > max_edges:
                raise ValueError("edge preflight exceeds max_edges")
            for item in values:
                evidence_status_by_key[item.key()] = status
            if status == "AMBIGUOUS":
                conflict_type = "MULTIPLE_PROVIDER_IDS" if len({item.provider_id for item in values}) > 1 else "AMBIGUOUS_PROVIDER_EVIDENCE"
                conflict_rows.append({
                    "conflict_type": conflict_type, "provider": provider,
                    "provider_id": None if conflict_type == "MULTIPLE_PROVIDER_IDS" else provider_id,
                    "artist_keys": [artist_key],
                    "evidence_keys": sorted(item.key() for item in values),
                    "source_refs": sorted({item.source_ref for item in values if item.source_ref}),
                    "rights_status": _conservative((item.rights_status for item in values), rights=True),
                    "commercial_use_status": _conservative((item.commercial_use_status for item in values), rights=False),
                    "knowledge_time": max((item.knowledge_time for item in values if item.knowledge_time), default=None),
                    "source_system": None if len({item.source_system for item in values if item.source_system}) != 1 else next((item.source_system for item in values if item.source_system), None),
                    "source_version": None if len({item.source_version for item in values if item.source_version}) != 1 else next((item.source_version for item in values if item.source_version), None),
                    "source_checksum": None if len({item.source_checksum for item in values if item.source_checksum}) != 1 else next((item.source_checksum for item in values if item.source_checksum), None),
                    "retrieved_at": max((item.retrieved_at for item in values if item.retrieved_at), default=None),
                    "explanation": "provider evidence is ambiguous; no ID is promoted",
                })
            elif status == "CONFLICT":
                shared_values = shared_evidence[(provider, provider_id)]
                conflict_rows.append({
                    "conflict_type": "SHARED_PROVIDER_ID", "provider": provider,
                    "provider_id": provider_id, "artist_keys": sorted(shared[(provider, provider_id)]),
                    "evidence_keys": sorted(item.key() for item in shared_values),
                    "source_refs": sorted({item.source_ref for item in shared_values if item.source_ref}),
                    "rights_status": _conservative((item.rights_status for item in shared_values), rights=True),
                    "commercial_use_status": _conservative((item.commercial_use_status for item in shared_values), rights=False),
                    "knowledge_time": max((item.knowledge_time for item in shared_values if item.knowledge_time), default=None),
                    "source_system": None if len({item.source_system for item in shared_values if item.source_system}) != 1 else next((item.source_system for item in shared_values if item.source_system), None),
                    "source_version": None if len({item.source_version for item in shared_values if item.source_version}) != 1 else next((item.source_version for item in shared_values if item.source_version), None),
                    "source_checksum": None if len({item.source_checksum for item in shared_values if item.source_checksum}) != 1 else next((item.source_checksum for item in shared_values if item.source_checksum), None),
                    "retrieved_at": max((item.retrieved_at for item in shared_values if item.retrieved_at), default=None),
                    "explanation": "one provider ID is claimed by multiple canonical artists; no claimant is promoted",
                })
        nodes.append({
            "node_key": stable_key("node", [run_key, artist_key, scope]), "run_key": run_key,
            "artist_key": artist_key, "artist_name": row.get("name"),
            "musicbrainz_id": row.get("musicbrainz_id"), "scope": scope,
            "estate_tier": row.get("estate_tier"),
            "provider_status_json": statuses, "rights_status": _text(row.get("rights_status")) or DEFAULT_RIGHTS,
            "commercial_use_status": _text(row.get("commercial_use_status")) or DEFAULT_COMMERCIAL,
            "knowledge_time": _text(row.get("knowledge_time")),
        })
    for bad in invalid:
        provider = str(bad["provider"])
        score_counts[(scope_by_artist.get(str(bad["artist_key"]), BROAD_SCOPE), provider)]["INVALID"] += 1
        invalid_key = stable_key("invalid_evidence", bad)
        evidence_rows.append({
            "evidence_key": invalid_key, "run_key": run_key, "artist_key": bad["artist_key"],
            "scope": scope_by_artist.get(str(bad["artist_key"]), BROAD_SCOPE),
            "provider": provider, "provider_id": bad["provider_id"],
            "source_table": bad["source_table"], "source_ref": bad["source_ref"],
            "source_url": bad["source_url"], "evidence_kind": "INVALID_PROVIDER_ID",
            "evidence_status": "MISSING", "claimed_status": bad.get("claimed_status"), "rights_status": bad["rights_status"],
            "commercial_use_status": bad["commercial_use_status"],
            "source_system": bad["source_system"], "source_version": bad["source_version"],
            "source_checksum": bad["source_checksum"], "retrieved_at": bad["retrieved_at"],
            "trust_class": "CANDIDATE", "resolution_basis": "INVALID_PROVIDER_ID",
            "knowledge_time": bad["knowledge_time"], "payload_json": bad["payload_json"],
        })
        conflict_rows.append({"conflict_type": "INVALID_PROVIDER_ID", "provider": provider, "provider_id": bad.get("provider_id"), "artist_keys": [bad["artist_key"]], "evidence_keys": [invalid_key], "source_refs": [bad["source_ref"]], "rights_status": bad["rights_status"], "commercial_use_status": bad["commercial_use_status"], "knowledge_time": bad["knowledge_time"], "source_system": _text(bad.get("source_system")), "source_version": _text(bad.get("source_version")), "source_checksum": _text(bad.get("source_checksum")), "retrieved_at": _text(bad.get("retrieved_at")), "explanation": "provider-specific ID shape validation failed; evidence was not admitted"})
    for bad in discarded:
        conflict_rows.append({
            "conflict_type": "DISCARDED_UNKNOWN_CLAIM", "provider": bad["provider"],
            "provider_id": bad["provider_id"], "artist_keys": [bad["artist_key"]] if bad.get("artist_key") else [],
            "evidence_keys": [], "source_refs": [bad["source_ref"]],
            "explanation": bad["reason"], "rights_status": bad["rights_status"],
            "commercial_use_status": bad["commercial_use_status"],
            "knowledge_time": bad["knowledge_time"],
            "source_system": _text(bad.get("source_system")),
            "source_version": _text(bad.get("source_version")),
            "source_checksum": _text(bad.get("source_checksum")),
            "retrieved_at": _text(bad.get("retrieved_at")),
        })
    unique_conflicts: dict[str, dict[str, Any]] = {}
    for conflict in conflict_rows:
        conflict_identity = stable_key("conflict_identity", [
            conflict.get("conflict_type"), conflict.get("provider"),
            conflict.get("provider_id"), conflict.get("artist_keys"),
            conflict.get("evidence_keys"), conflict.get("source_refs", []),
        ])
        unique_conflicts.setdefault(conflict_identity, conflict)
    conflict_rows = [unique_conflicts[key] for key in sorted(unique_conflicts)]
    for item in evidence:
        evidence_rows.append({
            "evidence_key": item.key(), "run_key": run_key, "artist_key": item.artist_key,
            "scope": scope_by_artist.get(item.artist_key, BROAD_SCOPE), "provider": item.provider,
            "provider_id": item.provider_id, "source_table": item.source_table,
            "source_ref": item.source_ref, "source_url": item.source_url,
            "evidence_kind": item.evidence_kind,
            "evidence_status": evidence_status_by_key.get(item.key(), "MISSING"),
            "claimed_status": item.claimed_status,
            "source_system": item.source_system, "source_version": item.source_version,
            "source_checksum": item.source_checksum, "retrieved_at": item.retrieved_at,
            "rights_status": item.rights_status,
            "commercial_use_status": item.commercial_use_status, "knowledge_time": item.knowledge_time,
            "payload_json": dict(item.payload or {}),
            "trust_class": item.trust_class, "resolution_basis": item.resolution_basis,
        })
    evidence_rows.sort(key=lambda item: item["evidence_key"])
    for conflict in conflict_rows:
        conflict["conflict_key"] = stable_key("conflict", [run_key, conflict])
        conflict.setdefault("source_refs", [])
        conflict.setdefault("rights_status", DEFAULT_RIGHTS)
        conflict.setdefault("commercial_use_status", DEFAULT_COMMERCIAL)
        conflict.setdefault("knowledge_time", None)
        conflict.setdefault("source_system", None)
        conflict.setdefault("source_version", None)
        conflict.setdefault("source_checksum", None)
        conflict.setdefault("retrieved_at", None)
        conflict.update({"run_key": run_key, "created_at": build_created_at})
    scorecard: list[dict[str, Any]] = []
    for scope, scope_rows in ((CANONICAL_SCOPE, narrow), (BROAD_SCOPE, broad)):
        universe = len(scope_rows)
        for provider in providers:
            counts = score_counts[(scope, provider)]
            entry = {"run_key": run_key, "scope": scope, "provider": provider, "universe_count": universe}
            for status in STATUSES:
                entry[status.lower() + "_count"] = int(counts.get(status, 0))
            entry["invalid_count"] = int(counts.get("INVALID", 0))
            entry["coverage_pct"] = (100.0 * (entry["verified_exact_count"] + entry["supported_multi_source_count"]) / universe) if universe else None
            entry["scorecard_key"] = stable_key("scorecard", entry)
            entry["rights_status"] = DEFAULT_RIGHTS
            entry["commercial_use_status"] = DEFAULT_COMMERCIAL
            entry["knowledge_time"] = None
            scorecard.append(entry)
    run = {
        "run_key": run_key, "run_version": RUN_VERSION, "as_of": as_of,
        "canonical_limit": canonical_limit, "canonical_count": len(narrow), "broader_count": len(broad),
        "available_broader_artist_count": available_broad_count,
        "dense_edge_count_avoided": len(all_rows) * len(providers) - len(edge_rows),
        "evidence_count": len(evidence_rows), "edge_count": len(edge_rows), "conflict_count": len(conflict_rows),
        "provider_count": len(providers), "input_digest": stable_key("inputs", run_material),
        "source_tables": source_names, "source_artifacts": artifact_rows,
        "estate_identity": next((item.get("sha256") for item in artifact_rows if item.get("role") == "governed_estate"), stable_key("estate", estate_rows)),
        "rights_status": DEFAULT_RIGHTS,
        "commercial_use_status": DEFAULT_COMMERCIAL, "knowledge_time_basis": DEFAULT_KNOWLEDGE_BASIS,
        "resource_warning": (
            "BROADER_CANONICAL_IS_IN_MEMORY_READ_ONLY; apply an explicit resource budget before materializing"
            if broad else
            "CANONICAL_ONLY_DEFAULT; broader scope requires explicit include_broad and max_artists"
        ),
        "build_status": "DRY_RUN_READY", "created_at": build_created_at,
    }
    created = build_created_at
    for collection in (nodes, edge_rows, evidence_rows, scorecard):
        for row in collection:
            row["created_at"] = created
    for claim in discarded:
        claim["created_at"] = created
    return {"run": run, "nodes": nodes, "evidence": evidence_rows, "edges": edge_rows, "conflicts": conflict_rows, "scorecard": scorecard, "discarded_claims": discarded}


def rows_from_connection(
    conn, governed_keys: Iterable[str] = (), *, include_broad: bool = False,
    max_artists: int = DEFAULT_MAX_ARTISTS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str], int]:
    """Read only governed artists by default; broad reads require an explicit cap."""
    if max_artists <= 0:
        raise ValueError("max_artists must be positive")
    def read(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cur = conn.execute(sql, params)
        columns = [str(d[0]) for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    present = {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables"
        ).fetchall()
    }
    if ("core", "artists") not in present:
        raise RuntimeError("required source table core.artists is unavailable")
    governed = tuple(dict.fromkeys(_text(key) for key in governed_keys if _text(key)))
    available_broad = int(conn.execute(
        "SELECT COUNT(*) FROM core.artists WHERE artist_key NOT IN (SELECT * FROM UNNEST(?))",
        [list(governed)],
    ).fetchone()[0]) if governed else int(conn.execute("SELECT COUNT(*) FROM core.artists").fetchone()[0])
    if include_broad and not governed:
        raise ValueError("include_broad requires governed_keys")
    if len(governed) > max_artists:
        raise ValueError("governed artist count exceeds max_artists")
    artist_columns = [
        "artist_key", "name", *PROVIDER_COLUMNS.keys(),
        "source_system", "source_url", "source_retrieved_at", "resolution_status",
        "knowledge_time", "ingested_at", "rights_status", "commercial_use_status",
    ]
    external_columns = [
        "entity_type", "entity_key", "id_type", "id_value", "url", "evidence_url",
        "source_system", "source_version", "source_checksum", "retrieved_at",
        "resolution_status", "knowledge_time", "ingested_at",
    ]
    linkage_columns = [
        "artist_key", "provider", "provider_id", "provider_url", "source_system",
        "source_version", "source_checksum", "retrieved_at", "resolution_status",
        "knowledge_time", "last_verified_at", "evidence_ref",
    ]
    def projected(table_schema: str, table_name: str, requested: Sequence[str]) -> str:
        available = {str(row[0]) for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=?",
            [table_schema, table_name],
        ).fetchall()}
        columns = [column for column in requested if column in available]
        if not columns:
            raise RuntimeError(f"{table_schema}.{table_name} has no usable projected columns")
        return ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
    def key_filter(column: str, keys: Sequence[str]) -> tuple[str, list[Any]]:
        return f'"{column}" IN (SELECT * FROM UNNEST(?))', [list(keys)]
    sources: list[str] = []
    artist_select = projected("core", "artists", artist_columns)
    artists: list[dict[str, Any]] = []
    if governed:
        clause, params = key_filter("artist_key", governed)
        artists.extend(read(f"SELECT {artist_select} FROM core.artists WHERE {clause}", params))
    if include_broad:
        remaining = max_artists - len(artists)
        clause, params = key_filter("artist_key", governed)
        artists.extend(read(f"SELECT {artist_select} FROM core.artists WHERE NOT ({clause}) ORDER BY artist_key LIMIT {remaining}", params))
    selected_keys = tuple(str(row["artist_key"]) for row in artists)
    external = []
    if ("core", "entity_external_ids") in present and selected_keys:
        clause, params = key_filter("entity_key", selected_keys)
        external = read(f"SELECT {projected('core', 'entity_external_ids', external_columns)} FROM core.entity_external_ids WHERE {clause}", params)
    linkages = []
    if ("identity", "artist_provider_linkages") in present and selected_keys:
        clause, params = key_filter("artist_key", selected_keys)
        linkages = read(f"SELECT {projected('identity', 'artist_provider_linkages', linkage_columns)} FROM identity.artist_provider_linkages WHERE {clause}", params)
    if ("core", "artists") in present:
        sources.append("core.artists")
    if ("core", "entity_external_ids") in present:
        sources.append("core.entity_external_ids")
    if ("identity", "artist_provider_linkages") in present:
        sources.append("identity.artist_provider_linkages")
    return artists, external, linkages, sources, available_broad


def read_wikidata_parquets(
    paths: Iterable[str], *, max_rows: int = DEFAULT_MAX_WIKIDATA_ROWS,
    max_bytes: int = DEFAULT_MAX_WIKIDATA_BYTES,
    allowed_mbids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Read projected Wikidata row groups under explicit row/byte bounds."""
    paths = list(paths)
    if not paths:
        return []
    import pyarrow.parquet as pq
    rows: list[dict[str, Any]] = []
    if max_rows <= 0 or max_bytes <= 0:
        raise ValueError("Wikidata bounds must be positive")
    selected = {"qid", "wikidata_id", "QID", "external_id_property", "property", "external_id_value", "P434", "p434", "musicbrainz_id", "mbid", "source_system", "source_version", "source_checksum", "retrieved_at", "knowledge_time", "ingested_at", "source_ref", "source_url"}
    allowed = {normalize_provider_id("MUSICBRAINZ", value) for value in (allowed_mbids or ())}
    total_bytes = 0
    def qid_of(row: Mapping[str, Any]) -> str | None:
        return normalize_provider_id("WIKIDATA", row.get("qid") or row.get("wikidata_id") or row.get("QID"))

    def batch_rows(batch: Any) -> Iterable[dict[str, Any]]:
        """Yield one bounded projected row at a time; never materialize a table."""
        names = list(batch.schema.names)
        for index in range(batch.num_rows):
            yield {
                name: batch.column(name)[index].as_py()
                for name in names
            }
    parquet_files: list[tuple[Any, list[str]]] = []
    for path in paths:
        file_size = os.path.getsize(path)
        total_bytes += file_size
        if total_bytes > max_bytes:
            raise ValueError("Wikidata input bytes exceed max_bytes")
        parquet = pq.ParquetFile(path)
        columns = [name for name in parquet.schema.names if name in selected]
        if not columns:
            continue
        parquet_files.append((parquet, columns))

    def p434_mbid(row: Mapping[str, Any], prop: str) -> str | None:
        candidates = []
        if prop == "P434":
            candidates.extend((row.get("external_id_value"), row.get("P434"), row.get("p434")))
        else:
            candidates.extend((row.get("P434"), row.get("p434"), row.get("musicbrainz_id"), row.get("mbid")))
        return next((candidate for candidate in (
            normalize_provider_id("MUSICBRAINZ", value) for value in candidates
        ) if candidate), None)

    # Pass 1 discovers the complete QID allowlist across all input files.
    # Pass 2 emits only typed properties for those QIDs.  Both passes stream
    # bounded record batches and the byte guard above covers every file.
    qids: set[str] = set()
    for parquet, columns in parquet_files:
        for batch in parquet.iter_batches(batch_size=10_000, columns=columns):
            for row in batch_rows(batch):
                qid = qid_of(row)
                prop = str(row.get("external_id_property") or row.get("property") or "").upper()
                mbid = p434_mbid(row, prop)
                if qid and ((prop == "P434" and mbid and (not allowed or mbid in allowed)) or (not prop and mbid and (not allowed or mbid in allowed))):
                    qids.add(qid)

    for parquet, columns in parquet_files:
        for batch in parquet.iter_batches(batch_size=10_000, columns=columns):
            for row in batch_rows(batch):
                qid = qid_of(row)
                prop = str(row.get("external_id_property") or row.get("property") or "").upper()
                if not qid or qid not in qids or (prop and prop not in WIKIDATA_PROPERTY_TO_PROVIDER):
                    continue
                mbid = p434_mbid(row, prop)
                if allowed and mbid and not prop and mbid not in allowed:
                    continue
                if prop == "P434" and allowed and mbid not in allowed:
                    continue
                rows.append(row)
                if len(rows) > max_rows:
                    raise ValueError("Wikidata rows exceed max_rows; refusing truncation")
    return rows


def read_estate_json(path: str) -> list[dict[str, Any]]:
    """Read the governed estate manifest; membership is never inferred here."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    entries = payload.get("artists") if isinstance(payload, Mapping) else None
    if isinstance(entries, Mapping):
        entries = [
            ({"tier": value} if not isinstance(value, Mapping) else dict(value)) | {"artist_key": key}
            for key, value in entries.items()
        ]
    if not isinstance(entries, list):
        raise ValueError("estate JSON must contain an artists list or mapping")
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("estate artists entries must be objects")
        key = _text(entry.get("artist_key") or entry.get("key"))
        if not key:
            raise ValueError("estate artist entries require artist_key/key")
        row = dict(entry)
        row["artist_key"] = key
        result.append(row)
    return result


def jsonable(result: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(result, sort_keys=True, default=str))


def write_graph_tables(conn, result: Mapping[str, Any]) -> dict[str, int]:
    """Create migration 048 tables and insert a result atomically."""
    migration = Path(__file__).resolve().parents[3] / "schema" / "migrations" / "048_identity_graph_v2.sql"
    json_columns = {"source_tables", "source_artifacts", "evidence_keys", "source_refs", "artist_keys", "provider_status_json", "payload_json"}
    tables = (("runs", [result["run"]]), ("nodes", result["nodes"]), ("evidence", result["evidence"]), ("edges", result["edges"]), ("conflicts", result["conflicts"]), ("scorecard", result["scorecard"]))
    begun = False
    try:
        conn.execute("BEGIN")
        begun = True
        # DDL is deliberately inside the same transaction as inserts.  A
        # malformed row must not leave a partially-created output database.
        conn.execute(migration.read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for name, rows in tables:
            table = "graph_v2_" + name
            columns = [str(row[0]) for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='identity' AND table_name=? ORDER BY ordinal_position", [table],
            ).fetchall()]
            if not columns and rows:
                raise ValueError(f"migration did not create identity.{table}")
            prepared: list[list[Any]] = []
            for raw_row in rows:
                row = dict(raw_row)
                if set(row) != set(columns):
                    raise ValueError(
                        f"identity.{table} row columns mismatch: "
                        f"missing={sorted(set(columns) - set(row))}, "
                        f"extra={sorted(set(row) - set(columns))}"
                    )
                prepared.append([
                    # default=str renders DuckDB datetime objects as RFC3339
                    # strings inside payload JSON; the DB columns are TEXT.
                    json.dumps(row[column], sort_keys=True, default=str) if column in json_columns and row[column] is not None else row[column]
                    for column in columns
                ])
            if prepared:
                # Bulk load through an Arrow table: duckdb-python executemany
                # inserts row-by-row (~1k rows/s), which takes minutes for the
                # ~570k-row graph; the Arrow path is ~100x faster while keeping
                # the same deterministic prepared values.
                import pyarrow as pa
                registered = f"t_ins_{name}"
                arrow = pa.Table.from_pylist([
                    dict(zip(columns, row)) for row in prepared
                ])
                conn.register(registered, arrow)
                try:
                    conn.execute(
                        f"INSERT INTO identity.{table} ({','.join(columns)}) "
                        f"SELECT * FROM {registered}"
                    )
                finally:
                    conn.unregister(registered)
            counts[name] = len(rows)
        conn.execute("COMMIT")
        begun = False
        return counts
    except Exception:
        if begun:
            conn.execute("ROLLBACK")
        raise
