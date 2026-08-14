"""YouTube collection coverage / censoring annotations.

Capped raw comment counts are observations of the sample, not population
estimates and not artist popularity rankings.
"""

from __future__ import annotations

from typing import Any

COMPLETE = "COMPLETE"
CAPPED = "CAPPED"
PARTIAL = "PARTIAL"
COMMENTS_DISABLED = "COMMENTS_DISABLED"
UNKNOWN = "UNKNOWN"


def sampling_status(
    *,
    comments_disabled: bool = False,
    comment_count_cap_hit: bool = False,
    comment_page_cap_hit: bool = False,
    comments_retrieved: int = 0,
    comments_reported: int | None = None,
) -> str:
    if comments_disabled:
        return COMMENTS_DISABLED
    if comment_count_cap_hit or comment_page_cap_hit:
        return CAPPED
    if comments_reported is None and comments_retrieved == 0:
        return UNKNOWN
    if comments_reported is not None and comments_retrieved < comments_reported:
        return PARTIAL
    return COMPLETE


def coverage_fraction(retrieved: int, reported: int | None) -> float | None:
    if reported is None or reported <= 0:
        return None
    return retrieved / reported


def annotate_coverage(
    *,
    videos_discovered: int,
    videos_selected: int,
    videos_with_comments_enabled: int,
    comments_reported: int | None,
    comments_requested: int,
    comments_retrieved: int,
    comment_pages_fetched: int,
    comment_page_cap_hit: bool,
    comment_count_cap_hit: bool,
    comments_disabled: bool = False,
) -> dict[str, Any]:
    status = sampling_status(
        comments_disabled=comments_disabled,
        comment_count_cap_hit=comment_count_cap_hit,
        comment_page_cap_hit=comment_page_cap_hit,
        comments_retrieved=comments_retrieved,
        comments_reported=comments_reported,
    )
    return {
        "videos_discovered": videos_discovered,
        "videos_eligible": videos_selected,
        "videos_selected": videos_selected,
        "videos_with_comments_enabled": videos_with_comments_enabled,
        "comments_reported_by_video": comments_reported,
        "comments_requested": comments_requested,
        "comments_retrieved": comments_retrieved,
        "observed_count": comments_retrieved,
        "available_population_count": comments_reported,
        "comment_pages_fetched": comment_pages_fetched,
        "comment_page_cap_hit": comment_page_cap_hit,
        "comment_count_cap_hit": comment_count_cap_hit,
        "coverage_complete": status == COMPLETE,
        "coverage_fraction": coverage_fraction(comments_retrieved, comments_reported),
        "sampling_status": status,
    }
