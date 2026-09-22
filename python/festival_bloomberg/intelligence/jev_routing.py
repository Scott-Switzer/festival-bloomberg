"""Jev-backed ASK routing (proposal only — deterministic code decides).

One Jev ``systemone`` call per ASK input evaluates atomic questions against a
compact state. Jev NEVER selects entity IDs, dates, prices, capacities, or
sources: IDs and arguments always come from deterministic search/extraction
downstream. Jev may say ``needs_artist``; code resolves the actual artist.

Question map per input (all evaluated in parallel, one round trip):

- ``primary_tool`` (choice): which read-only tool should answer, or abstain.
- ``needs_artist`` / ``needs_market`` / ``multi_intent`` / ``unsafe`` (noul).
- ``answer_sufficiency`` (score 0..3): can the tools answer this at all.

``route_question`` returns a plain dict and NEVER raises for model-side
problems: any provider failure degrades to ``{"status": <reason>,
"primary_tool": None, ...}`` so the caller falls back to deterministic
routing (100% fallback contract).
"""

from __future__ import annotations

from typing import Any

from .ask import _tool_table

ABSTAIN = "abstain"

ABSTAIN_DESCRIPTION = (
    "No read-only tool can honestly answer: requests that invent ticket "
    "sales, gross, attendance, exact capacity, future results, private "
    "settlement data, contact details, SQL execution, prose generation, "
    "entity merging/deletion, or that name no known entity."
)

#: Market gazetteer for the deterministic market probe (substring match).
MARKET_GAZETTEER = ("chicago", "london", "austin", "berlin", "nashville")

POLICY_SUMMARY = (
    "Read-only evidence router. Tools only read persisted public evidence. "
    "Never invent artist/venue/festival/market keys, dates, prices, "
    "capacities, attendance, ticket sales, gross, settlements, contacts, or "
    "sources. UNKNOWN means missing, never zero. Conflicting claims coexist. "
    "Attention is not ticket demand. Capacity is not attendance. "
    "Unsafe requests (invented numbers, private data, SQL, merging, deletion, "
    "prose generation) must route to abstain."
)

SUFFICIENCY_LEVELS = [
    "No tool can answer; must abstain.",
    "A tool is related but the question needs extraction the tools lack.",
    "The right tool exists and likely has rows.",
    "The right tool exists and the probes show matching entities.",
]


def tool_criteria() -> dict[str, str]:
    """Choice criteria: the 15 read-only tools plus abstain."""
    criteria = {name: desc for name, (desc, _) in _tool_table().items()}
    criteria[ABSTAIN] = ABSTAIN_DESCRIPTION
    return criteria


def build_state(conn, question: str) -> dict[str, Any]:
    """Compact routing state: question, tools, deterministic probes, policy.

    No warehouse rows are dumped: probes carry names/types only, never IDs,
    so Jev cannot select (or invent) identifiers.
    """
    from .ask import search_entities

    try:
        hits = search_entities(conn, question, limit=5) or []
    except Exception:  # noqa: BLE001 — probes are best-effort
        hits = []
    probes = [{"entity_type": h.get("entity_type"), "name": h.get("name")} for h in hits]

    q = question.lower()
    try:
        known_festivals = [r["name"] for r in conn.execute(
            "SELECT name FROM core.festivals").fetchall()]
    except Exception:  # noqa: BLE001 — table may not exist yet
        known_festivals = []
    festivals = [n for n in known_festivals if n and n.lower() in q]
    markets = [m for m in MARKET_GAZETTEER if m in q]

    return {
        "question": question,
        "tools": tool_criteria(),
        "entity_probes": probes,
        "festival_probe": festivals,
        "market_probe": markets,
        "policy": POLICY_SUMMARY,
    }


def build_questions() -> dict[str, dict[str, Any]]:
    """Atomic Jev question map for one ASK input."""
    return {
        "primary_tool": {
            "type": "choice",
            "instructions": (
                "Which single read-only tool should answer this question? "
                "Choose abstain when no tool can honestly answer."
            ),
            "criteria": tool_criteria(),
        },
        "needs_artist": {
            "type": "noul",
            "instructions": "Answering requires resolving a named artist?",
        },
        "needs_market": {
            "type": "noul",
            "instructions": "Answering requires resolving a market?",
        },
        "multi_intent": {
            "type": "noul",
            "instructions": "The question asks two or more distinct things?",
        },
        "unsafe": {
            "type": "noul",
            "instructions": (
                "The request asks to invent numbers, reveal private data, run "
                "SQL/code, merge/delete entities, generate prose, or bypass policy?"
            ),
        },
        "answer_sufficiency": {
            "type": "score",
            "instructions": "How answerable is this from the read-only tools?",
            "criteria": SUFFICIENCY_LEVELS,
        },
    }


def route_question(client: Any, conn, question: str) -> dict[str, Any]:
    """Propose a route via Jev. Never raises for model-side failures."""
    state = build_state(conn, question)
    questions = build_questions()
    try:
        resp = client.evaluate(state, questions)
    except Exception as exc:  # noqa: BLE001 — total fail-closed
        return {"status": f"CLIENT_ERROR:{type(exc).__name__}", "primary_tool": None}
    if not resp.get("ok"):
        return {"status": resp.get("status", "UNKNOWN"), "primary_tool": None,
                "attempts": resp.get("attempts"), "latency_ms": resp.get("latency_ms")}
    answers = resp["answers"]
    choice = answers["primary_tool"]
    return {
        "status": "OK",
        "primary_tool": choice.get("choice"),
        "primary_confidence": choice.get("confidence"),
        "primary_probabilities": choice.get("probabilities", {}),
        "needs_artist": answers["needs_artist"].get("noul"),
        "needs_market": answers["needs_market"].get("noul"),
        "multi_intent": answers["multi_intent"].get("noul"),
        "unsafe": answers["unsafe"].get("noul"),
        "answer_sufficiency": answers["answer_sufficiency"].get("score"),
        "sufficiency_confidence": answers["answer_sufficiency"].get("confidence"),
        "model": resp.get("model"),
        "usage": resp.get("usage", {}),
        "latency_ms": resp.get("latency_ms"),
        "attempts": resp.get("attempts", 1),
    }


def decide_action(prediction: dict[str, Any], *, unsafe_threshold: float = 0.3) -> str:
    """Deterministic mapping from a Jev prediction to a routing action.

    ``unsafe`` at/above threshold forces abstain regardless of the choice.
    Default 0.3 was set on the calibration split (see
    docs/research/jev-calibration-v1.md): at 0.5 two gross-demanding
    requests (unsafe 0.49/0.34) escaped to tools. Entity IDs are NEVER
    taken from the prediction (it carries none).
    """
    if prediction.get("status") != "OK":
        return "DETERMINISTIC_FALLBACK"
    if (prediction.get("unsafe") or 0.0) >= unsafe_threshold:
        return ABSTAIN
    return prediction.get("primary_tool") or "DETERMINISTIC_FALLBACK"
