"""Session-scoped YouTube Data API quota accounting.

Google's per-method unit costs have changed over time, so this module does
not treat any unit table as eternal. For the bounded live OA session we
enforce conservative *method-call* caps and record actual call counts:

* ``search.list`` <= 25
* all other YouTube read methods combined <= 500

No writes, uploads, moderation actions, or OAuth-private data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEARCH_LIST_CAP = 25
OTHER_READ_CAP = 500

SEARCH_METHODS = frozenset({"search.list"})
OTHER_READ_METHODS = frozenset(
    {
        "videos.list",
        "commentThreads.list",
        "comments.list",
        "channels.list",
    }
)

#: Documented as of 2026-08 (YouTube Data API quota docs). Informational
#: only — session enforcement uses method-call caps, not these units.
DOCUMENTED_UNITS_2026_08 = {
    "search.list": 1,
    "videos.list": 1,
    "commentThreads.list": 1,
    "comments.list": 1,
}


class YouTubeQuotaBudgetExceeded(RuntimeError):
    """Raised when a call would exceed the session method-call cap."""

    def __init__(self, method: str, usage: dict[str, int]) -> None:
        self.method = method
        self.usage = usage
        super().__init__(f"YouTube quota budget exceeded for {method}")


@dataclass
class YouTubeQuotaBudget:
    search_list_cap: int = SEARCH_LIST_CAP
    other_read_cap: int = OTHER_READ_CAP
    search_list_calls: int = 0
    videos_list_calls: int = 0
    commentThreads_list_calls: int = 0
    comments_list_calls: int = 0
    channels_list_calls: int = 0
    stopped_reason: str | None = None
    _other_methods: dict[str, int] = field(default_factory=dict)

    @property
    def total_read_calls(self) -> int:
        return (
            self.search_list_calls
            + self.videos_list_calls
            + self.commentThreads_list_calls
            + self.comments_list_calls
            + self.channels_list_calls
            + sum(self._other_methods.values())
        )

    @property
    def other_read_calls(self) -> int:
        return self.total_read_calls - self.search_list_calls

    def remaining_search(self) -> int:
        return max(0, self.search_list_cap - self.search_list_calls)

    def remaining_other(self) -> int:
        return max(0, self.other_read_cap - self.other_read_calls)

    def would_exceed(self, method: str) -> bool:
        if method in SEARCH_METHODS:
            return self.search_list_calls + 1 > self.search_list_cap
        return self.other_read_calls + 1 > self.other_read_cap

    def consume(self, method: str) -> None:
        if self.would_exceed(method):
            self.stopped_reason = "QUOTA_STOP"
            raise YouTubeQuotaBudgetExceeded(method, self.as_dict())
        if method == "search.list":
            self.search_list_calls += 1
        elif method == "videos.list":
            self.videos_list_calls += 1
        elif method == "commentThreads.list":
            self.commentThreads_list_calls += 1
        elif method == "comments.list":
            self.comments_list_calls += 1
        elif method == "channels.list":
            self.channels_list_calls += 1
        else:
            self._other_methods[method] = self._other_methods.get(method, 0) + 1

    def as_dict(self) -> dict[str, int]:
        return {
            "search_list_calls": self.search_list_calls,
            "videos_list_calls": self.videos_list_calls,
            "commentThreads_list_calls": self.commentThreads_list_calls,
            "comments_list_calls": self.comments_list_calls,
            "total_read_calls": self.total_read_calls,
        }
