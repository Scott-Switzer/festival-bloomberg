"""OpenAI-compatible Hetzner Experiments Inference API client with provider fallback."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


Message = Mapping[str, Any]


class HetznerLLMError(RuntimeError):
    """Raised when an inference request fails and no fallback succeeds."""

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 provider: str = "hetzner", response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.response_body = response_body


@dataclass(frozen=True)
class Provider:
    name: str
    api_key: str
    base_url: str
    model: Optional[str] = None


class HetznerLLMClient:
    """Small dependency-free Chat Completions client.

    Hetzner is attempted first. Configure fallback providers with either
    ``fallbacks=[Provider(...)]`` or environment variables:
    OPENAI_API_KEY / OPENAI_FALLBACK_MODEL and OPENROUTER_API_KEY /
    OPENROUTER_FALLBACK_MODEL. Fallbacks are used for rate-limit and
    transient server/network failures (and may be enabled for all failures).
    """

    DEFAULT_BASE_URL = "https://inference.hetzner.com/api/v1"
    RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        retries: int = 2,
        fallback: bool = True,
        fallback_on_all_errors: bool = False,
        fallbacks: Optional[Sequence[Provider]] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("HETZNER_VLLM_API_KEY") or os.getenv("HETZNER_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        self.fallback = fallback
        self.fallback_on_all_errors = fallback_on_all_errors
        self.fallbacks = list(fallbacks) if fallbacks is not None else self._env_fallbacks()

    @staticmethod
    def _env_fallbacks() -> List[Provider]:
        providers: List[Provider] = []
        if os.getenv("OPENAI_API_KEY"):
            providers.append(Provider("openai", os.environ["OPENAI_API_KEY"],
                                      os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                                      os.getenv("OPENAI_FALLBACK_MODEL", "gpt-4o-mini")))
        if os.getenv("OPENROUTER_API_KEY"):
            providers.append(Provider("openrouter", os.environ["OPENROUTER_API_KEY"],
                                      os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                                      os.getenv("OPENROUTER_FALLBACK_MODEL", "openai/gpt-4o-mini")))
        return providers

    def models(self) -> Dict[str, Any]:
        """Return the authenticated provider's model-list response."""
        return self._request_json("GET", f"{self.base_url}/models", self.api_key, "hetzner")

    def chat_completions_create(
        self,
        *,
        model: str,
        messages: Sequence[Message],
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], Iterator[Dict[str, Any]]]:
        """Create a Chat Completion; return JSON or an iterator of SSE JSON chunks."""
        payload = {"model": model, "messages": list(messages), "stream": stream, **kwargs}
        providers = [Provider("hetzner", self.api_key or "", self.base_url, model)]
        if self.fallback:
            providers.extend(self.fallbacks)
        last_error: Optional[Exception] = None
        for provider in providers:
            if not provider.api_key:
                last_error = HetznerLLMError("No API key configured", provider=provider.name)
                continue
            try:
                request_payload = dict(payload)
                if provider.model and provider.name != "hetzner":
                    request_payload["model"] = provider.model
                if stream:
                    return self._stream_request(provider, request_payload)
                return self._json_request(provider, request_payload)
            except HetznerLLMError as exc:
                last_error = exc
                if provider.name == "hetzner" and not self._should_fallback(exc):
                    raise
        raise last_error or HetznerLLMError("No provider succeeded")

    def _should_fallback(self, error: HetznerLLMError) -> bool:
        return self.fallback_on_all_errors or error.status_code in self.RETRYABLE_STATUS or error.status_code is None

    def _headers(self, api_key: str, stream: bool = False) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"}
        return headers

    def _json_request(self, provider: Provider, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_json("POST", f"{provider.base_url}/chat/completions", provider.api_key, provider.name, payload)

    def _request_json(self, method: str, url: str, api_key: str, provider: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=body, headers=self._headers(api_key), method=method)
        return self._perform_json(request, provider)

    def _perform_json(self, request: Request, provider: str) -> Dict[str, Any]:
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    detail: Any = json.loads(raw)
                except json.JSONDecodeError:
                    detail = raw
                if exc.code in self.RETRYABLE_STATUS and attempt < self.retries:
                    time.sleep(min(2 ** attempt, 8)); continue
                raise HetznerLLMError(f"{provider} HTTP {exc.code}: {detail}", status_code=exc.code, provider=provider, response_body=detail) from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 8)); continue
                raise HetznerLLMError(f"{provider} network error: {exc}", provider=provider) from exc
            except (ValueError, json.JSONDecodeError) as exc:
                raise HetznerLLMError(f"{provider} returned invalid JSON: {exc}", provider=provider) from exc
        raise AssertionError("unreachable")

    def _stream_request(self, provider: Provider, payload: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(f"{provider.base_url}/chat/completions", data=body,
                          headers=self._headers(provider.api_key, stream=True), method="POST")
        try:
            response = urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try: detail: Any = json.loads(raw)
            except json.JSONDecodeError: detail = raw
            raise HetznerLLMError(f"{provider.name} HTTP {exc.code}: {detail}", status_code=exc.code, provider=provider.name, response_body=detail) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise HetznerLLMError(f"{provider.name} network error: {exc}", provider=provider.name) from exc

        def chunks() -> Iterator[Dict[str, Any]]:
            try:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise HetznerLLMError(f"{provider.name} sent invalid SSE JSON: {data}", provider=provider.name) from exc
            finally:
                response.close()
        return chunks()
