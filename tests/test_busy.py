"""The /busy forecast. Every reading here is synthetic, built for the test; the
parser that turns a real checkout response into FloorReadings is tested in
test_roller.py, against captured responses."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import busy
from app import db as db_mod
from app.busy import FloorReading
from app.config import BookingsConfig, BookingVenue
from app.routes.public import busy_forecast

TZ = ZoneInfo("America/Vancouver")
CAP = 81


def _conn(tmp_path):
    conn = db_mod.connect(tmp_path / "t.db")
    db_mod.init_schema(conn)
    return conn


def _local(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm)


def _utc(local: datetime) -> datetime:
    return local.replace(tzinfo=TZ).astimezone(timezone.utc)


def _read(conn, start: datetime, booked: int, *, at: datetime, minutes=60, capacity=CAP):
    """One poll of `start`'s day, at venue-local time `at`, seeing one window."""
    busy.record_day(
        conn, "coquitlam", start.date(),
        [FloorReading(start, minutes, capacity - booked, capacity)],
        observed_at=_utc(at),
    )


def _day(payload, iso):
    (day,) = [d for d in payload["days"] if d["date"] == iso]
    return day


# ---------- persistence ----------

def test_record_day_writes_a_row_only_when_the_reading_moves(tmp_path):
    conn = _conn(tmp_path)
    start = _local(2026, 11, 10, 18)

    def read(remaining, capacity, hour):
        return busy.record_day(
            conn, "coquitlam", start.date(),
            [FloorReading(start, 60, remaining, capacity)],
            observed_at=_utc(_local(2026, 11, 9, hour)),
        )

    assert read(81, 81, 9) == 1
    assert read(81, 81, 10) == 0
    assert read(74, 81, 11) == 1
    assert read(74, 90, 12) == 1   # the venue changed its floor

    # Every read lands in booking_polls, changed or not: that's what tells a
    # quiet stretch from an unobserved one.
    assert conn.execute("SELECT COUNT(*) FROM booking_polls").fetchone()[0] == 4


def test_record_day_keeps_each_window_apart(tmp_path):
    conn = _conn(tmp_path)
    start = _local(2026, 11, 10, 18)
    busy.record_day(
        conn, "coquitlam", start.date(),
        [FloorReading(start, 60, 70, CAP), FloorReading(start, 90, 60, CAP)],
        observed_at=_utc(_local(2026, 11, 9, 9)),
    )
    rows = conn.execute("SELECT minutes, remaining FROM booking_floor ORDER BY minutes")
    assert [tuple(r) for r in rows] == [(60, 70), (90, 60)]


# ---------- reading history ----------

def test_a_reading_counts_only_if_its_day_was_read_shortly_before(tmp_path):
    conn = _conn(tmp_path)
    start = _local(2026, 11, 10, 18)
    _read(conn, start, 8, at=_local(2026, 11, 8, 9))
    h = busy.load_history(conn, "coquitlam", since=date(2026, 11, 1), tz=TZ)
    (t,) = h.timelines

    assert h.booked_at(t, _utc(_local(2026, 11, 8, 8))) is None    # before the first read
    assert h.booked_at(t, _utc(_local(2026, 11, 8, 20))) == 8
    assert h.booked_at(t, _utc(_local(2026, 11, 9, 9))) is None    # 24h stale


def test_an_overbooked_floor_reads_as_more_than_full(tmp_path):
    # The checkout lets a start go negative; that's people, not an error.
    conn = _conn(tmp_path)
    start = _local(2026, 11, 10, 18)
    _read(conn, start, CAP + 4, at=_local(2026, 11, 10, 17))
    h = busy.load_history(conn, "coquitlam", since=date(2026, 11, 1), tz=TZ)
    assert h.timelines[0].booked == [CAP + 4]


# ---------- the forecast ----------

def _seed_two_past_tuesdays(conn):
    """Two finished Tuesday 6pm hours, each empty five days out and with eight
    booked on the floor by an hour before."""
    for start in (_local(2026, 11, 3, 18), _local(2026, 11, 10, 18)):
        _read(conn, start, 0, at=start - timedelta(days=5))
        _read(conn, start, 8, at=start - timedelta(hours=1))


def test_forecast_tops_up_a_far_off_session_with_late_bookings(tmp_path):
    conn = _conn(tmp_path)
    _seed_two_past_tuesdays(conn)
    target = _local(2026, 11, 17, 18)
    read_at = target - timedelta(days=5)
    _read(conn, target, 2, at=read_at)

    payload = busy.forecast(conn, "coquitlam", now=_utc(read_at + timedelta(minutes=1)), tz=TZ)

    day = _day(payload, "2026-11-17")
    assert day["projected"] is True
    (step,) = day["steps"]
    assert step["t"] == "18:00"
    # Two booked now, plus the eight its two predecessors gained from here. No
    # 90-minute window was read, so that slot is empty rather than zero.
    assert step["now"] == [2, None]
    assert step["exp"] == [10.0, None]
    assert payload["windows"] == [60, 90]
    assert payload["capacity"] == CAP
    assert payload["learned_dates"] == 2


