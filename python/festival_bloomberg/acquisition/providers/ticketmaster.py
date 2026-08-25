"""Ticketmaster Discovery API v2 provider.

Official read-only events / attractions / venues. Event start time is
EVENT_TIME, never knowledge_time. Live retrievals use retrieval-time
knowledge. Price ranges are listed observations, not realized prices.
"""

from __future__ import annotations

from typing import Any

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
from ...evidence.semantics import ContentRole
from ...markets.chicago import chicago_from_structured_geo

DEFAULT_BASE_URL = "https://app.ticketmaster.com/discovery/v2"
PROVIDER_NAME = "ticketmaster_official_api"
PROVIDER_VERSION = "ticketmaster_discovery_v2-v1"
PARSER_VERSION = "ticketmaster_event-v1"

SEARCH_ATTRACTIONS = "SEARCH_ATTRACTIONS"
GET_ATTRACTION = "GET_ATTRACTION"
SEARCH_EVENTS = "SEARCH_EVENTS"
GET_EVENT = "GET_EVENT"
SEARCH_VENUES = "SEARCH_VENUES"
GET_VENUE = "GET_VENUE"

DEFAULT_PAGE_SIZE = 50
#: Ticketmaster's official deep-paging ceiling: results stop being served at
#: the 1000th item. A partition whose reported total exceeds this must be
#: SPLIT (by date window) rather than silently truncated. This is a provider
#: constraint, not our own cap.
RETRIEVAL_CEILING = 1000


