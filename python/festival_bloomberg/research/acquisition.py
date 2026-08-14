"""Public box-office source registry and research-corpus reporting.

The registry is a bounded, provenance-backed list of public box-office pages.
Every source is RESEARCH_ONLY / TERMS_REVIEW_REQUIRED: the corpus report keeps
the research vs commercial-eligible split explicit and fail-closed.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .boxscore import (
    SOURCE_BILLBOARD,
    SOURCE_POLLSTAR,
    SOURCE_TOURING_DATA,
    html_to_text_lines,
    parse_billboard_boxscore_html,
    parse_pollstar_hot_tickets,
    parse_touring_data,
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


def parse_source(source: str, content: str, *, source_url: str, **kwargs: Any) -> list[Any]:
    if source == SOURCE_BILLBOARD:
        return parse_billboard_boxscore_html(content, source_url=source_url)
    if source == SOURCE_POLLSTAR:
        return parse_pollstar_hot_tickets(html_to_text_lines(content), source_url=source_url)
    if source == SOURCE_TOURING_DATA:
        return parse_touring_data(html_to_text_lines(content), source_url=source_url, **kwargs)
    raise ValueError(f"unknown source {source!r}")


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