def test_forecast_without_enough_history_shows_bookings_alone(tmp_path):
    conn = _conn(tmp_path)
    # Only one finished Tuesday: one evening can't stand for "a Tuesday".
    start = _local(2026, 11, 10, 18)
    _read(conn, start, 0, at=start - timedelta(days=5))
    _read(conn, start, 8, at=start - timedelta(hours=1))
    target = _local(2026, 11, 17, 18)
    read_at = target - timedelta(days=5)
    _read(conn, target, 2, at=read_at)

    payload = busy.forecast(conn, "coquitlam", now=_utc(read_at), tz=TZ)

    day = _day(payload, "2026-11-17")
    assert day["projected"] is False
    assert day["steps"][0]["exp"][0] == day["steps"][0]["now"][0] == 2


def test_forecast_measures_lead_from_the_read_not_the_request(tmp_path):
    # A page viewed two days after the last read still describes that read: the
    # bookings shown are five days out from the session, not three.
    conn = _conn(tmp_path)
    _seed_two_past_tuesdays(conn)
    target = _local(2026, 11, 17, 18)
    read_at = target - timedelta(days=5)
    _read(conn, target, 2, at=read_at)

    payload = busy.forecast(conn, "coquitlam", now=_utc(read_at + timedelta(days=2)), tz=TZ)

    assert _day(payload, "2026-11-17")["steps"][0]["exp"][0] == 10.0


def test_a_projection_never_runs_past_the_floor(tmp_path):
    conn = _conn(tmp_path)
    _seed_two_past_tuesdays(conn)
    target = _local(2026, 11, 17, 18)
    read_at = target - timedelta(days=5)
    _read(conn, target, CAP - 3, at=read_at)

    payload = busy.forecast(conn, "coquitlam", now=_utc(read_at), tz=TZ)

    assert _day(payload, "2026-11-17")["steps"][0]["exp"][0] == CAP


def test_forecast_steps_cover_every_quarter_hour_between_first_and_last_start(tmp_path):
    conn = _conn(tmp_path)
    at = _local(2026, 11, 12, 9)
    _read(conn, _local(2026, 11, 17, 17), 3, at=at)
    _read(conn, _local(2026, 11, 17, 18), 5, at=at)

    payload = busy.forecast(conn, "coquitlam", now=_utc(at), tz=TZ)

    steps = _day(payload, "2026-11-17")["steps"]
    assert [s["t"] for s in steps] == ["17:00", "17:15", "17:30", "17:45", "18:00"]
    # A quarter hour the checkout listed nothing for stays empty.
    assert steps[1]["now"] == [None, None]


def test_forecast_reports_reads_in_venue_time(tmp_path):
    conn = _conn(tmp_path)
    _read(conn, _local(2026, 11, 17, 18), 2, at=_local(2026, 11, 12, 14, 5))

    payload = busy.forecast(conn, "coquitlam", now=_utc(_local(2026, 11, 12, 15)), tz=TZ)

    assert payload["as_of_local"] == "2026-11-12T14:05"
    assert payload["now_local"] == "2026-11-12T15:00"


# ---------- polling ----------

async def test_poll_records_what_landed_and_counts_what_failed(tmp_path):
    conn = _conn(tmp_path)
    cfg = BookingsConfig(
        enabled=True, days_ahead=1, spacing_seconds=(0.0, 0.0),
        venues=[BookingVenue(slug="coquitlam", name="Coquitlam", roller_venue="x")],
    )
    now = _utc(_local(2026, 11, 10, 12))

    async def fetch_day(venue, day):
        if day == date(2026, 11, 11):
            raise RuntimeError("upstream down")
        return [FloorReading(datetime.combine(day, datetime.min.time()).replace(hour=18), 60, 70, CAP)]

    counters = await busy.poll_bookings(conn, cfg, fetch_day=fetch_day, clock=lambda: now)

    assert counters == {"days": 1, "rows": 1, "errors": 1}
    # The failed day leaves no poll row, which keeps it out of the history.
    days = [r[0] for r in conn.execute("SELECT service_date FROM booking_polls")]
    assert days == ["2026-11-10"]


# ---------- route and config ----------

async def test_forecast_route_refuses_an_unknown_venue(tmp_path):
    conn = _conn(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db=conn, config=SimpleNamespace(bookings=BookingsConfig()),
    )))
    resp = await busy_forecast(request, venue="burnaby")
    assert resp.status_code == 404

    resp = await busy_forecast(request, venue="langley")
    assert json.loads(resp.body)["days"] == []


def test_bookings_are_off_unless_turned_on():
    cfg = BookingsConfig()
    assert cfg.enabled is False
    assert [v.roller_venue for v in cfg.venues] == ["activatecoquitlam", "activatelangley"]


def test_bookings_timezone_must_exist():
    with pytest.raises(ValueError):
        BookingsConfig(timezone="Mars/Olympus_Mons")