class TicketmasterProvider(BaseProvider):
    name = "ticketmaster"

    def __init__(self, transport=None, env=None, *, base_url: str | None = None) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (base_url or self.env.get("TICKETMASTER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

    def health(self) -> ProviderHealth:
        if self.secret("TICKETMASTER_API_KEY") is None:
            return ProviderHealth(provider=self.name, healthy=False, last_error="no TICKETMASTER_API_KEY")
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="free_tier")

    def configured(self) -> bool:
        return self.secret("TICKETMASTER_API_KEY") is not None

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        if self.secret("TICKETMASTER_API_KEY") is None:
            return self._not_configured(request, "TICKETMASTER_API_KEY not set")

        started = utc_now()
        retrieved_at = started.isoformat()
        operation = (request.operation or SEARCH_EVENTS).upper()
        try:
            if operation == SEARCH_ATTRACTIONS:
                records, meta, error = self._search_attractions(request, retrieved_at)
                endpoint = f"{self.base_url}/attractions.json"
            elif operation == GET_ATTRACTION:
                records, meta, error = self._get_attraction(request, retrieved_at)
                endpoint = f"{self.base_url}/attractions/{request.external_id}.json"
            elif operation == SEARCH_VENUES:
                records, meta, error = self._search_venues(request, retrieved_at)
                endpoint = f"{self.base_url}/venues.json"
            elif operation == GET_VENUE:
                records, meta, error = self._get_venue(request, retrieved_at)
                endpoint = f"{self.base_url}/venues/{request.external_id}.json"
            elif operation == GET_EVENT:
                records, meta, error = self._get_event(request, retrieved_at)
                endpoint = f"{self.base_url}/events/{request.external_id}.json"
            else:
                records, meta, error = self._search_events(request, retrieved_at)
                endpoint = f"{self.base_url}/events.json"
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
                "cost_usd": 0.0,
            },
            records=tuple(records),
        )

    def _api_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict | None]:
        key = self.secret("TICKETMASTER_API_KEY")
        request_params = dict(params or {})
        request_params["apikey"] = key
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
        params: dict[str, Any] = {"size": str(DEFAULT_PAGE_SIZE), "sort": "date,asc"}
        if request.external_id:
            params["attractionId"] = request.external_id
        elif request.query:
            params["keyword"] = request.query
        city, state, country = _market_parts(request.market_id)
        if city:
            params["city"] = city
        if state:
            params["stateCode"] = state
        if country:
            params["countryCode"] = country
        # Classification filter (e.g. "Music") and date windows, when requested.
        if getattr(request, "classification_name", None):
            params["classificationName"] = request.classification_name
        if getattr(request, "start_time", None):
            params["startDateTime"] = _tm_datetime(request.start_time)
        if getattr(request, "end_time", None):
            params["endDateTime"] = _tm_datetime(request.end_time)
        return self._paginate_embedded(
            "events.json",
            params,
            embedded_key="events",
            normalize=lambda item: self._normalize_event(item, retrieved_at, request),
            max_records=request.max_records,
        )

    def _get_event(self, request: AcquisitionRequest, retrieved_at: str):
        if not request.external_id:
            return [], {}, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "GET_EVENT requires external_id",
            }
        payload, error = self._api_get(f"events/{request.external_id}.json")
        if error is not None:
            return [], {}, error
        return [self._normalize_event(payload, retrieved_at, request)], {"pages_fetched": 1, "complete": True}, None

    def _search_attractions(self, request: AcquisitionRequest, retrieved_at: str):
        params: dict[str, Any] = {"size": str(DEFAULT_PAGE_SIZE)}
        if request.query:
            params["keyword"] = request.query
        return self._paginate_embedded(
            "attractions.json",
            params,
            embedded_key="attractions",
            normalize=lambda item: self._normalize_attraction(item, retrieved_at, request),
            max_records=request.max_records,
        )

    def _get_attraction(self, request: AcquisitionRequest, retrieved_at: str):
        if not request.external_id:
            return [], {}, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "GET_ATTRACTION requires external_id",
            }
        payload, error = self._api_get(f"attractions/{request.external_id}.json")
        if error is not None:
            return [], {}, error
        return [self._normalize_attraction(payload, retrieved_at, request)], {"pages_fetched": 1, "complete": True}, None

    def _search_venues(self, request: AcquisitionRequest, retrieved_at: str):
        params: dict[str, Any] = {"size": str(DEFAULT_PAGE_SIZE)}
        if request.query:
            params["keyword"] = request.query
        city, state, country = _market_parts(request.market_id)
        if city:
            params["city"] = city
        if state:
            params["stateCode"] = state
        if country:
            params["countryCode"] = country
        return self._paginate_embedded(
            "venues.json",
            params,
            embedded_key="venues",
            normalize=lambda item: self._normalize_venue(item, retrieved_at, request),
            max_records=request.max_records,
        )

    def _get_venue(self, request: AcquisitionRequest, retrieved_at: str):
        if not request.external_id:
            return [], {}, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "GET_VENUE requires external_id",
            }
        payload, error = self._api_get(f"venues/{request.external_id}.json")
        if error is not None:
            return [], {}, error
        return [self._normalize_venue(payload, retrieved_at, request)], {"pages_fetched": 1, "complete": True}, None

    def _paginate_embedded(
        self,
        path: str,
        params: dict[str, Any],
        *,
        embedded_key: str,
        normalize,
        max_records: int,
    ):
        records: list[dict] = []
        pages = 0
        reported = None
        complete = False
        truncated = False
        page_number = 0
        # Honor the caller's cap, but never page beyond the provider's own
        # deep-paging ceiling. A caller wanting full coverage passes
        # max_records >= RETRIEVAL_CEILING and the driver SPLITS any partition
        # whose reported total exceeds the ceiling.
        ceiling = max(1, int(max_records or RETRIEVAL_CEILING))
        while True:
            page_params = dict(params)
            page_params["page"] = str(page_number)
            payload, error = self._api_get(path, page_params)
            if error is not None:
                if pages == 0:
                    return [], {}, error
                truncated = True
                break
            pages += 1
            page_info = payload.get("page") or {}
            if reported is None:
                reported = page_info.get("totalElements")
            items = ((payload.get("_embedded") or {}).get(embedded_key)) or []
            if not items:
                # Empty page: complete only if we have already seen every
                # reported item; otherwise the API stopped serving early
                # (e.g. the deep-paging ceiling) and this is truncation.
                complete = (reported is None) or (len(records) >= int(reported))
                truncated = not complete
                break
            for item in items:
                records.append(normalize(item))
                if len(records) >= ceiling:
                    break
            total_pages = page_info.get("totalPages")
            if total_pages is not None and page_number + 1 >= int(total_pages):
                # Reached the provider-reported final page.
                complete = True
                truncated = False
                break
            if len(records) >= ceiling:
                # Stopped on our own ceiling before the reported end.
                truncated = True
                break
            page_number += 1
        meta = {
            "pages_fetched": pages,
            "items_fetched": len(records),
            "reported_total": reported,
            "complete": complete and not truncated,
            "truncated": truncated,
            "coverage_status": "TRUNCATED_BY_CAP" if (truncated and not complete) else ("COMPLETE" if complete else "PARTIAL"),
        }
        return records[:ceiling], meta, None

    def _normalize_event(self, item: dict, retrieved_at: str, request: AcquisitionRequest) -> dict:
        venue = _first(((item.get("_embedded") or {}).get("venues")) or [])
        attractions = ((item.get("_embedded") or {}).get("attractions")) or []
        dates = item.get("dates") or {}
        start = dates.get("start") or {}
        status = (dates.get("status") or {}).get("code")
        sales_obj = item.get("sales") or {}
        sales = sales_obj.get("public") or {}
        presales = sales_obj.get("presales") if isinstance(sales_obj.get("presales"), list) else []
        classifications = item.get("classifications") or []
        primary = next((c for c in classifications if c.get("primary")), classifications[0] if classifications else {})
        city = (venue.get("city") or {}).get("name") if venue else None
        state = (venue.get("state") or {}).get("name") if venue else None
        state_code = (venue.get("state") or {}).get("stateCode") if venue else None
        country = (venue.get("country") or {}).get("name") if venue else None
        country_code = (venue.get("country") or {}).get("countryCode") if venue else None
        location = (venue.get("location") or {}) if venue else {}
        chicago = chicago_from_structured_geo(city=city, state_code=state_code, country_code=country_code)
        event_type = classify_ticketmaster_event_type(item)
        local_date = start.get("localDate")
        event_time = start.get("dateTime") or local_date
        price_ranges = item.get("priceRanges") if isinstance(item.get("priceRanges"), list) else None
        first_price = price_ranges[0] if price_ranges else None
        return {
            "platform": "ticketmaster",
            "provider": PROVIDER_NAME,
            "object_type": "event",
            "platform_object_id": item.get("id"),
            "ticketmaster_event_id": item.get("id"),
            "event_name": item.get("name"),
            "text": item.get("name"),
            "attractions": [
                {
                    "ticketmaster_attraction_id": a.get("id"),
                    "attraction_name": a.get("name"),
                }
                for a in attractions
                if a.get("id") or a.get("name")
            ],
            "ticketmaster_attraction_id": (attractions[0].get("id") if attractions else None),
            "venue": {
                "ticketmaster_venue_id": venue.get("id") if venue else None,
                "venue_name": venue.get("name") if venue else None,
            },
            "ticketmaster_venue_id": venue.get("id") if venue else None,
            "venue_name": venue.get("name") if venue else None,
            "city": city,
            "state": state,
            "state_code": state_code,
            "country": country,
            "country_code": country_code,
            "latitude": _coord(location.get("latitude")),
            "longitude": _coord(location.get("longitude")),
            "local_date": local_date,
            "local_time": start.get("localTime"),
            "event_time": event_time,
            "timezone": dates.get("timezone"),
            "event_status": status,
            "onsale_start": sales.get("startDateTime"),
            "onsale_end": sales.get("endDateTime"),
            "presales": presales,
            "price_min": (first_price or {}).get("min"),
            "price_max": (first_price or {}).get("max"),
            "price_currency": (first_price or {}).get("currency"),
            "price_type": (first_price or {}).get("type"),
            "classifications": {
                "segment": ((primary.get("segment") or {}).get("name")),
                "segment_id": ((primary.get("segment") or {}).get("id")),
                "genre": ((primary.get("genre") or {}).get("name")),
                "genre_id": ((primary.get("genre") or {}).get("id")),
                "subgenre": ((primary.get("subgenre") or {}).get("name")),
                "subgenre_id": ((primary.get("subgenre") or {}).get("id")),
                "type": ((primary.get("type") or {}).get("name")),
                "family": (primary.get("segment") or {}).get("family"),
            },
            "promoter": ((item.get("promoter") or {}).get("name")) or None,
            "price_ranges": price_ranges,
            "canonical_url": item.get("url"),
            "source_origin": item.get("source"),
            "event_type": event_type,
            "festival_name": item.get("name") if event_type == "FESTIVAL_APPEARANCE" else None,
            "market_id": chicago.market_id,
            "market_context_method": chicago.method,
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "published_at": None,
            "content_role": ContentRole.EVENT_LISTING.value,
            "content_role_method": "provider_object_type",
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of({"id": item.get("id"), "name": item.get("name"), "date": local_date}),
        }

    def _normalize_attraction(self, item: dict, retrieved_at: str, request: AcquisitionRequest) -> dict:
        return {
            "platform": "ticketmaster",
            "provider": PROVIDER_NAME,
            "object_type": "attraction",
            "platform_object_id": item.get("id"),
            "ticketmaster_attraction_id": item.get("id"),
            "attraction_name": item.get("name"),
            "text": item.get("name"),
            "canonical_url": item.get("url"),
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "content_role": ContentRole.OFFICIAL_PROMOTIONAL.value,
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of({"id": item.get("id"), "name": item.get("name")}),
        }

    def _normalize_venue(self, item: dict, retrieved_at: str, request: AcquisitionRequest) -> dict:
        city = (item.get("city") or {}).get("name")
        state_code = (item.get("state") or {}).get("stateCode")
        country_code = (item.get("country") or {}).get("countryCode")
        chicago = chicago_from_structured_geo(city=city, state_code=state_code, country_code=country_code)
        location = item.get("location") or {}
        return {
            "platform": "ticketmaster",
            "provider": PROVIDER_NAME,
            "object_type": "venue",
            "platform_object_id": item.get("id"),
            "ticketmaster_venue_id": item.get("id"),
            "venue_name": item.get("name"),
            "text": item.get("name"),
            "city": city,
            "state": (item.get("state") or {}).get("name"),
            "state_code": state_code,
            "country": (item.get("country") or {}).get("name"),
            "country_code": country_code,
            "latitude": _coord(location.get("latitude")),
            "longitude": _coord(location.get("longitude")),
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


def classify_ticketmaster_event_type(item: dict) -> str:
    """Festival only when the source classification type is explicit."""
    for classification in item.get("classifications") or []:
        type_name = ((classification.get("type") or {}).get("name") or "").strip().lower()
        if type_name == "festival":
            return "FESTIVAL_APPEARANCE"
    return "UNKNOWN"


def _tm_datetime(value) -> str:
    """Format a datetime the way the Ticketmaster Discovery API expects."""
    from datetime import timezone

    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _market_parts(market_id: str | None) -> tuple[str | None, str | None, str | None]:
    if not market_id:
        return None, None, None
    parts = [p.strip() for p in market_id.split(",") if p.strip()]
    city = parts[0] if parts else None
    state = None
    country = "US"
    if len(parts) >= 2:
        state = parts[1]
        if state.upper() in {"IL", "ILLINOIS"}:
            state = "IL"
    if len(parts) >= 3:
        country = parts[2]
    return city, state, country


def _first(items: list) -> dict:
    return items[0] if items else {}


def _coord(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
