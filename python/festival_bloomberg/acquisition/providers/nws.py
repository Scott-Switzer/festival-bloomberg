"""National Weather Service (api.weather.gov) forecast provider.

Key-free public API. Each acquire() call returns the CURRENT forecast grid
snapshot for a (lat, lon) coordinate. Forecast semantics are preserved:

- ``generation_time`` — when the forecast was generated (the observation time
  of the FORECAST, not the weather).
- ``valid_time`` per period — when the forecast applies.

A forecast observed today is a TODAY snapshot; realized weather must never
leak into an earlier historical forecast state (enforced by construction:
every record carries the forecast generation time + retrieval time, and the
forecast period's own start/end are the validity window).

``request.query`` is ``"<lat>,<lon>"`` (e.g. ``"41.8781,-87.6298"``).
"""

from __future__ import annotations

import json
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

POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
USER_AGENT = "FestivalBloomberg/0.1 (research; weather-context)"


class NwsProvider(BaseProvider):
    name = "nws"

    def health(self) -> ProviderHealth:
        # Key-free: always healthy at the credential level.
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        return True

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(
            provider=self.name, estimated_cost_usd=0.0, free_quota=True,
            source="open_endpoint",
        )

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        latlon = (request.query or "").strip()
        try:
            lat_s, lon_s = latlon.split(",", 1)
            float(lat_s), float(lon_s)
        except ValueError:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=POINTS_URL.format(lat="?", lon="?"),
                started_at=started,
                cost_usd=0.0,
                error_category="request_invalid",
                provider_metadata={"detail": "query must be '<lat>,<lon>'"},
            )

        points_url = POINTS_URL.format(lat=lat_s.strip(), lon=lon_s.strip())
        try:
            points = self._get_json(points_url)
        except (TransportError, ValueError) as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=points_url,
                started_at=started,
                cost_usd=0.0,
                error_category="network" if isinstance(exc, TransportError) else "schema_invalid",
                provider_metadata={"detail": str(exc)},
            )
        forecast_url = (points.get("properties") or {}).get("forecast")
        if not forecast_url:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=points_url,
                started_at=started,
                cost_usd=0.0,
                error_category="schema_invalid",
                provider_metadata={"detail": "no forecast link in points response"},
            )

        try:
            forecast = self._get_json(forecast_url)
        except (TransportError, ValueError) as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=forecast_url,
                started_at=started,
                cost_usd=0.0,
                error_category="network" if isinstance(exc, TransportError) else "schema_invalid",
                provider_metadata={"detail": str(exc)},
            )

        props = forecast.get("properties") or {}
        generation_time = props.get("generatedAt") or props.get("updated")
        periods = props.get("periods") or []
        records = []
        for p in periods:
            records.append(
                {
                    "platform": "nws",
                    "object_type": "weather_forecast_period",
                    "platform_object_id": f"{lat_s.strip()},{lon_s.strip()}:{p.get('number')}",
                    "generation_time": generation_time,          # forecast observation time
                    "valid_start": p.get("startTime"),
                    "valid_end": p.get("endTime"),
                    "temperature": p.get("temperature"),
                    "temperature_unit": p.get("temperatureUnit"),
                    "precipitation_probability": p.get("probabilityOfPrecipitation", {}).get("value")
                    if isinstance(p.get("probabilityOfPrecipitation"), dict) else None,
                    "wind_speed": p.get("windSpeed"),
                    "short_forecast": p.get("shortForecast"),
                    "detailed_forecast": p.get("detailedForecast"),
                    "source_url": forecast_url,
                    "retrieved_at": utc_now().isoformat(),
                    "knowledge_time": utc_now().isoformat(),
                    "content_role": "weather_forecast",
                }
            )

        status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
        return self._result(
            request,
            status=status,
            provider_endpoint=forecast_url,
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of(records),
            provider_metadata={
                "provider_version": "nws-v1",
                "generation_time": generation_time,
                "lat": lat_s.strip(),
                "lon": lon_s.strip(),
            },
            records=tuple(records),
        )

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self.transport.request(
            "GET", url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json,application/json"},
            timeout_seconds=30.0,
        )
        if response.status == 429:
            raise TransportError(f"rate limited: {url}")
        if response.status != 200:
            raise TransportError(f"http {response.status}: {url}")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON from {url}") from exc
        return payload
