"""OpenStreetMap Overpass lookup for venue capacity tags.

Key-free. Tags such as capacity / capacity:persons / capacity:seats are
source claims. knowledge_time is retrieval time.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from ...evidence.semantics import ContentRole
from ..base import BaseProvider
from ..contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CostEstimate,
    ProviderHealth,
    content_hash_of,
    utc_now,
)
from ..transport import TransportError

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PROVIDER_NAME = "openstreetmap_overpass"
PROVIDER_VERSION = "osm_overpass-v1"
USER_AGENT = "FestivalBloomberg/0.1 (research; venue-capacity-claims)"
SEARCH_BY_NAME = "SEARCH_BY_NAME"
CAPACITY_TAGS = ("capacity", "capacity:persons", "capacity:seats", "capacity:wheelchair")


class OpenStreetMapProvider(BaseProvider):
    name = "openstreetmap"

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        return True

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="open_endpoint")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        retrieved_at = started.isoformat()
        name = (request.query or "").strip()
        if not name:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=OVERPASS_URL,
                started_at=started,
                cost_usd=0.0,
                error_category="request_invalid",
                provider_metadata={"detail": "SEARCH_BY_NAME requires query"},
            )
        city = "Chicago"
        if request.market_id:
            city = request.market_id.split(",")[0].strip() or city
        query = _overpass_query(name, city)
        try:
            response = self.transport.request(
                "POST",
                OVERPASS_URL,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode({"data": query}).encode("utf-8"),
                timeout_seconds=45.0,
            )
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=OVERPASS_URL,
                started_at=started,
                cost_usd=0.0,
                error_category="network",
                provider_metadata={"detail": str(exc)},
            )
        if response.status != 200:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=OVERPASS_URL,
                started_at=started,
                cost_usd=0.0,
                error_category="http_error",
                provider_metadata={"detail": f"http {response.status}"},
            )
        try:
            payload = response.json()
        except ValueError:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=OVERPASS_URL,
                started_at=started,
                cost_usd=0.0,
                error_category="schema_invalid",
            )
        records = [_normalize_element(el, retrieved_at) for el in payload.get("elements") or []]
        records = [r for r in records if r is not None]
        status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
        return self._result(
            request,
            status=status,
            provider_endpoint=OVERPASS_URL,
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of([r.get("osm_id") for r in records]),
            provider_metadata={"provider_version": PROVIDER_VERSION, "cost_usd": 0.0},
            records=tuple(records),
        )


def _overpass_query(name: str, city: str) -> str:
    escaped = name.replace('"', '\\"')
    city_esc = city.replace('"', '\\"')
    return f"""
[out:json][timeout:25];
area["name"="{city_esc}"]["admin_level"="8"]->.searchArea;
(
  nwr["name"="{escaped}"](area.searchArea);
);
out tags center;
""".strip()


def _normalize_element(element: dict[str, Any], retrieved_at: str) -> dict | None:
    tags = element.get("tags") or {}
    osm_type = element.get("type")
    osm_id = element.get("id")
    if osm_type is None or osm_id is None:
        return None
    claims = []
    for tag in CAPACITY_TAGS:
        raw = tags.get(tag)
        if raw is None:
            continue
        try:
            value = float(str(raw).split()[0].replace(",", ""))
        except (TypeError, ValueError):
            continue
        kind = "SEATED" if "seat" in tag else ("MAX_PERSONS" if tag != "capacity" else "UNKNOWN")
        if tag == "capacity:persons":
            kind = "MAX_PERSONS"
        claims.append({"tag": tag, "capacity_value": value, "capacity_kind": kind})
    return {
        "platform": "openstreetmap",
        "provider": PROVIDER_NAME,
        "object_type": "osm_object",
        "platform_object_id": f"{osm_type}/{osm_id}",
        "osm_type": osm_type,
        "osm_id": str(osm_id),
        "venue_name": tags.get("name"),
        "capacity_tags": {k: tags[k] for k in CAPACITY_TAGS if k in tags},
        "capacity_claims": claims,
        "osm_tags": tags,
        "canonical_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "retrieved_at": retrieved_at,
        "knowledge_time": retrieved_at,
        "knowledge_time_source": "retrieval",
        "content_role": ContentRole.ENCYCLOPEDIC.value,
        "provider_version": PROVIDER_VERSION,
        "content_hash": content_hash_of({"type": osm_type, "id": osm_id, "capacity": claims}),
    }
