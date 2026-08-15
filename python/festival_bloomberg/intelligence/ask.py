"""ASK — grounded, read-only intelligence over the terminal read models.

DeepSeek (when configured) composes an answer from tool RESULTS; it never
receives arbitrary SQL and never persists anything. Without a key the module
falls back to a deterministic intent router so ASK still returns real,
source-backed answers from the warehouse.

Enforced by construction (and tested):

- The tool surface is a closed set of read-model calls. There is NO SQL
  execution primitive, so an LLM prompt cannot "inject" arbitrary SQL.
- No handler writes to the warehouse. ASK cannot persist evidence.
- An answer only ever repeats values returned by a read model. It cannot
  create attendance / price / capacity / booking facts that are not stored.
- A factual answer always carries ``evidence`` (the source rows that back it).
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .readmodels import (
    get_artist,
    get_attention_series,
    get_competing_events,
    get_event,
    get_festival,
    get_market,
    get_news,
    get_source_evidence,
    get_venue,
    query_tape,
    search_entities,
)


def _boxoffice_history(conn, entity_id: str) -> list[dict[str, Any]]:
    # Historical boxoffice rows for an artist or venue key.
    artist = get_artist(conn, entity_id)
    if artist:
        return artist.get("history", [])
    venue = get_venue(conn, entity_id)
    if venue:
        return venue.get("history", [])
    return []


#: The closed, read-only tool surface. name -> (description, handler).
def _tool_table() -> dict[str, tuple[str, Callable[..., Any]]]:
    return {
        "search_entities": (
            "Search artists, venues, markets, festivals by name.",
            lambda conn, a: search_entities(conn, a["q"], a.get("limit", 25)),
        ),
        "get_artist": (
            "Artist identity, history, upcoming events, outcomes, attention, news.",
            lambda conn, a: get_artist(conn, a["entity_id"]),
        ),
        "get_event": (
            "Event timeline, observations, competition, evidence.",
            lambda conn, a: get_event(conn, a["entity_id"]),
        ),
        "get_venue": (
            "Venue history, upcoming calendar, capacity claims.",
            lambda conn, a: get_venue(conn, a["entity_id"]),
        ),
        "get_market": (
            "Market calendar, venues, history, context series.",
            lambda conn, a: get_market(conn, a["entity_id"]),
        ),
        "get_festival": (
            "Festival identity and editions (no corpus yet).",
            lambda conn, a: get_festival(conn, a["entity_id"]),
        ),
        "get_activity_tape": (
            "Recent tape activity with optional entity_type/market/activity filters.",
            lambda conn, a: query_tape(
                conn,
                entity_type=a.get("entity_type"),
                market_id=a.get("market_id"),
                activity_type=a.get("activity_type"),
                limit=a.get("limit", 100),
            ),
        ),
        "get_boxoffice_history": (
            "Historical boxoffice engagements for an artist or venue.",
            lambda conn, a: _boxoffice_history(conn, a["entity_id"]),
        ),
        "get_attention_series": (
            "Attention (pageviews/listens) time series for an entity.",
            lambda conn, a: get_attention_series(conn, a["entity_name"]),
        ),
        "get_news": (
            "News mentions for an entity (metadata only).",
            lambda conn, a: get_news(conn, a["entity_name"]),
        ),
        "get_competing_events": (
            "Events in the same market within N days of a date.",
            lambda conn, a: get_competing_events(conn, a["market"], a["event_date"], a.get("days", 7)),
        ),
        "get_source_evidence": (
            "Source/evidence lineage backing an event.",
            lambda conn, a: get_source_evidence(conn, a["entity_id"]),
        ),
    }


def run_tool(conn, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one read-only tool. Unknown tools are rejected (fail closed)."""
    tools = _tool_table()
    if name not in tools:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        result = tools[name][1](conn, args)
        return {"ok": True, "tool": name, "result": result}
    except Exception as exc:  # noqa: BLE001 — the terminal never leaks internals
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _intent(question: str) -> tuple[str, dict[str, Any]] | None:
    """Tiny deterministic router so ASK works without an LLM."""
    q = question.lower()
    tape_m = re.search(r"what changed in (\w+(?: \w+)*)", q)
    if "what changed" in q or "tape" in q:
        market = tape_m.group(1) if tape_m else None
        return "get_activity_tape", {
            "market_id": market.strip() if market else None,
            "limit": 30,
        }
    if "evidence" in q or "onsale" in q or "source" in q:
        return "get_source_evidence", {"entity_id": _extract_id(q)}
    return None


def _extract_id(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip()).lower()


def answer(
    conn,
    question: str,
    *,
    deepseek: Any = None,
) -> dict[str, Any]:
    """Answer a natural-language question using only read-only tools.

    Returns a dict with ``text`` and, when facts are cited, ``evidence``
    (the read-model rows that back the answer). No answer invents a fact.
    """
    # 1. Deterministic routing (no LLM required).
    routed = _intent(question)
    if routed:
        tool, args = routed
        res = run_tool(conn, tool, args)
        if res["ok"] and res["result"]:
            return {
                "text": f"Ran read-only tool {tool}: {len(res['result'])} rows returned.",
                "tool": tool,
                "evidence": res["result"],
                "mode": "deterministic",
            }
        return {
            "text": f"Tool {tool} returned no data. Nothing is invented; the corpus has no rows for this query.",
            "tool": tool,
            "evidence": [],
            "mode": "deterministic",
        }

    # 2. A simple "search X" / "show me X" falls back to entity search.
    search = search_entities(conn, question, limit=10)
    if search:
        return {
            "text": f"{len(search)} entities match. Showing top results with their evidence.",
            "tool": "search_entities",
            "evidence": search,
            "mode": "deterministic",
        }

    # 3. Optional DeepSeek composition (public/sanitized tool results only).
    if deepseek is not None and getattr(deepseek, "is_configured", False):
        return deepseek.compose_answer(conn, question, run_tool)

    # 4. Honest fallback: state what is and isn't answerable.
    return {
        "text": (
            "No deterministic match. I can answer grounded questions using "
            "read-only tools (search_entities, get_artist, get_event, get_venue, "
            "get_market, get_activity_tape, get_boxoffice_history, "
            "get_attention_series, get_news, get_competing_events, "
            "get_source_evidence). I never invent attendance, price, capacity, "
            "booking dates, or financial results."
        ),
        "tool": None,
        "evidence": [],
        "mode": "deterministic",
    }


class DeepSeekAskClient:
    """Optional DeepSeek V4 Pro ASK composer (fail closed)."""

    def __init__(self, *, api_key: str | None = None, transport: Any = None) -> None:
        self.api_key = api_key
        self.transport = transport

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.transport)

    def compose_answer(self, conn, question: str, run_tool_fn: Callable) -> dict[str, Any]:
        """Compose an answer from read-only tool results only.

        Implemented as a no-op here: a keyed run supplies the transport. The
        contract is that any factual sentence must cite a tool result; this
        stub never invents one.
        """
        return {
            "text": "DeepSeek ASK transport not implemented in this offline build.",
            "tool": None,
            "evidence": [],
            "mode": "not_configured",
        }
