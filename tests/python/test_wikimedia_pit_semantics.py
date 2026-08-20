"""Regression tests for Wikimedia historical-attention PIT semantics.

These tests lock the distinction that the Comparable V2 closure got wrong in
its first revision: ``retrieved_at`` (when Festival Bloomberg downloaded a
value) is NEVER a point-in-time admissibility gate. Admissibility is governed
by ``observation_time`` (the day the views occurred) and ``available_at`` (when
the source made that day's aggregate knowable), per
``attention.historical_pit``.

Wikimedia's documented load semantics are: a day's pageview aggregate is loaded
at the end of the relevant period, so ``available_at = observation_day + 1``
(00:00 UTC). The Analytics API serves data starting 2015-07-01; days before
that are UNAVAILABLE, never missing and never zero.
"""

from __future__ import annotations

from datetime import date, timedelta

from festival_bloomberg.attention.historical_pit import calendar_window, daily_series, pit_features
from festival_bloomberg.attention.wikimedia_pageviews import (
    WIKIMEDIA_SERIES_START,
    wikimedia_available_at,
)


def _obs(day: str, value: float, retrieved_at: str = "2026-08-19T00:00:00+00:00") -> dict:
    return {"day": day, "value": value, "retrieved_at": retrieved_at}


def _daily(rows, cutoff):
    """Build a daily series using the Wikimedia availability policy."""
    return daily_series(
        rows,
        value_fn=lambda r: r["value"],
        day_fn=lambda r: r["day"],
        available_fn=lambda r: wikimedia_available_at(date.fromisoformat(r["day"])).isoformat(),
        cutoff=cutoff,
    )


def test_wikimedia_available_at_is_day_plus_one():
    assert wikimedia_available_at(date(2024, 5, 10)) == date(2024, 5, 11)
    assert wikimedia_available_at(date(2015, 7, 1)) == date(2015, 7, 2)


def test_wikimedia_series_starts_2015_07_01():
    assert WIKIMEDIA_SERIES_START == date(2015, 7, 1)


def test_late_retrieval_with_source_available_before_cutoff_is_admissible():
    """A 2019 day fetched in 2026 is admissible at a 2020 cutoff: the source
    published that day's aggregate in 2019, so the late download is irrelevant."""
    cutoff = "2020-01-01"
    rows = [_obs("2019-12-20", 100.0, retrieved_at="2026-08-19T00:00:00+00:00")]
    daily = _daily(rows, cutoff)
    assert daily == {date(2019, 12, 20): 100.0}


def test_late_retrieval_does_not_make_value_unknowable():
    """Two identical observations differing only in retrieved_at produce the
    SAME admissible series — retrieval time alone never changes admissibility."""
    cutoff = "2020-01-01"
    early = _obs("2019-12-20", 100.0, retrieved_at="2019-12-21T00:00:00+00:00")
    late = _obs("2019-12-20", 100.0, retrieved_at="2026-08-19T00:00:00+00:00")
    assert _daily([early], cutoff) == _daily([late], cutoff)


def test_source_available_after_cutoff_is_inadmissible_even_if_retrieved_early():
    """available_at = observation_day + 1; if that bound is NOT before the
    cutoff, the day is excluded even when WE retrieved it early."""
    # cutoff 2020-01-01; observation 2019-12-31 -> available 2020-01-01,
    # which is NOT strictly before the cutoff.
    cutoff = "2020-01-01"
    rows = [_obs("2019-12-31", 50.0, retrieved_at="2019-12-31T23:00:00+00:00")]
    assert _daily(rows, cutoff) == {}


def test_observation_after_cutoff_is_inadmissible():
    cutoff = "2020-01-01"
    rows = [
        _obs("2019-12-31", 50.0),  # observation < cutoff (excluded by availability)
        _obs("2020-01-01", 999.0),  # same day as cutoff -> excluded
        _obs("2020-01-02", 999.0),  # future -> excluded
        _obs("2019-12-20", 100.0),  # admissible
    ]
    daily = _daily(rows, cutoff)
    assert daily == {date(2019, 12, 20): 100.0}


def test_retrieved_at_is_never_a_gate_column():
    """The daily_series available_fn only reads the observation day. A value's
    admissibility cannot be flipped by mutating retrieved_at."""
    cutoff = "2020-01-01"
    rows = [
        _obs("2019-12-20", 100.0, retrieved_at="2026-08-19T00:00:00+00:00"),
        _obs("2019-12-21", 200.0, retrieved_at="2000-01-01T00:00:00+00:00"),
    ]
    daily = _daily(rows, cutoff)
    # both days are admissible (both available 2019, before 2020 cutoff);
    # the wildly different retrieved_at values changed nothing.
    assert daily == {date(2019, 12, 20): 100.0, date(2019, 12, 21): 200.0}


def test_pre_2015_days_are_unavailable_not_missing_or_zero():
    """Days before the Wikimedia series start are UNAVAILABLE (the source did
    not exist), never MISSING and never fabricated ZERO. The caller marks them
    via the ``unavailable`` set using the authoritative series-start boundary."""
    days = {date(2014, 5, 19): 1.0, date(2014, 5, 20): 2.0}
    # every day strictly before the series start is unavailable
    unavailable = {d.isoformat() for d in days if d < WIKIMEDIA_SERIES_START}
    assert unavailable == {"2014-05-19", "2014-05-20"}
    w = calendar_window(
        days,
        start="2014-05-19",
        end="2014-05-20",
        complete=False,
        unavailable=unavailable,
    )
    assert w["status"] == "OK"
    # every day in the window is before the series start -> nothing expected
    assert w["expected_days"] == 0
    assert w["unavailable_days"] == 2
    # explicitly: a 2014 day is before the series start
    assert date(2014, 5, 20) < WIKIMEDIA_SERIES_START


def test_historical_window_end_before_cutoff_is_pit_safe():
    """A trailing window ending before a cutoff is PIT-computable from days
    whose availability bound is also strictly before the cutoff.

    With ``available_at = observation_day + 1`` and the strict
    ``available_at < cutoff`` rule, the latest admissible observation day
    before cutoff C is C-2 (C-1 becomes available exactly at C, not before it).
    This is the conservative consequence of day-level source load semantics.
    """
    cutoff = "2024-06-01"
    cutoff_d = date(2024, 6, 1)
    rows = []
    # admissible days: d+1 < 2024-06-01  =>  d <= 2024-05-30.
    # generate 30 such days: 2024-05-01 .. 2024-05-30.
    for i in range(0, 30):
        d = cutoff_d - timedelta(days=30) + timedelta(days=i)
        rows.append(_obs(d.isoformat(), 1.0))
    daily = _daily(rows, cutoff)
    f = pit_features(daily, cutoff=cutoff)
    assert f["status"] == "OK"
    # pit_features 30d window = [cutoff-30, cutoff) = [05-02, 06-01);
    # of the 30 admissible days, 05-01 lies just outside, so 29 land inside.
    # The latest admissible day (05-30) is inside and available (05-31) < cutoff.
    assert f["30d"] == 29.0
    # sanity: the day immediately before cutoff (05-31) is NOT admissible
    row_0531 = _obs("2024-05-31", 1.0)
    assert _daily([row_0531], cutoff) == {}
