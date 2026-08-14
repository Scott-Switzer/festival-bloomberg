"""Public box-office research corpus.

A RESEARCH_CORPUS of reported box-office engagements (Billboard Boxscore,
Pollstar Hot Tickets, Touring Data, openICPSR, OpenMusE). These sources are
RESEARCH_ONLY / TERMS_REVIEW_REQUIRED and never enter the commercial-eligible
corpus. The engagement model keeps multi-show aggregates distinct from
single-show event-level labels, and never divides a multi-show total.
"""

from .boxscore import (
    HEADCOUNT_PAID_TICKETS,
    HEADCOUNT_REPORTED_ATTENDANCE,
    HEADCOUNT_UNSPECIFIED,
    SOURCE_BILLBOARD,
    SOURCE_POLLSTAR,
    SOURCE_TOURING_DATA,
    BoxofficeEngagement,
    parse_billboard_boxscore_html,
    parse_pollstar_hot_tickets,
    parse_touring_data,
)
from .repository import ResearchRepository

__all__ = [
    "HEADCOUNT_PAID_TICKETS",
    "HEADCOUNT_REPORTED_ATTENDANCE",
    "HEADCOUNT_UNSPECIFIED",
    "SOURCE_BILLBOARD",
    "SOURCE_POLLSTAR",
    "SOURCE_TOURING_DATA",
    "BoxofficeEngagement",
    "ResearchRepository",
    "parse_billboard_boxscore_html",
    "parse_pollstar_hot_tickets",
    "parse_touring_data",
]
