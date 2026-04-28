"""Pure functions for streak / visit aggregation.

A "visit" is one calendar day on which the player's totalScore at *any* of
their tracked locations increased compared to the previous snapshot for that
location. Multiple location-increases on the same day count as one visit
(matches Activate's per-day visit accounting).

Activate streak rules:
  - Each visit advances the streak by 1.
  - Discount = min(streak, 5) * 5 percent (capped at 25%).
  - If 30 days elapse with no visit, the streak resets to 0.
  - `initial_streak` is an admin-set baseline for visits that happened
    before tracking started; treat it as if it were established on
    `initial_streak_set_at`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

STREAK_RESET_DAYS = 30
DISCOUNT_CAP_VISITS = 5
DISCOUNT_PCT_PER_VISIT = 5


@dataclass(frozen=True)
class StreakSummary:
    current_streak: int
    discount_pct: int
    days_since_last_visit: int | None
    last_visit_date: date | None
    visits_this_month: int
    visits_ytd: int
    visits_by_year: dict[int, int]


def discount_for(streak: int) -> int:
    return min(max(streak, 0), DISCOUNT_CAP_VISITS) * DISCOUNT_PCT_PER_VISIT


def compute_streak(
    visit_dates: list[date],
    *,
    initial_streak: int = 0,
    initial_streak_set_at: date | None = None,
    today: date | None = None,
) -> int:
    """Walk visits in chronological order, applying the 30-day reset rule.

    `visit_dates` may be unsorted and may contain duplicates; both are normalised.
    """
    today = today or date.today()
    unique_sorted = sorted(set(visit_dates))

    streak = initial_streak
    last_event = initial_streak_set_at  # day the current streak count was last "valid"

    for d in unique_sorted:
        if last_event is not None and (d - last_event).days > STREAK_RESET_DAYS:
            streak = 0
        streak += 1
        last_event = d

    if last_event is not None and (today - last_event).days > STREAK_RESET_DAYS:
        streak = 0

    return streak


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

    visits_this_month = sum(
        1 for d in unique_sorted if d.year == today.year and d.month == today.month
    )
    visits_ytd = sum(1 for d in unique_sorted if d.year == today.year)

    by_year: dict[int, int] = {}
    for offset in range(history_years):
        y = today.year - offset
        by_year[y] = sum(1 for d in unique_sorted if d.year == y)

    streak = compute_streak(
        unique_sorted,
        initial_streak=initial_streak,
        initial_streak_set_at=initial_streak_set_at,
        today=today,
    )

    days_since = (today - last).days if last is not None else None

    return StreakSummary(
        current_streak=streak,
        discount_pct=discount_for(streak),
        days_since_last_visit=days_since,
        last_visit_date=last if unique_sorted else None,
        visits_this_month=visits_this_month,
        visits_ytd=visits_ytd,
        visits_by_year=by_year,
    )


def previous_year_buckets(today: date, count: int) -> list[int]:
    """Return [today.year, today.year-1, ..., today.year-(count-1)]."""
    return [today.year - i for i in range(count)]
