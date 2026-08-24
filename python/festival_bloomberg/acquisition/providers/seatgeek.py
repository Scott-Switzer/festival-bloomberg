"""SeatGeek Platform API v2 — official event-level aggregate stats.

The official API does not expose individual ticket listings. Records never
fabricate listing_id, section, row, seat, or quantity from aggregate stats.
``score`` is stored only as provider_score — never a Festival Bloomberg
demand or booking score.
"""

from __future__ import annotations

from typing import Any

from ...evidence.semantics import ContentRole
from ...markets.chicago import chicago_from_structured_geo
from ..automation import AutomationStatus, automation_status
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

DEFAULT_BASE_URL = "https://api.seatgeek.com/2"
PROVIDER_NAME = "seatgeek_official_api"
PROVIDER_VERSION = "seatgeek_api_v2-v1"
PARSER_VERSION = "seatgeek_event_stats-v1"
OFFICIAL_API = True

SEARCH_EVENTS = "SEARCH_EVENTS"
GET_EVENT = "GET_EVENT"
SEARCH_PERFORMERS = "SEARCH_PERFORMERS"
GET_PERFORMER = "GET_PERFORMER"
SEARCH_VENUES = "SEARCH_VENUES"
GET_VENUE = "GET_VENUE"

DEFAULT_PAGE_SIZE = 20
MAX_PAGES = 5
LISTING_FIELDS = frozenset({"listing_id", "section", "row", "seat", "quantity"})


