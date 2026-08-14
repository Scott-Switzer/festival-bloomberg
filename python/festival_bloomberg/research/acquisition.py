"""Public box-office source registry and research-corpus reporting.

The registry is a bounded, provenance-backed list of public box-office pages.
Every source is RESEARCH_ONLY / TERMS_REVIEW_REQUIRED: the corpus report keeps
the research vs commercial-eligible split explicit and fail-closed.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..acquisition.transport import HttpTransport
from .boxscore import (
    SOURCE_BILLBOARD,
    SOURCE_POLLSTAR,
    SOURCE_TOURING_DATA,
    html_to_text_lines,
    parse_billboard_boxscore_html,
    parse_pollstar_hot_tickets,
    parse_touring_data_auto,
)

# (source, url, parser_kind)
BOXSCORE_SOURCES: list[tuple[str, str, str]] = [
    (
        SOURCE_BILLBOARD,
        "https://www.webcitation.org/getfile.php?fileid=6d68d0bde5484901a2b65c60c00a9e7bbde628dc",
        "billboard_html",
    ),
    (
        SOURCE_POLLSTAR,
        "https://news.pollstar.com/2024/04/18/hot-tickets-april-18-2024/",
        "pollstar_text",
    ),
    # Touring Data pages are registered separately (they carry a tour artist),
    # see oa/boxscore.py TOURING_PAGES.
]

# Verified public Pollstar "Hot Tickets" pages (biweekly, Jan-May 2024).
# The chart is ~top-20 per week across four capacity tiers. Each page was
# reach-checked before inclusion; 404s are reported honestly, not retried.
POLLSTAR_ARCHIVE: list[tuple[str, str]] = [
    ("https://news.pollstar.com/2024/01/11/hot-tickets-january-11-2024/", "2024-01-11"),
    ("https://news.pollstar.com/2024/01/18/hot-tickets-january-18-2024/", "2024-01-18"),
    ("https://news.pollstar.com/2024/02/15/hot-tickets-february-15-2024/", "2024-02-15"),
    ("https://news.pollstar.com/2024/02/22/hot-tickets-february-22-2024/", "2024-02-22"),
    ("https://news.pollstar.com/2024/03/14/hot-tickets-march-14-2024/", "2024-03-14"),
    ("https://news.pollstar.com/2024/03/21/hot-tickets-march-21-2024/", "2024-03-21"),
    ("https://news.pollstar.com/2024/04/11/hot-tickets-april-11-2024/", "2024-04-11"),
    ("https://news.pollstar.com/2024/04/18/hot-tickets-april-18-2024/", "2024-04-18"),
    ("https://news.pollstar.com/2024/04/25/hot-tickets-april-25-2024/", "2024-04-25"),
    ("https://news.pollstar.com/2024/05/16/hot-tickets-may-16-2024/", "2024-05-16"),
    ("https://news.pollstar.com/2024/05/23/hot-tickets-may-23-2024/", "2024-05-23"),
    ("https://news.pollstar.com/2024/05/30/hot-tickets-may-30-2024/", "2024-05-30"),
]

TOURING_DATA_CATEGORY_URL = "https://touringdata.org/category/data/"

_TOUR_SLUG = re.compile(r"/20\d\d/\d\d/\d\d/([a-z0-9\-]+)/?$")
_YEAR_END_SLUG = re.compile(r"^(?:19|20)\d{2}|top-touring|year-end|worldwide", re.IGNORECASE)


def discover_touring_data_pages(
    transport: HttpTransport,
    *,
    category_url: str = TOURING_DATA_CATEGORY_URL,
    max_pages: int = 20,
) -> list[tuple[str, str]]:
    """Discover individual tour pages from the Touring Data category listing.

    Returns ``(url, slug)`` for bounded, de-duplicated tour pages. Year-end /
    top-touring summaries are excluded. Deterministic order (sorted by slug).
    """
    response = transport.request("GET", category_url, timeout_seconds=45)
    if response.status != 200:
        return []
    html = response.body.decode("utf-8", errors="replace")
    seen: dict[str, str] = {}
    for href in re.findall(r"href=[\"']([^\"']+)[\"']", html):
        m = _TOUR_SLUG.search(href)
        if not m:
            continue
        slug = m.group(1)
        if _YEAR_END_SLUG.search(slug):
            continue
        if slug not in seen:
            seen[slug] = href
    # return (url, slug) pairs, deterministically ordered by slug
    return [(href, slug) for slug, href in sorted(seen.items())][:max_pages]


def slug_to_label(slug: str) -> str:
    """Best-effort display name for a Touring Data slug (identity stays the slug)."""
    return " ".join(part for part in slug.replace("-", " ").split() if part)


def parse_source_with_meta(
    source: str,
    content: str,
    *,
    source_url: str,
    **kwargs: Any,
) -> tuple[list[Any], dict[str, Any]]:
    """Parse source content into engagements + per-source metadata.

    Touring Data returns a ``skipped`` meta (unreported/estimated blocks).
    """
    if source == SOURCE_BILLBOARD:
        return parse_billboard_boxscore_html(content, source_url=source_url), {}
    if source == SOURCE_POLLSTAR:
        return parse_pollstar_hot_tickets(html_to_text_lines(content), source_url=source_url), {}
    if source == SOURCE_TOURING_DATA:
        engagements, skipped = parse_touring_data_auto(
            html_to_text_lines(content), source_url=source_url, **kwargs
        )
        return engagements, {"skipped": skipped}
    raise ValueError(f"unknown source {source!r}")


def parse_source(source: str, content: str, *, source_url: str, **kwargs: Any) -> list[Any]:
    """Back-compat wrapper (V1 callers): returns engagements only."""
    engagements, _meta = parse_source_with_meta(source, content, source_url=source_url, **kwargs)
    return engagements


def corpus_report(research_repo) -> dict[str, Any]:
    """Engagement-level summary + research/commercial split.

    The entire public box-office corpus is research-only by construction; the
    commercial-eligible count is therefore zero and is asserted, not assumed.
    """
    engagements = research_repo.query_engagements()
    by_source = Counter(e["reporting_source"] for e in engagements)
    by_headcount = Counter(e["headcount_definition"] for e in engagements)
    by_rights = Counter(e["rights_status"] for e in engagements)
    reported = sum(1 for e in engagements if e.get("is_reported"))
    estimated = sum(1 for e in engagements if e.get("is_estimated"))
    single = sum(1 for e in engagements if not e.get("is_multi_show"))
    multi = sum(1 for e in engagements if e.get("is_multi_show"))
    chicago = sum(1 for e in engagements if (e.get("city") or "").lower() in {"chicago", "chicago, il"})

    commercial_eligible = sum(
        1 for e in engagements if e.get("commercial_use_status") not in (
            "RESEARCH_ONLY", "TERMS_REVIEW_REQUIRED", "UNKNOWN",
        )
    )

    return {
        "engagements_total": len(engagements),
        "by_source": dict(by_source),
        "by_headcount_definition": dict(by_headcount),
        "by_rights": dict(by_rights),
        "reported": reported,
        "estimated": estimated,
        "single_show": single,
        "multi_show": multi,
        "chicago_engagements": chicago,
        "research_corpus": len(engagements),
        "commercial_eligible_corpus": commercial_eligible,
        "rights_verdict": "FAIL_CLOSED" if commercial_eligible == 0 else "REVIEW",
    }
