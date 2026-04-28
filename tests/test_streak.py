from datetime import date, timedelta

from app.streak import (
    DISCOUNT_CAP_VISITS,
    STREAK_RESET_DAYS,
    compute_streak,
    discount_for,
    summarize,
)


def test_discount_caps_at_25_percent():
    assert discount_for(0) == 0
    assert discount_for(1) == 5
    assert discount_for(5) == 25
    assert discount_for(50) == 25
    assert discount_for(-3) == 0


def test_no_visits_no_streak():
    assert compute_streak([], today=date(2026, 4, 28)) == 0


def test_each_visit_adds_one():
    visits = [date(2026, 4, 1), date(2026, 4, 5), date(2026, 4, 10)]
    assert compute_streak(visits, today=date(2026, 4, 11)) == 3


def test_duplicate_dates_dedupe():
    d = date(2026, 4, 10)
    assert compute_streak([d, d, d], today=date(2026, 4, 11)) == 1


def test_visit_at_day_30_keeps_streak():
    base = date(2026, 1, 1)
    visits = [base, base + timedelta(days=STREAK_RESET_DAYS)]  # gap of exactly 30 days
    assert compute_streak(visits, today=base + timedelta(days=STREAK_RESET_DAYS)) == 2


def test_visit_at_day_31_resets_streak():
    base = date(2026, 1, 1)
    visits = [base, base + timedelta(days=STREAK_RESET_DAYS + 1)]  # gap of 31 days
    assert compute_streak(visits, today=base + timedelta(days=STREAK_RESET_DAYS + 1)) == 1


def test_streak_resets_when_today_is_more_than_30_days_after_last_visit():
    visits = [date(2026, 1, 1), date(2026, 1, 10)]
    today = date(2026, 3, 1)  # > 30 days after Jan 10
    assert compute_streak(visits, today=today) == 0


def test_initial_streak_seeds_count_and_resets_if_stale():
    base = date(2026, 1, 1)
    # Admin says "they have 3 visits banked, set today" — no actual visits in DB.
    assert compute_streak(
        [],
        initial_streak=3,
        initial_streak_set_at=base,
        today=base + timedelta(days=10),
    ) == 3
    # 31 days later with no further visits → reset.
    assert compute_streak(
        [],
        initial_streak=3,
        initial_streak_set_at=base,
        today=base + timedelta(days=STREAK_RESET_DAYS + 1),
    ) == 0


def test_initial_streak_carries_through_real_visits():
    base = date(2026, 1, 1)
    visits = [base + timedelta(days=10), base + timedelta(days=20)]
    s = compute_streak(
        visits,
        initial_streak=2,
        initial_streak_set_at=base,
        today=base + timedelta(days=21),
    )
    assert s == 4  # 2 (initial) + 2 visits


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

    assert s.visits_this_month == 3   # April: 1, 27, 28
    assert s.visits_ytd == 4          # 2026: 1/5, 4/1, 4/27, 4/28
    assert s.visits_by_year[2026] == 4
    assert s.visits_by_year[2025] == 1
    assert s.visits_by_year[2024] == 1
    assert s.visits_by_year[2023] == 0
    assert s.last_visit_date == today
    assert s.days_since_last_visit == 0
    # Continuous chain Jan 5 → Apr 1 (gap = 86 days) → reset to 1, then +27, +28
    assert s.current_streak == 3
    assert s.discount_pct == 15