class SeatGeekProvider(BaseProvider):
    name = "seatgeek"
    official_api = True
    module_status = "OFFICIAL_API_EVENT_STATS"

    def __init__(self, transport=None, env=None, *, base_url: str | None = None) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (base_url or self.env.get("SEATGEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

    def client_id(self) -> str | None:
        return (
            self.secret("SEATGEEK_CLIENT_ID")
            or self.secret("SEATGEEK_API_KEY")
            or self.secret("SEATGEEK_KEY")
        )

    def configured(self) -> bool:
        return self.client_id() is not None

    def health(self) -> ProviderHealth:
        if not self.configured():
            return ProviderHealth(provider=self.name, healthy=False, last_error="no SEATGEEK_CLIENT_ID")
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="free_open_api")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        disposition = automation_status("seatgeek")
        if disposition == AutomationStatus.DISABLED:
            return self._result(
                request,
                status=AcquisitionStatus.POLICY_DENIED,
                error_category="automation_disabled",
                provider_metadata={
                    "automation_status": disposition.value,
                    "rationale": "automated acquisition is disabled for this provider",
                },
            )
        if not self.configured():
            return self._not_configured(request, "SEATGEEK_CLIENT_ID not set")

        started = utc_now()
        retrieved_at = started.isoformat()
        operation = (request.operation or SEARCH_EVENTS).upper()
        try:
            if operation == GET_EVENT:
                records, meta, error = self._get_event(request, retrieved_at)
                endpoint = f"{self.base_url}/events/{request.external_id}"
            elif operation == SEARCH_PERFORMERS:
                records, meta, error = self._search_collection(
                    "performers", request, retrieved_at, self._normalize_performer
                )
                endpoint = f"{self.base_url}/performers"
            elif operation == GET_PERFORMER:
                records, meta, error = self._get_one("performers", request, retrieved_at, self._normalize_performer)
                endpoint = f"{self.base_url}/performers/{request.external_id}"
            elif operation == SEARCH_VENUES:
                records, meta, error = self._search_collection(
                    "venues", request, retrieved_at, self._normalize_venue
                )
                endpoint = f"{self.base_url}/venues"
            elif operation == GET_VENUE:
                records, meta, error = self._get_one("venues", request, retrieved_at, self._normalize_venue)
                endpoint = f"{self.base_url}/venues/{request.external_id}"
            else:
                records, meta, error = self._search_events(request, retrieved_at)
                endpoint = f"{self.base_url}/events"
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=self.base_url,
                started_at=started,
                cost_usd=0.0,
                error_category="network",
                provider_metadata={"detail": str(exc), "provider_version": PROVIDER_VERSION},
            )

        if error is not None:
            return self._result(
                request,
                status=error["status"],
                provider_endpoint=endpoint,
                started_at=started,
                cost_usd=0.0,
                error_category=error["category"],
                provider_metadata={
                    "detail": error.get("detail"),
                    "operation": operation,
                    "provider_version": PROVIDER_VERSION,
                },
            )

        status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
        return self._result(
            request,
            status=status,
            provider_endpoint=endpoint,
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of([r.get("platform_object_id") for r in records]),
            provider_metadata={
                "operation": operation,
                "pagination": meta,
                "provider_version": PROVIDER_VERSION,
                "parser_version": PARSER_VERSION,
                "exposes_individual_listings": False,
                "cost_usd": 0.0,
            },
            records=tuple(records),
        )

    def _auth_params(self) -> dict[str, str]:
        params = {"client_id": self.client_id() or ""}
        secret = self.secret("SEATGEEK_CLIENT_SECRET")
        if secret:
            params["client_secret"] = secret
        return params

    def _api_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict | None]:
        request_params = dict(self._auth_params())
        request_params.update(params or {})
        response = self.transport.request(
            "GET",
            f"{self.base_url}/{path.lstrip('/')}",
            params=request_params,
            timeout_seconds=30.0,
        )
        if response.status == 200:
            try:
                return response.json(), None
            except ValueError:
                return None, {
                    "status": AcquisitionStatus.SCHEMA_INVALID,
                    "category": "schema_invalid",
                    "detail": path,
                }
        if response.status in {401, 403}:
            return None, {
                "status": AcquisitionStatus.PROVIDER_ERROR,
                "category": "auth",
                "detail": f"http {response.status}",
            }
        if response.status == 429:
            return None, {
                "status": AcquisitionStatus.RATE_LIMITED,
                "category": "rate_limited",
                "detail": f"http {response.status}",
            }
        if response.status == 404:
            return None, {
                "status": AcquisitionStatus.NO_RESULTS,
                "category": "not_found",
                "detail": f"http {response.status}",
            }
        return None, {
            "status": AcquisitionStatus.PROVIDER_ERROR,
            "category": "http_error",
            "detail": f"http {response.status}",
        }

    def _search_events(self, request: AcquisitionRequest, retrieved_at: str):
        params: dict[str, Any] = {"per_page": str(DEFAULT_PAGE_SIZE)}
        if request.external_id:
            params["performers.id"] = request.external_id
        elif request.query:
            params["q"] = request.query
        city, state, _country = _market_parts(request.market_id)
        if city:
            params["venue.city"] = city
        if state:
            params["venue.state"] = state
        return self._paginate("events", params, retrieved_at, request, self._normalize_event, "events")

    def _get_event(self, request: AcquisitionRequest, retrieved_at: str):
        if not request.external_id:
            return [], {}, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "GET_EVENT requires external_id",
            }
        payload, error = self._api_get(f"events/{request.external_id}")
        if error is not None:
            return [], {}, error
        return [self._normalize_event(payload, retrieved_at, request)], {"pages_fetched": 1, "complete": True}, None

    def _get_one(self, resource: str, request: AcquisitionRequest, retrieved_at: str, normalize):
        if not request.external_id:
            return [], {}, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": f"GET requires external_id for {resource}",
            }
        payload, error = self._api_get(f"{resource}/{request.external_id}")
        if error is not None:
            return [], {}, error
        return [normalize(payload, retrieved_at, request)], {"pages_fetched": 1, "complete": True}, None

    def _search_collection(self, resource: str, request: AcquisitionRequest, retrieved_at: str, normalize):
        params: dict[str, Any] = {"per_page": str(DEFAULT_PAGE_SIZE)}
        if request.query:
            params["q"] = request.query
        city, state, country = _market_parts(request.market_id)
        if resource == "venues":
            if city:
                params["city"] = city
            if state:
                params["state"] = state
            if country:
                params["country"] = country
        return self._paginate(resource, params, retrieved_at, request, normalize, resource)

    def _paginate(self, path: str, params: dict[str, Any], retrieved_at: str, request, normalize, list_key: str):
        records: list[dict] = []
        pages = 0
        reported = None
        complete = False
        truncated = False
        page = 1
        while len(records) < request.max_records and pages < MAX_PAGES:
            page_params = dict(params)
            page_params["page"] = str(page)
            payload, error = self._api_get(path, page_params)
            if error is not None:
                if pages == 0:
                    return [], {}, error
                truncated = True
                break
            pages += 1
            meta = payload.get("meta") or {}
            if reported is None:
                reported = meta.get("total")
            items = payload.get(list_key) or []
            if not items:
                complete = True
                break
            for item in items:
                records.append(normalize(item, retrieved_at, request))
                if len(records) >= request.max_records:
                    truncated = True
                    break
            per_page = int(meta.get("per_page") or DEFAULT_PAGE_SIZE)
            total = int(reported or 0)
            if page * per_page >= total or len(items) < per_page:
                complete = not truncated
                break
            page += 1
        else:
            if pages >= MAX_PAGES:
                truncated = True
        meta_out = {
            "pages_fetched": pages,
            "items_fetched": len(records),
            "reported_total": reported,
            "complete": complete and not truncated,
            "coverage_status": "TRUNCATED_BY_CAP" if truncated and not complete else ("COMPLETE" if complete else "PARTIAL"),
        }
        return records[: request.max_records], meta_out, None

    def _normalize_event(self, item: dict, retrieved_at: str, request: AcquisitionRequest) -> dict:
        venue = item.get("venue") or {}
        stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        performers = item.get("performers") or []
        primary = next((p for p in performers if p.get("primary")), performers[0] if performers else {})
        city = venue.get("city")
        state = venue.get("state")
        country = venue.get("country")
        chicago = chicago_from_structured_geo(city=city, state_code=state, country_code=country)
        location = venue.get("location") or {}
        local_dt = item.get("datetime_local")
        local_date = str(local_dt)[:10] if local_dt else None
        record = {
            "platform": "seatgeek",
            "provider": PROVIDER_NAME,
            "object_type": "event",
            "platform_object_id": str(item.get("id") or ""),
            "seatgeek_event_id": item.get("id"),
            "event_name": item.get("title") or item.get("short_title"),
            "text": item.get("title"),
            "canonical_url": item.get("url"),
            "local_date": local_date,
            "event_time": item.get("datetime_utc") or local_dt,
            "datetime_local": local_dt,
            "datetime_utc": item.get("datetime_utc"),
            "time_tbd": item.get("time_tbd"),
            "venue": {
                "seatgeek_venue_id": venue.get("id"),
                "venue_name": venue.get("name"),
            },
            "seatgeek_venue_id": venue.get("id"),
            "venue_name": venue.get("name"),
            "city": city,
            "state": state,
            "state_code": state,
            "country": country,
            "country_code": country,
            "latitude": _coord(location.get("lat")),
            "longitude": _coord(location.get("lon")),
            "performers": [
                {
                    "seatgeek_performer_id": p.get("id"),
                    "performer_name": p.get("name"),
                    "primary": bool(p.get("primary")),
                }
                for p in performers
                if p.get("id") or p.get("name")
            ],
            "seatgeek_performer_id": primary.get("id"),
            "listing_count": _num(stats.get("listing_count")),
            "average_price": _num(stats.get("average_price")),
            "lowest_price": _num(stats.get("lowest_price")),
            "highest_price": _num(stats.get("highest_price")),
            "median_price": _num(stats["median_price"]) if "median_price" in stats else None,
            "provider_score": _num(item.get("score")),
            "stats": {k: stats[k] for k in stats if k in {"listing_count", "average_price", "lowest_price", "highest_price", "median_price"}},
            "market_id": chicago.market_id,
            "market_context_method": chicago.method,
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "content_role": ContentRole.TICKET_LISTING.value,
            "content_role_method": "provider_object_type",
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of({"id": item.get("id"), "stats": stats, "title": item.get("title")}),
        }
        for forbidden in LISTING_FIELDS:
            record.pop(forbidden, None)
        return record

    def _normalize_performer(self, item: dict, retrieved_at: str, request: AcquisitionRequest) -> dict:
        return {
            "platform": "seatgeek",
            "provider": PROVIDER_NAME,
            "object_type": "performer",
            "platform_object_id": str(item.get("id") or ""),
            "seatgeek_performer_id": item.get("id"),
            "performer_name": item.get("name"),
            "text": item.get("name"),
            "canonical_url": item.get("url"),
            "provider_score": _num(item.get("score")),
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "content_role": ContentRole.OFFICIAL_PROMOTIONAL.value,
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of({"id": item.get("id"), "name": item.get("name")}),
        }

    def _normalize_venue(self, item: dict, retrieved_at: str, request: AcquisitionRequest) -> dict:
        city = item.get("city")
        state = item.get("state")
        country = item.get("country")
        chicago = chicago_from_structured_geo(city=city, state_code=state, country_code=country)
        location = item.get("location") or {}
        return {
            "platform": "seatgeek",
            "provider": PROVIDER_NAME,
            "object_type": "venue",
            "platform_object_id": str(item.get("id") or ""),
            "seatgeek_venue_id": item.get("id"),
            "venue_name": item.get("name"),
            "text": item.get("name"),
            "city": city,
            "state": state,
            "state_code": state,
            "country": country,
            "country_code": country,
            "latitude": _coord(location.get("lat")),
            "longitude": _coord(location.get("lon")),
            "provider_score": _num(item.get("score")),
            "canonical_url": item.get("url"),
            "market_id": chicago.market_id,
            "market_context_method": chicago.method,
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "content_role": ContentRole.EVENT_LISTING.value,
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of({"id": item.get("id"), "name": item.get("name")}),
        }


def _market_parts(market_id: str | None) -> tuple[str | None, str | None, str | None]:
    if not market_id:
        return None, None, None
    parts = [p.strip() for p in market_id.split(",") if p.strip()]
    city = parts[0] if parts else None
    state = parts[1] if len(parts) >= 2 else None
    country = parts[2] if len(parts) >= 3 else "US"
    if state and state.upper() in {"IL", "ILLINOIS"}:
        state = "IL"
    return city, state, country


def _coord(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number
