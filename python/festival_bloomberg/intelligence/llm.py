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

#: Catalog substring hints used to pick a real model per task. Hints are
#: ordered most-preferred-first; a known-good, account-deployable model id is
#: listed BEFORE looser substrings so a broken/unavailable same-prefix model
#: (e.g. ``nvidia/llama-3.2-nv-embedqa-1b-v1``) never shadows a working one
#: (``nvidia/nv-embedqa-e5-v5``).
TASK_HINTS: dict[str, tuple[str, ...]] = {
    "FAST_EXTRACT": ("llama-3.3-70b", "llama-3.1-70b"),
    "DEEP_REASON": ("deepseek-v4", "deepseek-r1", "qwq"),
    "CODE_REASON": ("deepseek-coder", "qwen2.5-coder"),
    "EMBED": ("nv-embedqa-e5-v5", "nv-embed-v1", "embed"),
    "RERANK": ("rerankqa", "rerank"),
}


class ModelRouter:
    """Resolve semantic tasks to model ids, catalog-aware.

    Three distinct layers, in strict routing order:

    1. **explicit overrides** — user-configured ids, honored only when they
       are valid in the known catalog;
    2. **catalog candidates** — hint-matched against the LIVE catalog;
    3. **fallback defaults** — ``DEFAULT_TASKS``, used only when the id is
       present in the known catalog.

    A catalog of ``None`` means "not loaded yet"; overrides/fallbacks are
    trusted as starting points in that state. Once a catalog IS set, NO model
    id absent from it is ever issued — the router returns ``UNAVAILABLE``
    (fail closed) instead of inventing one.
    """

    UNAVAILABLE = "UNAVAILABLE"

    def __init__(self, catalog: list[str] | None = None, tasks: dict[str, str] | None = None) -> None:
        self.catalog: list[str] | None = list(catalog) if catalog is not None else None
        # Explicit overrides ONLY. The defaults live in DEFAULT_TASKS (fallback
        # layer); they must never short-circuit catalog matching.
        self.overrides: dict[str, str] = dict(tasks or {})

    def set_catalog(self, catalog: list[str] | None) -> None:
        self.catalog = list(catalog) if catalog is not None else None

    def route(self, task: str) -> str:
        catalog = self.catalog
        known = catalog is not None
        # 1. Explicit override wins — only if valid in the known catalog.
        override = self.overrides.get(task)
        if override:
            if not known or override in catalog:
                return override
        # 2. Match against the live catalog by hint.
        if known:
            for hint in TASK_HINTS.get(task, ()):
                for model in catalog:
                    if hint in model:
                        return model
        # 3. Fallback default — only if it exists in the known catalog.
        fallback = DEFAULT_TASKS.get(task)
        if fallback and (not known or fallback in catalog):
            return fallback
        # 4. Fail closed.
        return self.UNAVAILABLE

    def resolve(self, task: str, catalog: list[str] | None = None) -> str:
        if catalog is not None:
            self.set_catalog(catalog)
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
        self.router.set_catalog(models)
        return {"status": "OK", "models": models}

    def _ensure_catalog(self) -> dict[str, Any]:
        """Load the model catalog once so chat/embed only use catalog-valid ids."""
        if self._models is None:
            result = self.list_models()
            if result["status"] != "OK":
                return result
        return {"status": "OK", "models": self._models or []}

    def chat(self, *, messages: list[dict[str, str]], task: str = "FAST_EXTRACT",
             max_tokens: int = 1024, temperature: float = 0.0) -> dict[str, Any]:
        """OpenAI-compatible chat completion. Fail-closed + malformed-safe."""
        if not self.is_configured:
            return {"ok": False, "status": "NOT_CONFIGURED", "content": None}
        catalog_result = self._ensure_catalog()
        if catalog_result["status"] != "OK":
            return {"ok": False, "status": catalog_result["status"], "content": None}
        model = self.router.route(task)
        if model == ModelRouter.UNAVAILABLE:
            return {"ok": False, "status": "MODEL_UNAVAILABLE", "content": None,
                    "task": task}
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
        catalog_result = self._ensure_catalog()
        if catalog_result["status"] != "OK":
            return {"ok": False, "status": catalog_result["status"], "vectors": []}
        model = self.router.route(task)
        if model == ModelRouter.UNAVAILABLE:
            return {"ok": False, "status": "MODEL_UNAVAILABLE", "vectors": [],
                    "task": task}
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
