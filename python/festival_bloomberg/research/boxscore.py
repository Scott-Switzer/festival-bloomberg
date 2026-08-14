"""Public box-office research corpus: engagement model + source parsers.

A BOXOFFICE_ENGAGEMENT is one reported record from a public box-office source.
It may span ONE or MULTIPLE shows; multi-show aggregates are never divided
across nights unless the source itself does. The headcount numerator differs
by source, so every engagement carries an explicit ``headcount_definition``:

* Billboard "Attend/Capacity"    -> REPORTED_ATTENDANCE (paid vs scanned unknown)
* Pollstar "Tickets Sold"        -> PAID_TICKETS (per Pollstar reporting policy)
* Touring Data "(attendance - $)" -> REPORTED_ATTENDANCE (reported rows only)

A value is never relabeled into a stronger category. These sources are
RESEARCH_ONLY / TERMS_REVIEW_REQUIRED; nothing here is commercial-eligible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
import html as _html_module
from html.parser import HTMLParser
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now

# ---------------------------------------------------------------------------
# Reporting sources + headcount semantics
# ---------------------------------------------------------------------------
SOURCE_BILLBOARD = "billboard"
SOURCE_POLLSTAR = "pollstar"
SOURCE_TOURING_DATA = "touring_data"
SOURCE_OPENICPSR = "openicpsr"
SOURCE_OPENMUSE = "openmuse"

HEADCOUNT_PAID_TICKETS = "PAID_TICKETS"
HEADCOUNT_REPORTED_ATTENDANCE = "REPORTED_ATTENDANCE"
HEADCOUNT_UNSPECIFIED = "UNSPECIFIED"
HEADCOUNT_DEFINITIONS = frozenset({
    HEADCOUNT_PAID_TICKETS, HEADCOUNT_REPORTED_ATTENDANCE, HEADCOUNT_UNSPECIFIED,
})

from ..economics.outcome_claims import (  # noqa: E402
    OBSERVED_PUBLIC,
    RIGHTS_RESEARCH_ONLY,
    RIGHTS_TERMS_REVIEW_REQUIRED,
)

# source -> default rights (fail closed; commercial use is never granted here)
SOURCE_RIGHTS: dict[str, tuple[str, str]] = {
    SOURCE_BILLBOARD: (RIGHTS_RESEARCH_ONLY, RIGHTS_RESEARCH_ONLY),
    SOURCE_POLLSTAR: (RIGHTS_RESEARCH_ONLY, RIGHTS_RESEARCH_ONLY),
    SOURCE_TOURING_DATA: (RIGHTS_TERMS_REVIEW_REQUIRED, RIGHTS_TERMS_REVIEW_REQUIRED),
    SOURCE_OPENICPSR: (RIGHTS_TERMS_REVIEW_REQUIRED, RIGHTS_TERMS_REVIEW_REQUIRED),
    SOURCE_OPENMUSE: (RIGHTS_TERMS_REVIEW_REQUIRED, RIGHTS_TERMS_REVIEW_REQUIRED),
}


@dataclass
class BoxofficeEngagement:
    engagement_id: str
    artist: str
    reporting_source: str
    retrieved_at: str
    headcount_definition: str
    rights_status: str
    commercial_use_status: str
    observation_class: str = OBSERVED_PUBLIC
    rank: int | None = None
    venue: str | None = None
    market: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    promoter: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    dates_raw: str | None = None
    number_of_shows: int | None = None
    headcount_total: float | None = None
    capacity_total: float | None = None
    sellable_capacity_per_show: float | None = None
    reported_sellouts: int | None = None
    ticket_gross_total: float | None = None
    currency: str | None = "USD"
    price_min: float | None = None
    price_max: float | None = None
    prices_raw: str | None = None
    capacity_tier: str | None = None
    tour: str | None = None
    headcount_source_label: str | None = None
    sell_through_pct: float | None = None
    source_url: str | None = None
    source_publication_time: str | None = None
    is_multi_show: bool = False
    is_reported: bool = True
    is_estimated: bool = False
    raw_payload_hash: str | None = None
    software_version: str = "public_boxscore_research_corpus_v1"

    def __post_init__(self) -> None:
        if self.headcount_definition not in HEADCOUNT_DEFINITIONS:
            raise ValueError(f"invalid headcount_definition {self.headcount_definition!r}")
        if self.number_of_shows is not None and self.number_of_shows > 1:
            self.is_multi_show = True

    def to_row(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def build(cls, **kwargs: Any) -> "BoxofficeEngagement":
        kwargs.setdefault("retrieved_at", utc_now().isoformat())
        kwargs.setdefault("observation_class", OBSERVED_PUBLIC)
        source = kwargs.get("reporting_source", "unknown")
        rights, commercial = SOURCE_RIGHTS.get(source, (RIGHTS_TERMS_REVIEW_REQUIRED, RIGHTS_TERMS_REVIEW_REQUIRED))
        kwargs.setdefault("rights_status", rights)
        kwargs.setdefault("commercial_use_status", commercial)
        engagement_id = kwargs.pop(
            "engagement_id",
            f"eng_{content_hash_of({
                'source': source,
                'rank': kwargs.get('rank'),
                'artist': kwargs.get('artist'),
                'venue': kwargs.get('venue'),
                'dates': kwargs.get('dates_raw'),
                'gross': kwargs.get('ticket_gross_total'),
                'headcount': kwargs.get('headcount_total'),
            })[:20]}",
        )
        return cls(engagement_id=engagement_id, **kwargs)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
_MONEY = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)")
_INT = re.compile(r"(\d{1,3}(?:,\d{3})*|\d+)")

_BLOCK_BREAK = re.compile(r"<(?:br\s*/?|/p|/div|/li|/tr|/td|/h[1-6])\s*>", re.IGNORECASE)
_TAG_STRIP = re.compile(r"<[^>]+>")


def html_to_text_lines(html: str) -> str:
    """HTML -> text, preserving block-level line breaks (no external deps).

    Pollstar/Touring Data embed records inside ``<p>``/``<br>`` blocks, so a
    naive tag-strip would flatten the record boundaries. Block tags become
    newlines; every other tag becomes a space; whitespace is collapsed per
    line."""
    if not html:
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = _BLOCK_BREAK.sub("\n", text)
    text = _TAG_STRIP.sub(" ", text)
    text = _html_module.unescape(text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("$", "")
    if text in ("", "-", "n/a", "N/A", "null"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(raw: str | None) -> int | None:
    value = _to_float(raw)
    return int(value) if value is not None else None


def _split_city_state(market: str | None) -> tuple[str | None, str | None]:
    if not market:
        return None, None
    parts = [p.strip() for p in market.split(",")]
    city = parts[0] if parts else None
    rest = parts[1] if len(parts) > 1 else None
    return city or None, rest or None


def _parse_iso_date(month_abbr: str, day: int, year: int) -> str | None:
    month = _MONTHS.get(month_abbr.lower()[:3])
    if month is None:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


_SINGLE_DATE = re.compile(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE)


def _dates_from_raw(raw: str | None) -> tuple[str | None, str | None, int | None]:
    """Best-effort start/end + inferred show count from a date string.

    Single dates ("Oct. 26, 2013") -> start == end == that date, shows=1.
    Multi-date ranges -> start/end None (day-level division is not attempted),
    but a year is recovered and show count stays None (caller sets it from an
    explicit shows column when present).
    """
    if not raw:
        return None, None, None
    text = raw.strip()
    dates = list(_SINGLE_DATE.finditer(text))
    year_match = re.search(r"(19|20)\d{2}", text)
    year = int(year_match.group(0)) if year_match else None
    if len(dates) == 1:
        m = dates[0]
        iso = _parse_iso_date(m.group(1), int(m.group(2)), int(m.group(3)))
        return iso, iso, 1
    return None, None, None if len(dates) <= 1 else None


# ---------------------------------------------------------------------------
# Billboard Current Boxscore (HTML table)
# ---------------------------------------------------------------------------
class _TableExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row: list[str] = []
        self.current_cell: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            self.current_row.append(" ".join("".join(self.current_cell).split()))
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag == "table":
            self.in_table = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)


def _extract_tables(html: str) -> list[list[list[str]]]:
    parser = _TableExtractor()
    parser.feed(html or "")
    # split rows into tables by header row presence is fragile; return one
    # flat list of rows for the caller to window over the boxscore table.
    return [parser.rows]


def parse_billboard_boxscore_html(html: str, *, source_url: str | None = None) -> list[BoxofficeEngagement]:
    rows = _extract_tables(html)
    engagements: list[BoxofficeEngagement] = []
    header_index: int | None = None
    flat_rows: list[list[str]] = [r for table in rows for r in table]

    for i, row in enumerate(flat_rows):
        joined = " ".join(row).lower()
        if "artist/event" in joined and "gross" in joined:
            header_index = i
            break
    if header_index is None:
        return engagements

    for row in flat_rows[header_index + 1:]:
        if len(row) < 9:
            continue
        cells = [c.strip() for c in row]
        rank = _to_int(cells[0])
        if rank is None:
            continue
        artist = cells[1]
        venue = cells[2]
        market = cells[3]
        city, state = _split_city_state(market)
        dates_raw = cells[4]
        gross = _to_float(cells[5])
        attend_cap = cells[6].split("/")
        headcount = _to_float(attend_cap[0].strip()) if attend_cap else None
        capacity = _to_float(attend_cap[1].strip()) if len(attend_cap) > 1 else None
        shows_sellouts = cells[7].split("/")
        shows = _to_int(shows_sellouts[0].strip()) if shows_sellouts else None
        sellouts = _to_int(shows_sellouts[1].strip()) if len(shows_sellouts) > 1 else None
        prices_raw = cells[8]
        price_min, price_max = _price_range(cells[8])
        promoter = cells[9] if len(cells) > 9 else None
        start, end, _ = _dates_from_raw(dates_raw)

        engagements.append(BoxofficeEngagement.build(
            reporting_source=SOURCE_BILLBOARD,
            rank=rank,
            artist=artist,
            venue=venue,
            market=market,
            city=city,
            state=state,
            promoter=promoter,
            start_date=start,
            end_date=end,
            dates_raw=dates_raw,
            number_of_shows=shows,
            headcount_total=headcount,
            headcount_definition=HEADCOUNT_REPORTED_ATTENDANCE,
            capacity_total=capacity,
            reported_sellouts=sellouts,
            ticket_gross_total=gross,
            price_min=price_min,
            price_max=price_max,
            prices_raw=prices_raw,
            source_url=source_url,
            is_multi_show=bool(shows and shows > 1),
            is_reported=True,
            is_estimated=False,
        ))
    return engagements


def _price_range(prices_raw: str) -> tuple[float | None, float | None]:
    values = [v for v in (_to_float(m) for m in re.findall(r"[\d,]+\.?\d*", prices_raw)) if v is not None]
    if not values:
        return None, None
    return min(values), max(values)


# ---------------------------------------------------------------------------
# Pollstar Hot Tickets / Top Shows (semi-structured text)
# ---------------------------------------------------------------------------
_POLLSTAR_HEAD = re.compile(r"^\s*(\d+)\)\s*(.+)$")
_POLLSTAR_TIER_LABEL = re.compile(
    r"^more than [\d,]+$|^\d[\d,]+\s*[–-]\s*\d[\d,]+$|^\d[\d,]+\s+or less$",
    re.IGNORECASE,
)
_POLLSTAR_FIELDS = {
    "tickets_sold": re.compile(r"Tickets Sold:\s*([\d,]+)", re.IGNORECASE),
    "venue": re.compile(r"Venue:\s*(.+?)\s*;", re.IGNORECASE),
    "gross": re.compile(r"Gross:\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE),
    "range": re.compile(r"Ticket Range:\s*\$?([\d.,]+)\s*-\s*\$?([\d.,]+)", re.IGNORECASE),
    "promoter": re.compile(r"Promoter:\s*(.+?)\s*;", re.IGNORECASE),
    "dates": re.compile(r"Dates:\s*(.+?)\s*;", re.IGNORECASE),
    "shows": re.compile(r"No\. of Shows:\s*(\d+)", re.IGNORECASE),
}


def _pollstar_tier_labels(lines: list[str]) -> list[str]:
    """Extract Pollstar capacity-tier labels (in page order)."""
    tiers: list[str] = []
    pending: str | None = None
    for line in lines:
        head = _POLLSTAR_HEAD.match(line)
        if head:
            break  # tiers are all listed before the first record
        if line.lower() == "capacity":
            if pending:
                tiers.append(f"{pending} Capacity")
                pending = None
            continue
        if _POLLSTAR_TIER_LABEL.match(line):
            pending = line
            continue
        one_line = re.match(r"^(.+?)\s+capacity$", line, re.IGNORECASE)
        if one_line and _POLLSTAR_TIER_LABEL.match(one_line.group(1).strip()):
            tiers.append(line)
            pending = None
    return tiers


def parse_pollstar_hot_tickets(text: str, *, source_url: str | None = None) -> list[BoxofficeEngagement]:
    """Parse a Pollstar Hot Tickets page.

    The chart lists top-5 engagements per capacity tier (4 tiers, up to 20
    records). Tier headers appear as a block before the records, and each tier
    restarts ranking at 1, so a record's tier is inferred from its position
    (records are grouped in blocks of 5 per tier).
    """
    engagements: list[BoxofficeEngagement] = []
    lines = [ln.strip() for ln in text.splitlines()]
    tiers = _pollstar_tier_labels(lines)

    pending_artist: str | None = None
    pending_rank: int | None = None
    records: list[tuple[int | None, str, dict[str, str | None]]] = []

    for line in lines:
        head = _POLLSTAR_HEAD.match(line)
        if head:
            pending_rank = int(head.group(1))
            pending_artist = head.group(2).strip()
            continue
        if pending_artist is not None and "Tickets Sold:" in line:
            fields: dict[str, str | None] = {}
            for key, pat in _POLLSTAR_FIELDS.items():
                m = pat.search(line)
                fields[key] = m.group(1) if m else None
            rm = _POLLSTAR_FIELDS["range"].search(line)
            if rm:
                fields["range_min"] = rm.group(1)
                fields["range_max"] = rm.group(2)
            records.append((pending_rank, pending_artist, fields))
            pending_artist = None
            pending_rank = None

    for index, (rank, artist, fields) in enumerate(records):
        headcount = _to_float(fields["tickets_sold"])
        gross = _to_float(fields["gross"])
        price_min = _to_float(fields.get("range_min"))
        price_max = _to_float(fields.get("range_max"))
        shows = _to_int(fields["shows"])
        venue_full = fields["venue"]
        venue = venue_full
        city = None
        if venue_full and "," in venue_full:
            before, _, after = venue_full.rpartition(",")
            venue = before.strip()
            city = after.strip()
        start, end, _ = _dates_from_raw(fields["dates"])
        tier = tiers[index // 5] if tiers and index // 5 < len(tiers) else None
        engagements.append(BoxofficeEngagement.build(
            reporting_source=SOURCE_POLLSTAR,
            rank=rank,
            artist=artist,
            venue=venue,
            market=venue_full,
            city=city,
            promoter=fields["promoter"],
            start_date=start,
            end_date=end,
            dates_raw=fields["dates"],
            number_of_shows=shows,
            headcount_total=headcount,
            headcount_definition=HEADCOUNT_PAID_TICKETS,
            ticket_gross_total=gross,
            price_min=price_min,
            price_max=price_max,
            prices_raw=fields["range"],
            capacity_tier=tier,
            source_url=source_url,
            is_multi_show=bool(shows and shows > 1),
            is_reported=True,
            is_estimated=False,
        ))
    return engagements


# ---------------------------------------------------------------------------
# Touring Data (date-level attendance/gross; reported vs estimated)
# ---------------------------------------------------------------------------
_TOURING_LINE = re.compile(
    r"^(?P<dates>[A-Za-z]+\.?\s+\d{1,2}(?:-\d{1,2})?(?:,\s*\d{1,2})?(?:,\s*\d{1,2})?,\s+\d{4}):\s*"
    r"(?P<venue>.+?),\s*(?P<city>[^()]+?)\s*\((?P<attendance>[\d,]+)\s*[–—-]\s*\$?(?P<gross>[\d,]+(?:\.\d+)?)\)"
    r"(?:\s*\((?P<shows>\d+)\s*shows?\))?",
    re.IGNORECASE,
)


def parse_touring_data(
    text: str,
    *,
    source_url: str | None = None,
    artist: str | None = None,
) -> list[BoxofficeEngagement]:
    engagements: list[BoxofficeEngagement] = []
    current_is_estimated = False
    tour_artist = (artist or "").strip()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r"total (reported|estimated) (gross|attendance)", stripped, re.IGNORECASE):
            current_is_estimated = "estimated" in stripped.lower() and "reported" not in stripped.lower()
            continue
        if re.search(r"^reported shows|^estimated shows", stripped, re.IGNORECASE):
            continue

        m = _TOURING_LINE.match(stripped)
        if not m:
            continue

        attendance = _to_float(m.group("attendance"))
        gross = _to_float(m.group("gross"))
        shows = _to_int(m.group("shows")) if m.group("shows") else None
        dates_raw = m.group("dates").strip()
        venue = m.group("venue").strip()
        city = m.group("city").strip()
        start, end, single_show = _dates_from_raw(dates_raw)
        if single_show == 1:
            shows = shows or 1
        is_estimated = current_is_estimated or bool(re.search(r"\bestimated\b|~", stripped, re.IGNORECASE))

        engagements.append(BoxofficeEngagement.build(
            reporting_source=SOURCE_TOURING_DATA,
            artist=tour_artist,
            venue=venue,
            city=city,
            start_date=start,
            end_date=end,
            dates_raw=dates_raw,
            number_of_shows=shows,
            headcount_total=attendance,
            headcount_definition=HEADCOUNT_REPORTED_ATTENDANCE,
            ticket_gross_total=gross,
            source_url=source_url,
            is_multi_show=bool(shows and shows > 1),
            is_reported=not is_estimated,
            is_estimated=is_estimated,
        ))
    return engagements


# ---------------------------------------------------------------------------
# Touring Data (2024+) block format
# ---------------------------------------------------------------------------
# Current Touring Data pages render each engagement as a 7-line block:
#
#   March 5-7, 2024          <- dates (start of block)
#   Zach Bryan               <- artist
#   United Center            <- venue
#   Chicago, United States   <- city
#   $12,648,557              <- gross (or "TBA" for unreported/upcoming)
#   56,931 (100%)            <- headcount + sell-through (or "TBA")
#   3 shows                  <- number of shows
#
# Only blocks with numeric gross AND headcount are REPORTED outcomes.
# "TBA" blocks are upcoming/unreported shows and are skipped (never promoted).
# A block whose headcount/gross carries a tilde is treated as estimated.
# ---------------------------------------------------------------------------

_TOURING_DATE_LINE = re.compile(
    r"^(?P<month>[A-Za-z]+)\.?\s+(?P<day>\d{1,2})"
    r"(?:\s*[–-]\s*(?:(?P<end_month>[A-Za-z]+)\.?\s+)?(?P<end_day>\d{1,2}))?"
    r",?\s+(?P<year>(?:19|20)\d{2})$",
    re.IGNORECASE,
)
_TOURING_SHOWS_LINE = re.compile(r"^(?P<shows>\d+)\s+shows?$", re.IGNORECASE)
_TOURING_HEADCOUNT_LINE = re.compile(r"^([\d,]+)\s*\((\d+(?:\.\d+)?)%\)$")


def parse_touring_data_blocks(
    text: str,
    *,
    source_url: str | None = None,
    artist: str | None = None,
    tour: str | None = None,
) -> tuple[list[BoxofficeEngagement], dict[str, int]]:
    """Parse the current (2024+) Touring Data block layout.

    Returns ``(engagements, skipped)`` where ``skipped`` counts unreported
    (TBA) and estimated blocks that were NOT emitted as reported outcomes.
    """
    engagements: list[BoxofficeEngagement] = []
    skipped = {"unreported": 0, "estimated": 0, "malformed": 0}
    lines = [ln.strip() for ln in (text or "").splitlines()]
    tour_artist = (artist or "").strip()

    i = 0
    n = len(lines)
    while i < n:
        m = _TOURING_DATE_LINE.match(lines[i])
        if not m:
            i += 1
            continue
        if i + 6 >= n:
            break
        block = lines[i : i + 7]
        dates_raw = block[0]
        block_artist = block[1].strip()
        venue = block[2].strip()
        city = block[3].strip()
        gross_raw = block[4].strip()
        headcount_raw = block[5].strip()
        shows_raw = block[6].strip()
        i += 7

        # start / end date from the block's own date line
        month = m.group("month")
        day = int(m.group("day"))
        year = int(m.group("year"))
        start = _parse_iso_date(month, day, year)
        end = start
        if m.group("end_day"):
            end_month = m.group("end_month") or month
            end = _parse_iso_date(end_month, int(m.group("end_day")), year)

        shows_m = _TOURING_SHOWS_LINE.match(shows_raw)
        shows = int(shows_m.group("shows")) if shows_m else None

        is_estimated = bool(re.search(r"~|\best\.?\b|\bestimated\b", gross_raw + " " + headcount_raw, re.IGNORECASE))
        if gross_raw.upper() == "TBA" or headcount_raw.upper() == "TBA":
            skipped["unreported"] += 1
            continue

        gross = _to_float(gross_raw.lstrip("~ "))
        headcount = None
        sell_through = None
        headcount_clean = headcount_raw.lstrip("~ ")
        hm = _TOURING_HEADCOUNT_LINE.match(headcount_clean)
        if hm:
            headcount = _to_float(hm.group(1))
            sell_through = _to_float(hm.group(2))
        else:
            headcount = _to_float(headcount_clean)

        if gross is None or headcount is None:
            skipped["malformed"] += 1
            continue
        if is_estimated:
            skipped["estimated"] += 1

        engagements.append(BoxofficeEngagement.build(
            reporting_source=SOURCE_TOURING_DATA,
            artist=block_artist or tour_artist,
            tour=tour,
            venue=venue,
            city=city,
            start_date=start,
            end_date=end,
            dates_raw=dates_raw,
            number_of_shows=shows,
            headcount_total=headcount,
            headcount_definition=HEADCOUNT_REPORTED_ATTENDANCE,
            headcount_source_label="Tickets Sold (reported headcount)",
            sell_through_pct=sell_through,
            ticket_gross_total=gross,
            source_url=source_url,
            is_multi_show=bool(shows and shows > 1),
            is_reported=not is_estimated,
            is_estimated=is_estimated,
        ))
    return engagements, skipped


def parse_touring_data_auto(
    text: str,
    *,
    source_url: str | None = None,
    artist: str | None = None,
    tour: str | None = None,
) -> tuple[list[BoxofficeEngagement], dict[str, int]]:
    """Dispatch to the right Touring Data parser.

    The 2024+ block layout is tried first; if it finds nothing, fall back to
    the legacy inline ``Date: Venue, City (attendance - $gross)`` layout used
    by 2019-era pages.
    """
    blocks, skipped = parse_touring_data_blocks(
        text, source_url=source_url, artist=artist, tour=tour,
    )
    if blocks:
        return blocks, skipped
    legacy = parse_touring_data(text, source_url=source_url, artist=artist)
    return legacy, {"unreported": 0, "estimated": 0, "malformed": 0}
