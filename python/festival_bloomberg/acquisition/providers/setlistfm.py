"""Setlist.fm API v1 provider.

Official historical performance evidence. ``eventDate`` is EVENT_TIME.
``lastUpdated`` is SOURCE_UPDATED_AT, never knowledge_time. Live retrievals
use retrieval-time knowledge. Content role is PERFORMANCE_HISTORY, never
FAN_GENERATED.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...evidence.semantics import ContentRole
from ...markets.chicago import chicago_from_structured_geo
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

DEFAULT_BASE_URL = "https://api.setlist.fm/rest/1.0"
PROVIDER_NAME = "setlistfm_official_api"
PROVIDER_VERSION = "setlistfm_api_v1-v1"
PARSER_VERSION = "setlistfm_setlist-v1"

SEARCH_ARTISTS = "SEARCH_ARTISTS"
GET_ARTIST = "GET_ARTIST"
SEARCH_SETLISTS = "SEARCH_SETLISTS"
GET_ARTIST_SETLISTS = "GET_ARTIST_SETLISTS"
SEARCH_VENUES = "SEARCH_VENUES"
GET_VENUE_SETLISTS = "GET_VENUE_SETLISTS"

MAX_PAGES = 25
ITEMS_PER_PAGE = 20


class SetlistFmProvider(BaseProvider):
    name = "setlistfm"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        base_url: str | None = None,
        throttle_seconds: float = 0.0,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (base_url or self.env.get("SETLISTFM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.throttle_seconds = throttle_seconds

    def health(self) -> ProviderHealth:
        if self.secret("SETLISTFM_API_KEY") is None:
            return ProviderHealth(provider=self.name, healthy=False, last_error="no SETLISTFM_API_KEY")
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="free_tier")

    def configured(self) -> bool:
        return self.secret("SETLISTFM_API_KEY") is not None

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        if self.secret("SETLISTFM_API_KEY") is None:
            return self._not_configured(request, "SETLISTFM_API_KEY not set")

        started = utc_now()
        retrieved_at = started.isoformat()
        operation = (request.operation or SEARCH_SETLISTS).upper()
        try:
            if operation == SEARCH_ARTISTS:
                records, meta, error = self._search_artists(request, retrieved_at)
                endpoint = f"{self.base_url}/search/artists"
            elif operation == GET_ARTIST:
                records, meta, error = self._get_artist(request, retrieved_at)
                endpoint = f"{self.base_url}/artist/{request.external_id}"
            elif operation == GET_ARTIST_SETLISTS:
                records, meta, error = self._artist_setlists(request, retrieved_at)
                endpoint = f"{self.base_url}/artist/{request.external_id}/setlists"
            elif operation == SEARCH_VENUES:
                records, meta, error = self._search_venues(request, retrieved_at)
                endpoint = f"{self.base_url}/search/venues"
            elif operation == GET_VENUE_SETLISTS:
                records, meta, error = self._venue_setlists(request, retrieved_at)
                endpoint = f"{self.base_url}/venue/{request.external_id}/setlists"
            else:
                records, meta, error = self._search_setlists(request, retrieved_at)
                endpoint = f"{self.base_url}/search/setlists"
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
                    "pagination": meta,
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

    def _headers(self) -> dict[str, str]:
        key = self.secret("SETLISTFM_API_KEY")
        return {
            "Accept": "application/json",
            "x-api-key": key or "",
            "User-Agent": "FestivalBloomberg/0.1 (research; +https://github.com/Scott-Switzer/festival-bloomberg)",
        }

    def _api_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict | None]:
        if self.throttle_seconds:
            import time

            time.sleep(self.throttle_seconds)
        response = self.transport.request(
            "GET",
            f"{self.base_url}/{path.lstrip('/')}",
            headers=self._headers(),
            params=params,
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

    def _search_artists(self, request: AcquisitionRequest, retrieved_at: str):
        params = {"artistName": request.query, "p": "1", "sort": "relevance"}
        payload, error = self._api_get("search/artists", params)
        if error is not None:
            return [], {}, error
        artists = payload.get("artist") or payload.get("artists") or []
        records = [self._normalize_artist(item, retrieved_at) for item in artists]
        meta = {
            "pages_fetched": 1,
            "items_fetched": len(records),
            "reported_total": payload.get("total"),
            "complete": True,
            "coverage_status": "COMPLETE",
        }
        return records[: request.max_records], meta, None

    def _get_artist(self, request: AcquisitionRequest, retrieved_at: str):
        if not request.external_id:
            return [], {}, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "GET_ARTIST requires external_id (mbid)",
            }
        payload, error = self._api_get(f"artist/{request.external_id}")
        if error is not None:
            return [], {}, error
        return [self._normalize_artist(payload, retrieved_at)], {"pages_fetched": 1, "complete": True}, None

    def _search_setlists(self, request: AcquisitionRequest, retrieved_at: str):
        params: dict[str, Any] = {}
        if request.external_id:
            params["artistMbid"] = request.external_id
        elif request.query:
            params["artistName"] = request.query
        city, state, country = _market_parts(request.market_id)
        if city:
            params["cityName"] = city
        if state:
            params["stateCode"] = state
        if country:
            params["countryCode"] = country
        return self._paginate_setlists("search/setlists", params, retrieved_at, request)

    def _artist_setlists(self, request: AcquisitionRequest, retrieved_at: str):
        if not request.external_id:
            return [], {}, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "GET_ARTIST_SETLISTS requires external_id (mbid)",
            }
        return self._paginate_setlists(
            f"artist/{request.external_id}/setlists",
            {},
            retrieved_at,
            request,
        )

    def _search_venues(self, request: AcquisitionRequest, retrieved_at: str):
        params: dict[str, Any] = {}
        if request.query:
            params["venueName"] = request.query
        city, state, country = _market_parts(request.market_id)
        if city:
            params["cityName"] = city
        if state:
            params["stateCode"] = state
        if country:
            params["countryCode"] = country
        payload, error = self._api_get("search/venues", params)
        if error is not None:
            return [], {}, error
        venues = payload.get("venue") or payload.get("venues") or []
        records = [self._normalize_venue(item, retrieved_at) for item in venues]
        meta = {
            "pages_fetched": 1,
            "items_fetched": len(records),
            "reported_total": payload.get("total"),
            "complete": True,
            "coverage_status": "COMPLETE",
        }
        return records[: request.max_records], meta, None

    def _venue_setlists(self, request: AcquisitionRequest, retrieved_at: str):
        if not request.external_id:
            return [], {}, {
                "status": AcquisitionStatus.SCHEMA_INVALID,
                "category": "request_invalid",
                "detail": "GET_VENUE_SETLISTS requires external_id",
            }
        return self._paginate_setlists(
            f"venue/{request.external_id}/setlists",
            {},
            retrieved_at,
            request,
        )

    def _paginate_setlists(
        self,
        path: str,
        params: dict[str, Any],
        retrieved_at: str,
        request: AcquisitionRequest,
    ):
        records: list[dict] = []
        pages = 0
        reported = None
        complete = False
        truncated = False
        page = 1
        while len(records) < request.max_records and pages < MAX_PAGES:
            page_params = dict(params)
            page_params["p"] = str(page)
            payload, error = self._api_get(path, page_params)
            if error is not None:
                if pages == 0:
                    return [], {}, error
                truncated = True
                break
            pages += 1
            if reported is None:
                reported = payload.get("total")
            items = payload.get("setlist") or payload.get("setlists") or []
            if not items:
                complete = True
                break
            for item in items:
                records.append(self._normalize_setlist(item, retrieved_at))
                if len(records) >= request.max_records:
                    truncated = True
                    break
            items_per_page = int(payload.get("itemsPerPage") or ITEMS_PER_PAGE)
            total = int(payload.get("total") or 0)
            if page * items_per_page >= total:
                complete = not truncated
                break
            page += 1
        else:
            if pages >= MAX_PAGES:
                truncated = True
        if truncated and reported is not None and len(records) < int(reported):
            coverage = "TRUNCATED_BY_CAP"
            complete = False
        elif complete:
            coverage = "COMPLETE"
        else:
            coverage = "PARTIAL"
        meta = {
            "pages_fetched": pages,
            "items_fetched": len(records),
            "reported_total": reported,
            "complete": complete,
            "coverage_status": coverage,
        }
        return records[: request.max_records], meta, None

    def _normalize_artist(self, item: dict, retrieved_at: str) -> dict:
        return {
            "platform": "setlistfm",
            "provider": PROVIDER_NAME,
            "object_type": "artist",
            "platform_object_id": item.get("mbid"),
            "artist_mbid": item.get("mbid"),
            "artist_name": item.get("name"),
            "artist_sort_name": item.get("sortName"),
            "artist_disambiguation": item.get("disambiguation"),
            "text": item.get("name"),
            "canonical_url": item.get("url"),
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "content_role": ContentRole.PERFORMANCE_HISTORY.value,
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of({"mbid": item.get("mbid"), "name": item.get("name")}),
        }

    def _normalize_venue(self, item: dict, retrieved_at: str) -> dict:
        city_obj = item.get("city") or {}
        country = city_obj.get("country") or {}
        chicago = chicago_from_structured_geo(
            city=city_obj.get("name"),
            state_code=city_obj.get("stateCode"),
            state=city_obj.get("state"),
            country_code=country.get("code"),
            country=country.get("name"),
        )
        coords = city_obj.get("coords") or {}
        return {
            "platform": "setlistfm",
            "provider": PROVIDER_NAME,
            "object_type": "venue",
            "platform_object_id": item.get("id"),
            "venue_id": item.get("id"),
            "venue_name": item.get("name"),
            "city_id": city_obj.get("id"),
            "city": city_obj.get("name"),
            "state": city_obj.get("state"),
            "state_code": city_obj.get("stateCode"),
            "country_code": country.get("code"),
            "latitude": _coord(coords.get("lat")),
            "longitude": _coord(coords.get("long")),
            "market_id": chicago.market_id,
            "market_context_method": chicago.method,
            "canonical_url": item.get("url"),
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "content_role": ContentRole.PERFORMANCE_HISTORY.value,
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of({"id": item.get("id"), "name": item.get("name")}),
        }

    def _normalize_setlist(self, item: dict, retrieved_at: str) -> dict:
        artist = item.get("artist") or {}
        venue = item.get("venue") or {}
        city_obj = venue.get("city") or {}
        country = city_obj.get("country") or {}
        tour = item.get("tour") or {}
        sets = item.get("set") or []
        chicago = chicago_from_structured_geo(
            city=city_obj.get("name"),
            state_code=city_obj.get("stateCode"),
            state=city_obj.get("state"),
            country_code=country.get("code"),
            country=country.get("name"),
        )
        coords = city_obj.get("coords") or {}
        event_date = parse_setlist_event_date(item.get("eventDate"))
        last_updated = parse_setlist_last_updated(item.get("lastUpdated"))
        set_count, song_count, encore_count = set_stats(sets)
        return {
            "platform": "setlistfm",
            "provider": PROVIDER_NAME,
            "object_type": "setlist",
            "platform_object_id": item.get("id"),
            "setlist_id": item.get("id"),
            "version_id": item.get("versionId"),
            "source_revision_id": item.get("versionId"),
            "artist_name": artist.get("name"),
            "artist_mbid": artist.get("mbid"),
            "artist_sort_name": artist.get("sortName"),
            "artist_disambiguation": artist.get("disambiguation"),
            "event_time": event_date,
            "local_date": event_date,
            "source_updated_at": last_updated,
            "venue_id": venue.get("id"),
            "venue_name": venue.get("name"),
            "city_id": city_obj.get("id"),
            "city": city_obj.get("name"),
            "state": city_obj.get("state"),
            "state_code": city_obj.get("stateCode"),
            "country_code": country.get("code"),
            "latitude": _coord(coords.get("lat")),
            "longitude": _coord(coords.get("long")),
            "tour_name": tour.get("name"),
            "set_count": set_count,
            "song_count": song_count,
            "encore_count": encore_count,
            "event_type": "TOUR_DATE" if tour.get("name") else "UNKNOWN",
            "festival_name": None,
            "canonical_url": item.get("url"),
            "text": item.get("info") or item.get("url"),
            "market_id": chicago.market_id,
            "market_context_method": chicago.method,
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "published_at": None,
            "content_role": ContentRole.PERFORMANCE_HISTORY.value,
            "content_role_method": "provider_object_type",
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of(
                {"id": item.get("id"), "versionId": item.get("versionId"), "eventDate": item.get("eventDate")}
            ),
        }


def parse_setlist_event_date(value: str | None) -> str | None:
    """Setlist eventDate is dd-MM-yyyy. Store as ISO date when parseable."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%d-%m-%Y")
        return parsed.date().isoformat()
    except ValueError:
        return value


