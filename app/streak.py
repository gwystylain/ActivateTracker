"""Pure functions for visit aggregation and the trailing-window discount.

A "visit" is one calendar day on which the player's totalScore at *any* of
their tracked locations increased compared to the previous snapshot for that
location. Multiple location-increases on the same day count as one visit
(matches Activate's per-day visit accounting).

Activate discount rules (based on visits in the trailing 30 days):
  - 0 visits  -> 0% off
  - 1 visit   -> 10% off
  - 2 visits  -> 15% off
  - 3 visits  -> 20% off
  - 4+ visits -> 25% off
  - `initial_streak` is an admin-set baseline for visits that happened before
    tracking started; it is treated as that many visits on
    `initial_streak_set_at` and only counts while that date is still inside
    the 30-day window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

DISCOUNT_WINDOW_DAYS = 30
DISCOUNT_MAX_PCT = 25

#: playactivate's score refresh lags by ~1 day (see `activity_day`).
SCORE_REFRESH_LAG_DAYS = 1


def activity_day(polled_at: str | date | datetime) -> date:
    """The calendar day of play that a poll's scores describe.

    playactivate's score refresh lags by ~1 day: a visit on May 14 first shows
    up in the poll on May 15. Everything that dates the *data* — the visit rows
    and the chart's x-axis — has to subtract that lag, or it reports the day we
    noticed instead of the day the player was there. The stored `polled_at` is
    deliberately not shifted: that one really is when we fetched.
    """
    if isinstance(polled_at, str):
        day = date.fromisoformat(polled_at[:10])
    elif isinstance(polled_at, datetime):  # before date: datetime is a date
        day = polled_at.date()
    else:
        day = polled_at
    return day - timedelta(days=SCORE_REFRESH_LAG_DAYS)


@dataclass(frozen=True)
class StreakSummary:
    discount_pct: int
    days_since_last_visit: int | None
    last_visit_date: date | None
    visits_last_30_days: int
    visits_ytd: int
    visits_by_year: dict[int, int]


def discount_for(visits_last_30_days: int) -> int:
    """Tiered discount: first visit is 10%, then +5% per visit, capped at 25%."""
    n = max(visits_last_30_days, 0)
    if n <= 0:
        return 0
    return min(5 + 5 * n, DISCOUNT_MAX_PCT)


def visits_in_window(
    visit_dates: list[date],
    *,
    initial_streak: int = 0,
    initial_streak_set_at: date | None = None,
    today: date | None = None,
    window_days: int = DISCOUNT_WINDOW_DAYS,
) -> int:
    """Count unique visit days falling within the trailing `window_days`.

    The admin baseline is treated as `initial_streak` visits on
    `initial_streak_set_at` and only contributes while that date is still
    inside the window.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=window_days)
    count = sum(1 for d in set(visit_dates) if cutoff <= d <= today)
    if (
        initial_streak > 0
        and initial_streak_set_at is not None
        and cutoff <= initial_streak_set_at <= today
    ):
        count += initial_streak
    return count


def summarize(
    visit_dates: list[date],
    *,
    initial_streak: int = 0,
    initial_streak_set_at: date | None = None,
    today: date | None = None,
    history_years: int = 4,
) -> StreakSummary:
    today = today or date.today()
    unique_sorted = sorted(set(visit_dates))
    last = unique_sorted[-1] if unique_sorted else initial_streak_set_at

    visits_last_30_days = visits_in_window(
        unique_sorted,
        initial_streak=initial_streak,
        initial_streak_set_at=initial_streak_set_at,
        today=today,
    )
    visits_ytd = sum(1 for d in unique_sorted if d.year == today.year)

    by_year: dict[int, int] = {}
    for offset in range(history_years):
        y = today.year - offset
        by_year[y] = sum(1 for d in unique_sorted if d.year == y)

    days_since = (today - last).days if last is not None else None

    return StreakSummary(
        discount_pct=discount_for(visits_last_30_days),
        days_since_last_visit=days_since,
        last_visit_date=last if unique_sorted else None,
        visits_last_30_days=visits_last_30_days,
        visits_ytd=visits_ytd,
        visits_by_year=by_year,
    )


def previous_year_buckets(today: date, count: int) -> list[int]:
    """Return [today.year, today.year-1, ..., today.year-(count-1)]."""
    return [today.year - i for i in range(count)]
