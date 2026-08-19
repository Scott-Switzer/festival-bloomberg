"""Non-optimizing scenario board: validation + summaries.

The board lets a buyer place shortlist artists into hypothetical day × stage ×
slot positions. It VALIDATES (double-booking, stage conflicts, routing
conflicts) and SUMMARIZES (billing distribution, coverage). It never optimizes
and never recommends a lineup. Warnings are warnings — not fabricated hard
constraints.
"""

from __future__ import annotations

from typing import Any

from .repository import list_shortlists, save_scenario


def _slots_for(conn, project_key: str, slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shortlist = {s["artist_key"] or s["artist_name"]: s for s in list_shortlists(conn, project_key)}
    out = []
    for slot in slots:
        artist_key = slot.get("artist_key")
        artist_name = slot.get("artist_name") or ""
        sl = shortlist.get(artist_key) or shortlist.get(artist_name)
        status = (sl or {}).get("status", "UNKNOWN")
        out.append({
            "artist_key": artist_key,
            "artist_name": artist_name,
            "day": slot.get("day"),
            "stage": slot.get("stage"),
            "slot_label": slot.get("slot_label"),
            "billing_tier": slot.get("billing_tier"),
            "shortlist_status": status,
        })
    return out


def validate_scenario(
    conn, *, project_key: str, slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministic conflict/coverage warnings for a slot arrangement."""
    warnings: list[dict[str, Any]] = []
    expanded = _slots_for(conn, project_key, slots)

    # 1. Artist cannot occupy two slots simultaneously (same day, overlapping
    #    time). Without times, same day + same artist on different stages is a
    #    possible conflict, not a confirmed one.
    by_day_artist: dict[tuple[int | None, str], list[dict[str, Any]]] = {}
    for s in expanded:
        key = (s["day"], s["artist_name"])
        by_day_artist.setdefault(key, []).append(s)
    for (day, artist), arr in by_day_artist.items():
        if len(arr) > 1:
            stages = ", ".join(sorted({s["stage"] or "?" for s in arr}))
            warnings.append({
                "severity": "CONFIRMED" if any(s.get("slot_label") for s in arr) else "POSSIBLE",
                "type": "ARTIST_DOUBLE_BOOKED",
                "detail": f"{artist} placed in {len(arr)} slots on day {day} "
                          f"({stages}) — must occupy exactly one slot",
            })

    # 2. Stage schedule conflict: two artists in the same day + stage + slot.
    by_slot: dict[tuple[int | None, str, str | None], list[dict[str, Any]]] = {}
    for s in expanded:
        key = (s["day"], s["stage"], s["slot_label"])
        by_slot.setdefault(key, []).append(s)
    for key, arr in by_slot.items():
        if len(arr) > 1:
            warnings.append({
                "severity": "CONFIRMED",
                "type": "STAGE_SLOT_CONFLICT",
                "detail": f"day {key[0]} stage {key[1]} slot {key[2]}: "
                          f"{', '.join(s['artist_name'] for s in arr)} share one slot",
            })

    # 3. Routing conflict: an artist booked on the same day in two markets is a
    #    physical impossibility, but the planning board has no cross-market
    #    routing data, so only same-artist-same-day (across stages) counts as
    #    CONFIRMED; market-level routing stays UNKNOWN.
    for w in warnings:
        pass
    # (cross-market routing intentionally not asserted: UNKNOWN, not guessed)

    # 4. Shortlist status warnings (PASSED artists should not be placed).
    for s in expanded:
        if s["shortlist_status"] == "PASSED":
            warnings.append({
                "severity": "INFO",
                "type": "SHORTLIST_PASSED",
                "detail": f"{s['artist_name']} is marked PASSED on the shortlist "
                          f"but is placed in the scenario",
            })

    # 5. Data coverage warnings (no fabricated economics).
    covered = sum(1 for s in expanded if s["shortlist_status"] != "UNKNOWN")
    if expanded and covered < len(expanded):
        warnings.append({
            "severity": "INFO",
            "type": "COVERAGE_GAP",
            "detail": f"{len(expanded) - covered} of {len(expanded)} placed artists "
                      f"are not on the project shortlist (no evidence snapshot)",
        })
    return warnings


def summarize_scenario(
    conn, *, project_key: str, slots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Non-opinionated scenario summary (counts/distributions only)."""
    expanded = _slots_for(conn, project_key, slots)
    warnings = validate_scenario(conn, project_key=project_key, slots=slots)
    artists = {s["artist_name"] for s in expanded}
    billing: dict[str, int] = {}
    for s in expanded:
        t = s["billing_tier"] or "UNKNOWN"
        billing[t] = billing.get(t, 0) + 1
    stages_used = {s["stage"] for s in expanded if s["stage"]}
    days_used = {s["day"] for s in expanded if s["day"] is not None}
    shortlisted = sum(1 for s in expanded if s["shortlist_status"] not in ("UNKNOWN",))
    return {
        "slot_count": len(expanded),
        "artist_count": len(artists),
        "days_used": sorted(d for d in days_used if d is not None),
        "stages_used": sorted(stages_used),
        "billing_distribution": billing,
        "shortlist_coverage": round(shortlisted / len(expanded), 3) if expanded else 0.0,
        "warnings": warnings,
        "conflict_count": len([w for w in warnings if w["severity"] in ("CONFIRMED", "POSSIBLE")]),
    }


def persist_scenario(
    conn, *, project_key: str, name: str, slots: list[dict[str, Any]],
    notes: str | None = None,
) -> dict[str, Any]:
    """Validate + summarize + persist one scenario board (idempotent)."""
    warnings = validate_scenario(conn, project_key=project_key, slots=slots)
    summaries = summarize_scenario(conn, project_key=project_key, slots=slots)
    saved = save_scenario(
        conn, project_key=project_key, name=name, slots=slots,
        warnings=warnings, summaries=summaries, notes=notes,
    )
    return {**saved, "warnings": warnings, "summaries": summaries}