def parse_setlist_last_updated(value: str | None) -> str | None:
    if not value:
        return None
    try:
        cleaned = value.replace("+0000", "+00:00")
        return datetime.fromisoformat(cleaned).isoformat()
    except ValueError:
        return value


def set_stats(sets: list) -> tuple[int | None, int | None, int | None]:
    if not isinstance(sets, list):
        return None, None, None
    set_count = len(sets)
    song_count = 0
    encore_count = 0
    for block in sets:
        if not isinstance(block, dict):
            continue
        songs = block.get("song") or []
        if isinstance(songs, list):
            song_count += len(songs)
        if block.get("encore"):
            encore_count += 1
    return set_count, song_count, encore_count


def _market_parts(market_id: str | None) -> tuple[str | None, str | None, str | None]:
    if not market_id:
        return None, None, None
    parts = [p.strip() for p in market_id.split(",") if p.strip()]
    city = parts[0] if parts else None
    state = "IL" if len(parts) >= 2 and parts[1].upper() in {"IL", "ILLINOIS"} else (parts[1] if len(parts) >= 2 else None)
    country = parts[2] if len(parts) >= 3 else "US"
    if country and country.upper() in {"US", "USA", "UNITED STATES"}:
        country = "US"
    return city, state, country


def _coord(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
