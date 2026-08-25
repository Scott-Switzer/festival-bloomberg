"""Venue capacity extraction from Wikipedia wikitext using mwparserfromhell.

Semantics are conservative:

* ``capacity`` and ``seats`` alone stay ``MAX_PERSONS`` (an upper bound) unless
  the field name or a parenthetical in the raw value names a configuration.
* ``seating_capacity`` maps to ``SEATED`` only via an explicit seated field.
* A phrase such as ``23,500 (concert)`` or ``18,006 (hockey)`` becomes
  configuration evidence with the raw parenthetical preserved verbatim.
* ``2,000-2,500`` (a range) is not invented into a single number; it is
  preserved as raw evidence with ``capacity_value=None``.
* ``<ref>`` templates and other embedded wikitext are removed before parsing.
* No numeric field other than an explicit capacity-like field is ever parsed.

Parsing wikitext is not permission to invent semantics. A bare
``capacity = 20,000`` remains ``MAX_PERSONS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import mwparserfromhell

PARSER_VERSION = "wikipedia_infobox_v2"

# Field names that express a seated configuration by name alone.
_SEATED_FIELDS = frozenset({"seating_capacity", "seated_capacity", "seats"})
# Field names that express a standing/GA configuration by name alone.
_STANDING_FIELDS = frozenset({"standing_capacity", "ga_capacity", "general_admission"})
# Field names that express a concert configuration by name alone.
_CONCERT_FIELDS = frozenset({"concert_capacity", "concerts_capacity"})
# Field names that express a sports configuration by name alone.
_SPORTS_FIELDS = frozenset({"hockey_capacity", "basketball_capacity", "football_capacity"})
# Generic field names that remain MAX_PERSONS unless a parenthetical overrides.
_GENERIC_FIELDS = frozenset({"capacity", "capacity_building", "max_capacity", "capacity_general"})

# Parenthetical configuration keywords -> capacity kind.
# Only used when the source text itself contains the word.
_CONFIG_KEYWORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"concert"), "CONCERT"),
    (re.compile(r"\bstanding\b|\bgeneral admission\b|\bga\b"), "STANDING"),
    (re.compile(r"\bseated\b|seating|seats\b"), "SEATED"),
    (re.compile(r"\bhockey\b|\bbasketball\b|\bfootball\b|\bsports?\b|\bboxing\b"), "SPORTS"),
)

_NUMBER_RE = re.compile(r"(\d[\d,]*\d|\d)")


@dataclass(frozen=True)
class CapacityFieldExtract:
    """One extracted capacity-looking field from a venue infobox."""

    field_name: str
    raw_value: str            # complete original field value, verbatim
    clean_value: str          # refs/templates stripped, whitespace normalised
    capacity_value: float | None  # parsed single number, or None (range/unparseable)
    capacity_kind: str
    configuration_description: str | None
    parse_state: str          # OK | RANGE | UNPARSEABLE | NO_NUMBER
    is_range: bool = False


@dataclass
class InfoboxParseResult:
    """Parsed venue infobox content."""

    wikitext: str
    templates: list[str] = field(default_factory=list)
    fields: list[CapacityFieldExtract] = field(default_factory=list)

    def capacities(self) -> list[CapacityFieldExtract]:
        return [f for f in self.fields if f.capacity_value is not None]


def _clean_text(node) -> str:
    """Remove templates and references from an infobox value, keeping text."""
    if node is None:
        return ""
    stripped = node.strip_code().strip()
    # Normalise internal whitespace.
    return re.sub(r"\s+", " ", stripped)


def _looks_like_venue_infobox(template_name: str) -> bool:
    name = template_name.strip().lower().replace("_", " ")
    if not name.startswith("infobox"):
        return False
    if "venue" in name:
        return True
    if any(kw in name for kw in ("stadium", "arena", "theater", "theatre",
                                 "amphitheatre", "amphitheater", "club", "music")):
        return True
    return False


def _classify(field_name: str, clean_value: str) -> tuple[str, str | None]:
    """Return (capacity_kind, configuration_description).

    Explicit segregated fields (seated/standing/concert/sports) win by name. A
    generic ``capacity`` field is overridden only when the raw value itself
    names a configuration (e.g. ``23,500 (concert)``). Otherwise it stays
    MAX_PERSONS as an upper bound.
    """
    fname_norm = field_name.strip().lower().replace(" ", "_")

    if fname_norm in _SEATED_FIELDS:
        return "SEATED", None
    if fname_norm in _STANDING_FIELDS:
        return "STANDING", None
    if fname_norm in _CONCERT_FIELDS:
        return "CONCERT", None
    if fname_norm in _SPORTS_FIELDS:
        return "SPORTS", None

    # Match configuration keywords against the cleaned value in priority order.
    matched_config: tuple[re.Pattern[str], str] | None = None
    if fname_norm in _GENERIC_FIELDS:
        for pattern, kind in _CONFIG_KEYWORDS:
            if pattern.search(clean_value.lower()):
                matched_config = (pattern, kind)
                break

    if matched_config is not None:
        return matched_config[1], matched_config[0].pattern
    return "MAX_PERSONS", None


def _parse_number(clean_value: str) -> tuple[float | None, str, bool]:
    """Parse a single integer from a cleaned value.

    Returns (value, state, is_range). A range (two numbers separated by a
    dash/en-dash/em-dash or 'to') yields (None, RANGE, True) — no invention.
    A `<br>` separates two distinct figures; it must never concatenate them
    into a larger number (e.g. 18,000 + 21,032 must not become 1,800,021,032).
    """
    # Block separators (<br>, newlines, semicolons, slashes, mid-line pipes)
    # separate two distinct figures; they must never be concatenated.
    # Normalise them to whitespace first, THEN strip commas.
    cleaned = re.sub(r"<br\s*/?>|\n|;|/|\|\|", " ", clean_value, flags=re.IGNORECASE)
    # Range marker (single number-dash-number or 'to' between two numbers).
    if re.search(r"\d\s*[-\u2013\u2014to]\s*\d", clean_value.lower()):
        return None, "RANGE", True
    # A separator was stripped (e.g. <br>) but two comma-grouped figures ended
    # up adjacent with no separator: "18,00021,032" -> two figures, one token.
    if re.search(r",\d\d\d\d", cleaned):
        return None, "UNPARSEABLE", False
    # Now commas can be removed safely and discrete figures counted.
    cleaned = re.sub(r",\s", " ", cleaned)
    cleaned = re.sub(r"(\d),(\d)", r"\1\2", cleaned)  # 18,000 -> 18000
    cleaned = cleaned.replace(" ", "")
    numbers = _NUMBER_RE.findall(cleaned)
    if not numbers:
        return None, "NO_NUMBER", False
    if len(numbers) > 1:
        # Two or more discrete numbers without a range marker -> ambiguous.
        return None, "UNPARSEABLE", False
    try:
        return float(numbers[0]), "OK", False
    except (TypeError, ValueError):
        return None, "UNPARSEABLE", False


_CAPACITY_FIELD_HINT = re.compile(r"capacity|seats?\b", re.IGNORECASE)


def _is_capacity_field(param_name: str) -> bool:
    name = param_name.strip().lower().replace(" ", "_")
    if name in _SEATED_FIELDS or name in _STANDING_FIELDS or \
       name in _CONCERT_FIELDS or name in _SPORTS_FIELDS or name in _GENERIC_FIELDS:
        return True
    return bool(_CAPACITY_FIELD_HINT.search(name))


# Label templates whose positional parameters enumerate multiple
# configuration-specific capacity rows (e.g. {{ubl|Basketball: 19,812|...}}).
_LIST_TEMPLATES = frozenset({"ubl", "unbulleted list", "flatlist", "plainlist"})

# Configuration labels matched inside list items / parentheticals.
_LABEL_KINDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"concert"), "CONCERT"),
    (re.compile(r"standing|general admission|\bga\b"), "STANDING"),
    (re.compile(r"seated|seating|\bseats\b"), "SEATED"),
    (re.compile(r"hockey|basketball|football|baseball|soccer|boxing|mma|wrestling|sports?"), "SPORTS"),
)


_ITEM_SPLIT_RE = re.compile(r"(?:\b\|\b)|(?:\n\*)")


def _label_kind_from_text(text: str) -> str | None:
    low = text.lower()
    for pattern, kind in _LABEL_KINDS:
        if pattern.search(low):
            return kind
    return None


def _expand_list_value(raw_value: str) -> list[tuple[str, str]]:
    """Return (label, numeric-text) pairs from a list-valued capacity field.

    Handles ``{{ubl|Basketball: 19,812<ref/>|Ice hockey: 18,006}}`` and plain
    ``Label: number`` sequences. References and nested templates are stripped.
    A top-level ``{{ubl|...}}`` / ``{{flatlist|...}}`` contributes each of its
    positional rows; remaining raw text is split on top-level pipes.
    Conservative: an unlabeled plain number stays a single generic row.
    """
    code = mwparserfromhell.parse(raw_value)

    # Collect row text: positional params of list templates + stray text.
    row_texts: list[str] = []
    list_templates = [t for t in code.filter_templates() if str(t.name).strip().lower() in _LIST_TEMPLATES]
    if list_templates:
        for t in list_templates:
            for param in t.params:
                pname = str(param.name).strip()
                if pname.isnumeric():
                    row_texts.append(str(param.value).strip())
    # Also add leftover raw text outside the list template (top-level pipes).
    for t in list_templates:
        raw_value = raw_value.replace(str(t), "").strip()
    if raw_value and _NUMBER_RE.search(raw_value) and ":" in raw_value:
        row_texts.append(raw_value)
    if not row_texts:
        # Not wrapped in a list template: split on top-level pipes / newlines.
        split_text = raw_value.replace("||", "\n").replace("|", "\n")
        for part in split_text.split("\n"):
            part = part.strip()
            if part:
                row_texts.append(part)

    out: list[tuple[str, str]] = []
    for row in row_texts:
        row = row.strip()
        if not row:
            continue
        if ":" in row and _NUMBER_RE.search(row):
            label, _, rest = row.partition(":")
            label = label.strip()
            rest = re.sub(r"<ref[^>]*>.*?</ref>", "", rest, flags=re.IGNORECASE | re.DOTALL)
            clean_row = re.sub(r"<ref[^>]*/?>", "", rest, flags=re.IGNORECASE)
            clean_row = _clean_text(mwparserfromhell.parse(clean_row))
            numbers = _NUMBER_RE.findall(clean_row.replace(",", ""))
            if numbers and len(numbers) == 1:
                out.append((label, numbers[0]))
        elif _NUMBER_RE.search(row):
            # A bare number row (no label) -> single generic row.
            numbers = _NUMBER_RE.findall(row.replace(",", ""))
            if len(numbers) == 1:
                out.append(("capacity", numbers[0]))
    return out


def _parse_venue_infobox_fields(code, result: InfoboxParseResult) -> None:
    for template in code.filter_templates():
        tname = str(template.name).strip()
        if not _looks_like_venue_infobox(tname):
            continue
        result.templates.append(tname)
        for param in template.params:
            pname = str(param.name).strip().lower().replace(" ", "_")
            if not _is_capacity_field(pname):
                continue
            raw = str(param.value).strip()
            clean = _clean_text(param.value)

            # List-valued capacity ({{ubl|...}}) -> multiple config rows.
            list_pairs = _expand_list_value(raw)
            if len(list_pairs) > 1:
                for label, num in list_pairs:
                    value = float(num)
                    kind = _label_kind_from_text(label) or "MAX_PERSONS"
                    result.fields.append(
                        CapacityFieldExtract(
                            field_name=pname,
                            raw_value=f"{raw}",
                            clean_value=f"{label}: {num}",
                            capacity_value=value,
                            capacity_kind=kind,
                            configuration_description=label or None,
                            parse_state="OK",
                            is_range=False,
                        )
                    )
                continue

            value, state, is_range = _parse_number(clean)
            if value is None and state == "UNPARSEABLE":
                # Single labeled pair "Concert: 22,000"
                pair = _expand_list_value(raw)
                if len(pair) == 1:
                    label, num = pair[0]
                    result.fields.append(
                        CapacityFieldExtract(
                            field_name=pname, raw_value=raw,
                            clean_value=f"{label}: {num}",
                            capacity_value=float(num),
                            capacity_kind=_label_kind_from_text(label) or "MAX_PERSONS",
                            configuration_description=label,
                            parse_state="OK", is_range=False,
                        )
                    )
                continue
            kind, config = _classify(pname, clean)
            result.fields.append(
                CapacityFieldExtract(
                    field_name=pname,
                    raw_value=raw,
                    clean_value=clean,
                    capacity_value=value,
                    capacity_kind=kind,
                    configuration_description=config,
                    parse_state=state,
                    is_range=is_range,
                )
            )


def parse_venue_infobox(wikitext: str) -> InfoboxParseResult:
    """Parse venue infobox capacity fields from raw page wikitext."""
    result = InfoboxParseResult(wikitext=wikitext)
    code = mwparserfromhell.parse(wikitext)
    _parse_venue_infobox_fields(code, result)
    return result


def extracts_to_records(
    result: InfoboxParseResult,
    *,
    page_title: str,
    source_url: str,
    wikidata_qid: str | None,
    retrieved_at: str,
) -> list[dict[str, Any]]:
    """Convert parsed fields into provider-style records for CapacityClaim.

    Preserves raw_value and parser_version alongside numeric claims.
    """
    records: list[dict[str, Any]] = []
    for field in result.fields:
        if field.capacity_value is None:
            # Range/unparseable raw evidence is preserved in config description
            # but yields no numeric claim.
            continue
        records.append(
            {
                "capacity_value": field.capacity_value,
                "capacity_kind": field.capacity_kind,
                "source_field": field.configuration_description or field.field_name,
                "raw_value": field.raw_value,
                "parser_version": PARSER_VERSION,
                "source_url": source_url,
                "wikidata_qid": wikidata_qid,
                "page_title": page_title,
                "retrieved_at": retrieved_at,
                "configuration_description": (
                    f"field={field.field_name}"
                    + (f"; parenthetical={field.raw_value}" if field.configuration_description else "")
                ),
            }
        )
    return records
