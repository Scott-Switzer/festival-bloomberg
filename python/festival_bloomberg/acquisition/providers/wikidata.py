"""Wikidata entity lookup for venue capacity claims (P1083).

Key-free. Capacity values are source claims with rank/qualifiers/references.
knowledge_time is retrieval time unless a referenced publication time exists
as a source fact (still not used as knowledge_time).
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

API_BASE = "https://www.wikidata.org/w/api.php"
PROVIDER_NAME = "wikidata_official_api"
PROVIDER_VERSION = "wikidata_wb-v1"
USER_AGENT = "FestivalBloomberg/0.1 (research; venue-capacity-claims)"
SEARCH_ENTITIES = "SEARCH_ENTITIES"
GET_ENTITY_CLAIMS = "GET_ENTITY_CLAIMS"
P1083 = "P1083"


class WikidataProvider(BaseProvider):
    name = "wikidata"

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        return True

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="open_endpoint")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        retrieved_at = started.isoformat()
        operation = (request.operation or SEARCH_ENTITIES).upper()
        try:
            if operation == GET_ENTITY_CLAIMS:
                records, error = self._get_claims(request, retrieved_at)
                endpoint = API_BASE
            else:
                records, error = self._search(request, retrieved_at)
                endpoint = API_BASE
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=API_BASE,
                started_at=started,
                cost_usd=0.0,
                error_category="network",
                provider_metadata={"detail": str(exc)},
            )
        if error is not None:
            return self._result(
                request,
                status=error["status"],
                provider_endpoint=endpoint,
                started_at=started,
                cost_usd=0.0,
                error_category=error["category"],
                provider_metadata={"detail": error.get("detail"), "operation": operation},
            )
        status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
        return self._result(
            request,
            status=status,
            provider_endpoint=endpoint,
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of([r.get("wikidata_qid") for r in records]),
            provider_metadata={"operation": operation, "provider_version": PROVIDER_VERSION, "cost_usd": 0.0},
            records=tuple(records),
        )

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT, "Accept": "application/json"}

    def _get(self, params: dict[str, str]) -> tuple[Any, dict | None]:
        response = self.transport.request(
            "GET",
            f"{API_BASE}?{urlencode(params)}",
            headers=self._headers(),
            timeout_seconds=30.0,
        )
        if response.status != 200:
            return None, {
                "status": AcquisitionStatus.PROVIDER_ERROR,
                "category": "http_error",
                "detail": f"http {response.status}",
            }
        try:
            return response.json(), None
        except ValueError:
            return None, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "schema_invalid",
                "detail": "wikidata json",
            }

    def _search(self, request: AcquisitionRequest, retrieved_at: str):
        query = (request.query or request.entity_id or "").strip()
        if not query:
            return [], {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "SEARCH_ENTITIES requires query",
            }
        payload, error = self._get(
            {
                "action": "wbsearchentities",
                "search": query,
                "language": "en",
                "type": "item",
                "format": "json",
                "limit": str(min(request.max_records, 10)),
            }
        )
        if error is not None:
            return [], error
        records = []
        for item in payload.get("search") or []:
            records.append(
                {
                    "platform": "wikidata",
                    "provider": PROVIDER_NAME,
                    "object_type": "entity",
                    "platform_object_id": item.get("id"),
                    "wikidata_qid": item.get("id"),
                    "label": item.get("label"),
                    "description": item.get("description"),
                    "canonical_url": item.get("concepturi") or item.get("url"),
                    "retrieved_at": retrieved_at,
                    "knowledge_time": retrieved_at,
                    "knowledge_time_source": "retrieval",
                    "content_role": ContentRole.ENCYCLOPEDIC.value,
                    "provider_version": PROVIDER_VERSION,
                    "content_hash": content_hash_of({"id": item.get("id"), "label": item.get("label")}),
                }
            )
        return records, None

    def _get_claims(self, request: AcquisitionRequest, retrieved_at: str):
        qid = (request.external_id or request.query or "").strip()
        if not qid:
            return [], {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "GET_ENTITY_CLAIMS requires QID",
            }
        payload, error = self._get(
            {
                "action": "wbgetentities",
                "ids": qid,
                "props": "claims|labels|sitelinks",
                "languages": "en",
                "format": "json",
            }
        )
        if error is not None:
            return [], error
        entity = ((payload.get("entities") or {}).get(qid)) or {}
        claims = ((entity.get("claims") or {}).get(P1083)) or []
        label = ((entity.get("labels") or {}).get("en") or {}).get("value")
        records = []
        for statement in claims:
            parsed = parse_p1083_statement(statement, qid=qid, label=label, retrieved_at=retrieved_at)
            if parsed is not None:
                records.append(parsed)
        if not records:
            records.append(
                {
                    "platform": "wikidata",
                    "provider": PROVIDER_NAME,
                    "object_type": "entity",
                    "platform_object_id": qid,
                    "wikidata_qid": qid,
                    "label": label,
                    "capacity_value": None,
                    "capacity_claims": [],
                    "retrieved_at": retrieved_at,
                    "knowledge_time": retrieved_at,
                    "knowledge_time_source": "retrieval",
                    "content_role": ContentRole.ENCYCLOPEDIC.value,
                    "provider_version": PROVIDER_VERSION,
                    "content_hash": content_hash_of({"id": qid, "p1083": None}),
                }
            )
        return records, None


def parse_p1083_statement(statement: dict, *, qid: str, label: str | None, retrieved_at: str) -> dict | None:
    snak = statement.get("mainsnak") or {}
    datavalue = (snak.get("datavalue") or {}).get("value")
    if not isinstance(datavalue, dict):
        return None
    amount = datavalue.get("amount")
    try:
        capacity = float(str(amount).replace("+", ""))
    except (TypeError, ValueError):
        return None
    qualifiers = _flatten_qualifiers(statement.get("qualifiers") or {})
    kind = capacity_kind_from_qualifiers(qualifiers)
    rank = statement.get("rank") or "normal"
    references = statement.get("references") or []
    return {
        "platform": "wikidata",
        "provider": PROVIDER_NAME,
        "object_type": "capacity_claim",
        "platform_object_id": statement.get("id") or qid,
        "wikidata_qid": qid,
        "label": label,
        "capacity_value": capacity,
        "capacity_kind": kind,
        "wikidata_rank": rank,
        "wikidata_unit": datavalue.get("unit"),
        "wikidata_qualifiers": qualifiers,
        "wikidata_reference_count": len(references),
        "canonical_url": f"https://www.wikidata.org/wiki/{qid}",
        "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "retrieved_at": retrieved_at,
        "knowledge_time": retrieved_at,
        "knowledge_time_source": "retrieval",
        "content_role": ContentRole.ENCYCLOPEDIC.value,
        "provider_version": PROVIDER_VERSION,
        "content_hash": content_hash_of({"id": statement.get("id"), "amount": amount, "rank": rank}),
    }


def capacity_kind_from_qualifiers(qualifiers: dict[str, list[str]]) -> str:
    blob = " ".join(v for values in qualifiers.values() for v in values).lower()
    if "concert" in blob or "music" in blob:
        return "CONCERT"
    if "basketball" in blob or "hockey" in blob or "sport" in blob:
        return "SPORTS"
    if "seated" in blob or "seat" in blob:
        return "SEATED"
    if "standing" in blob or "ga" in blob:
        return "STANDING"
    return "MAX_PERSONS"


def _flatten_qualifiers(qualifiers: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for pid, snaks in qualifiers.items():
        values: list[str] = []
        for snak in snaks or []:
            dv = (snak.get("datavalue") or {}).get("value")
            if isinstance(dv, dict):
                values.append(str(dv.get("id") or dv.get("text") or dv.get("amount") or dv))
            elif dv is not None:
                values.append(str(dv))
        if values:
            out[pid] = values
    return out
