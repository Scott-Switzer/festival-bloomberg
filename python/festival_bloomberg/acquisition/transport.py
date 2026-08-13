"""Injectable HTTP transport for acquisition providers.

Providers never talk to ``urllib`` / ``requests`` directly. They call an
injected :class:`HttpTransport`, which makes every provider testable with a
fixture-based fake and keeps paid-call safety explicit: a fake transport can
record every request and fail closed without touching the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class TransportError(RuntimeError):
    """Raised for network-level failures (DNS, refused, timeout)."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: Any = None,
        timeout_seconds: float = 30.0,
    ) -> HttpResponse: ...


class UrllibTransport:
    """Default transport backed by the standard library.

    Sets an identifying User-Agent and applies a hard timeout per request.
    HTTP error statuses are returned as :class:`HttpResponse` (so providers
    can inspect 429/401/403); network-level failures raise :class:`TransportError`.
    """

    USER_AGENT = (
        "FestivalBloomberg/0.1 (+https://github.com/Scott-Switzer/festival-bloomberg)"
    )

    def __init__(self, user_agent: str | None = None) -> None:
        self._user_agent = user_agent or self.USER_AGENT

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: Any = None,
        timeout_seconds: float = 30.0,
    ) -> HttpResponse:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", self._user_agent)
        if body is not None and not isinstance(body, bytes):
            body_bytes = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        else:
            body_bytes = body  # type: ignore[assignment]
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status=response.status,
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(
                status=exc.code,
                body=exc.read(),
                headers=dict(exc.headers.items()),
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"network failure: {exc}") from exc
