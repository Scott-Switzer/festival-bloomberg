"""Provider-neutral model router + OpenAI-compatible NVIDIA NIM client.

The product must never be bound to a single vendor model id. ``ModelRouter``
maps a semantic TASK to a model, resolving against the live catalog when one
is supplied and falling back to sensible defaults otherwise. The defaults are
starting points only — the operational acceptance run queries the catalog and
records which models were actually available.

``NimClient`` is fail-closed: without an API key it reports ``NOT_CONFIGURED``
and makes ZERO network calls. It exposes:

- ``list_models()``     — the current catalog (validates auth cheaply)
- ``chat()``            — OpenAI-compatible chat completions
- ``embed()``           — OpenAI-compatible embeddings (retrieval)

The client is a tool FOR the deterministic verifier and grounded ASK layers.
It never persists facts by itself: any claim it returns must still pass
deterministic admissibility downstream.
"""

from __future__ import annotations

import json
from typing import Any

from ..localenv import load_local_env

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

#: Task -> default model id (overridable, resolved against the catalog).
#: These are FALLBACKS, not a binding contract; validate via list_models().
DEFAULT_TASKS: dict[str, str] = {
    "FAST_EXTRACT": "meta/llama-3.3-70b-instruct",
    "DEEP_REASON": "deepseek-ai/deepseek-r1",
    "CODE_REASON": "qwen/qwen2.5-coder-32b-instruct",
    "EMBED": "nvidia/nv-embedqa-e5-v5",
    "RERANK": "nvidia/llama-3.2-nv-rerankqa-1b-v2",
}

#: Catalog substring hints used to pick a real model per task.
TASK_HINTS: dict[str, tuple[str, ...]] = {
    "FAST_EXTRACT": ("llama-3.3-70b", "llama-3.1-70b"),
    "DEEP_REASON": ("deepseek-r1", "qwq", "deepseek-v4"),
    "CODE_REASON": ("qwen2.5-coder", "deepseek-coder"),
    "EMBED": ("embedqa", "embed"),
    "RERANK": ("rerankqa", "rerank"),
}


class ModelRouter:
    """Resolve semantic tasks to model ids, catalog-aware."""

    def __init__(self, catalog: list[str] | None = None, tasks: dict[str, str] | None = None) -> None:
        self.catalog = list(catalog or [])
        self.tasks = dict(tasks or DEFAULT_TASKS)

    def route(self, task: str) -> str:
        # 1. Explicit override wins.
        if task in self.tasks:
            return self.tasks[task]
        # 2. Match against the live catalog by hint.
        for hint in TASK_HINTS.get(task, ()):
            for model in self.catalog:
                if hint in model:
                    return model
        # 3. Fallback default.
        return DEFAULT_TASKS.get(task, DEFAULT_TASKS["FAST_EXTRACT"])

    def resolve(self, task: str, catalog: list[str] | None = None) -> str:
        if catalog:
            self.catalog = list(catalog)
        return self.route(task)


class NimClient:
    """OpenAI-compatible chat/embed client (NVIDIA NIM or any compatible host)."""

    name = "nvidia"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        transport: Any = None,
        router: ModelRouter | None = None,
    ) -> None:
        load_local_env()
        import os
        self.api_key = api_key or os.environ.get("NVIDIA_API_KEY")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.transport = transport
        self.router = router or ModelRouter()
        self._models: list[str] | None = None

    # -- configuration --------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _transport(self) -> Any:
        if self.transport is not None:
            return self.transport
        from ..acquisition.transport import UrllibTransport
        return UrllibTransport()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -- API ------------------------------------------------------------------
    def list_models(self) -> dict[str, Any]:
        """Return the current model catalog (validates auth cheaply)."""
        if not self.is_configured:
            return {"status": "NOT_CONFIGURED", "models": []}
        resp = self._transport().request(
            "GET", f"{self.base_url}/models", headers=self._headers(), timeout_seconds=30.0
        )
        if resp.status != 200:
            return {"status": f"HTTP_{resp.status}", "models": []}
        try:
            payload = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"status": "SCHEMA_INVALID", "models": []}
        models = [m.get("id") for m in payload.get("data", []) if m.get("id")]
        self._models = models
        return {"status": "OK", "models": models}

    def chat(self, *, messages: list[dict[str, str]], task: str = "FAST_EXTRACT",
             max_tokens: int = 1024, temperature: float = 0.0) -> dict[str, Any]:
        """OpenAI-compatible chat completion. Fail-closed + malformed-safe."""
        if not self.is_configured:
            return {"ok": False, "status": "NOT_CONFIGURED", "content": None}
        model = self.router.route(task)
        body = json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        try:
            resp = self._transport().request(
                "POST", f"{self.base_url}/chat/completions",
                headers=self._headers(), body=body.encode("utf-8"),
                timeout_seconds=120.0,
            )
        except Exception as exc:  # noqa: BLE001 — network errors degrade gracefully
            return {"ok": False, "status": "NETWORK_ERROR", "content": None,
                    "detail": f"{type(exc).__name__}"}
        if resp.status != 200:
            return {"ok": False, "status": f"HTTP_{resp.status}", "content": None}
        try:
            payload = json.loads(resp.body.decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, UnicodeDecodeError):
            return {"ok": False, "status": "MALFORMED_RESPONSE", "content": None}
        return {"ok": True, "status": "OK", "content": content, "model": model}

    def embed(self, inputs: list[str], task: str = "EMBED") -> dict[str, Any]:
        """OpenAI-compatible embeddings (retrieval). Fail-closed."""
        if not self.is_configured:
            return {"ok": False, "status": "NOT_CONFIGURED", "vectors": []}
        model = self.router.route(task)
        body = json.dumps({"model": model, "input": inputs,
                           "input_type": "query", "encoding_format": "float"})
        try:
            resp = self._transport().request(
                "POST", f"{self.base_url}/embeddings",
                headers=self._headers(), body=body.encode("utf-8"),
                timeout_seconds=60.0,
            )
        except Exception:  # noqa: BLE001
            return {"ok": False, "status": "NETWORK_ERROR", "vectors": []}
        if resp.status != 200:
            return {"ok": False, "status": f"HTTP_{resp.status}", "vectors": []}
        try:
            payload = json.loads(resp.body.decode("utf-8"))
            vectors = [d["embedding"] for d in payload["data"]]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return {"ok": False, "status": "MALFORMED_RESPONSE", "vectors": []}
        return {"ok": True, "status": "OK", "vectors": vectors, "model": model}
