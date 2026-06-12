from datetime import date, timedelta

from app.streak import (
    DISCOUNT_WINDOW_DAYS,
    discount_for,
    summarize,
    visits_in_window,
)


def test_discount_tiers():
    assert discount_for(0) == 0
    assert discount_for(1) == 10
    assert discount_for(2) == 15
    assert discount_for(3) == 20
    assert discount_for(4) == 25
    assert discount_for(9) == 25   # caps at 25%
    assert discount_for(-3) == 0


def test_no_visits_no_discount():
    s = summarize([], today=date(2026, 4, 28))
    assert s.visits_last_30_days == 0
    assert s.discount_pct == 0


def test_visits_in_window_counts_trailing_30_days():
    today = date(2026, 4, 30)
    visits = [
        date(2026, 4, 30),               # today
        date(2026, 4, 15),               # within window
        today - timedelta(days=30),      # exactly 30 days ago -> included
        today - timedelta(days=31),      # just outside the window
        date(2026, 4, 1),                # within window
    ]
    assert visits_in_window(visits, today=today) == 4


def test_duplicate_dates_dedupe():
    d = date(2026, 4, 10)
    assert visits_in_window([d, d, d], today=date(2026, 4, 11)) == 1


def test_initial_streak_counts_only_while_inside_window():
    base = date(2026, 1, 1)
    # Baseline set today -> contributes.
    assert (
        visits_in_window(
            [], initial_streak=3, initial_streak_set_at=base, today=base
        )
        == 3
    )
    # 31 days later the baseline falls out of the window.
    assert (
        visits_in_window(
            [],
            initial_streak=3,
            initial_streak_set_at=base,
            today=base + timedelta(days=DISCOUNT_WINDOW_DAYS + 1),
        )
        == 0
    )


def test_initial_streak_adds_to_real_visits():
    base = date(2026, 1, 1)
    visits = [base + timedelta(days=10), base + timedelta(days=20)]
    n = visits_in_window(
        visits,
        initial_streak=2,
        initial_streak_set_at=base,
        today=base + timedelta(days=21),
    )
    assert n == 4  # 2 (baseline, still in window) + 2 visits


def test_summarize_counts_buckets():
    today = date(2026, 4, 28)
    visits = [
        date(2024, 5, 1),
        date(2025, 12, 31),
        date(2026, 1, 5),
        date(2026, 4, 1),
        date(2026, 4, 27),
        date(2026, 4, 28),
    ]
    s = summarize(visits, today=today, history_years=4)

    assert s.visits_ytd == 4          # 2026: 1/5, 4/1, 4/27, 4/28
    assert s.visits_by_year[2026] == 4
    assert s.visits_by_year[2025] == 1
    assert s.visits_by_year[2024] == 1
    assert s.visits_by_year[2023] == 0
    assert s.last_visit_date == today
    assert s.days_since_last_visit == 0
    # Visits within the trailing 30 days: 4/1, 4/27, 4/28 (Jan 5 is too old).
    assert s.visits_last_30_days == 3
    assert s.discount_pct == 20
