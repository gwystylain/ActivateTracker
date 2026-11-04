"""How busy it will be, forecast from the booking checkout's own floor count.

Activate sells admission through ROLLER, and every product it sells — the 60-
and 90-minute admissions, the group and holiday bookings — draws on one shared
resource, the venue's floor (81 people at Coquitlam, 91 at Langley). For every
session the checkout states the least capacity the floor has left at any point
in that session's own window, so `capacity - remaining` is how many booked
people share the floor at the busiest moment of a visit starting then, with
every product and its true length already counted. Anyone not yet in the
booking system is invisible, so this is a floor under the real crowd.

Five days out an evening reads as empty because nobody has booked it yet. Each
upcoming reading is therefore topped up by what similar finished sessions
gained from the same distance out to their start (`PickupModel`).
"""
from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

from .config import BookingsConfig, BookingVenue
from .db import transaction

log = logging.getLogger(__name__)

# The visit lengths the checkout sells, and so the windows worth reading.
WINDOWS = (60, 90)
# Lengths it doesn't sell, played as back-to-back sessions of the given size.
# Each reading is already the worst moment of its own window, so the worst
# moment of two hours from s is the worse of the hours from s and s+60 — exact,
# not an estimate: the captures give the same answer split as 90 + 90 from s
# and s+30, at every start at both venues.
JOINED = {120: 60}
# Every visit length the forecast answers for, in payload order.
LENGTHS = WINDOWS + tuple(JOINED)
STEP = timedelta(minutes=15)

# A change-only row says what a session had left at some poll; it only says
# what it had at time X if a poll actually read that day shortly before X.
FRESH = timedelta(hours=12)

# How far back finished sessions are read to learn late-booking pickup from.
HISTORY_DAYS = 56

# A pickup estimate must rest on finished sessions from at least this many
# dates, so one unusual evening can't stand in for "a Tuesday".
MIN_ANALOG_DATES = 2

_START_FMT = "%Y-%m-%dT%H:%M"


@dataclass(frozen=True)
class FloorReading:
    """The floor's least capacity left during one visit window, as one read reported it."""

    start: datetime     # naive, the venue's wall clock
    minutes: int        # the window: a visit of this length starting at `start`
    remaining: int
    capacity: int


# ---------- persistence ----------

def record_day(
    conn: sqlite3.Connection,
    venue: str,
    day: date,
    readings: Iterable[FloorReading],
    *,
    observed_at: datetime,
) -> int:
    """Store one successful read of (venue, day). Returns rows written.

    A window's row is written only when its reading moved since the last one,
    which keeps a year of hourly polls down to the bookings that actually
    happened. The read itself always lands in `booking_polls`, so "nothing
    changed" stays distinguishable from "nobody looked".
    """
    stamp = _utc_stamp(observed_at)
    lo = datetime.combine(day, time()).strftime(_START_FMT)
    hi = datetime.combine(day + timedelta(days=1), time()).strftime(_START_FMT)

    current: dict[tuple[str, int], tuple[int, int]] = {}
    for r in readings:
        if r.start.date() != day:
            log.warning("floor reading %s listed under %s; ignored", r.start, day)
            continue
        current[(r.start.strftime(_START_FMT), r.minutes)] = (r.remaining, r.capacity)

    latest = {
        (row["session_start"], row["minutes"]): (row["remaining"], row["capacity"])
        for row in conn.execute(
            """
            SELECT b.session_start, b.minutes, b.remaining, b.capacity
            FROM booking_floor b
            WHERE b.venue = ? AND b.session_start >= ? AND b.session_start < ?
              AND b.observed_at = (
                  SELECT MAX(observed_at) FROM booking_floor
                  WHERE venue = b.venue AND session_start = b.session_start
                    AND minutes = b.minutes)
            """,
            (venue, lo, hi),
        )
    }
    rows = [
        (venue, start, minutes, stamp, remaining, capacity)
        for (start, minutes), (remaining, capacity) in sorted(current.items())
        if latest.get((start, minutes)) != (remaining, capacity)
    ]
    with transaction(conn):
        conn.executemany(
            "INSERT OR REPLACE INTO booking_floor"
            " (venue, session_start, minutes, observed_at, remaining, capacity)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "INSERT OR IGNORE INTO booking_polls (venue, service_date, polled_at)"
            " VALUES (?, ?, ?)",
            (venue, day.isoformat(), stamp),
        )
    return len(rows)


# ---------- polling ----------

FetchDay = Callable[[BookingVenue, date], Awaitable[list[FloorReading]]]

_poll_lock = asyncio.Lock()


async def poll_bookings(
    conn: sqlite3.Connection,
    cfg: BookingsConfig,
    *,
    fetch_day: FetchDay | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, int]:
    """Read today and the next `days_ahead` days at every configured venue.

    A day that fails to fetch is skipped and counted, never recorded: its
    absence from `booking_polls` is what keeps it out of the forecast's
    history. Serialised on its own lock — this never touches playactivate.com,
    so there is no reason to queue behind a score poll.
    """
    if _poll_lock.locked():
        log.info("poll_bookings skipped: another booking poll is in progress")
        return {"skipped": 1}

    clock = clock or (lambda: datetime.now(timezone.utc))
    async with _poll_lock:
        if fetch_day is not None:
            return await _poll(conn, cfg, fetch_day, clock)

        from curl_cffi.requests import AsyncSession

        from . import roller

        async with AsyncSession() as session:
            async def fetch(venue: BookingVenue, day: date) -> list[FloorReading]:
                return await roller.fetch_day(venue, day, cfg=cfg, session=session)

            return await _poll(conn, cfg, fetch, clock)


async def _poll(
    conn: sqlite3.Connection,
    cfg: BookingsConfig,
    fetch_day: FetchDay,
    clock: Callable[[], datetime],
) -> dict[str, int]:
    counters = {"days": 0, "rows": 0, "errors": 0}
    tz = ZoneInfo(cfg.timezone)
    today = clock().astimezone(tz).date()
    lo, hi = cfg.spacing_seconds
    first = True
    for venue in cfg.venues:
        for offset in range(cfg.days_ahead + 1):
            day = today + timedelta(days=offset)
            if not first:
                await asyncio.sleep(random.uniform(lo, hi))
            first = False
            try:
                readings = await fetch_day(venue, day)
            except Exception as e:  # one bad day never stops the rest
                counters["errors"] += 1
                log.warning("booking fetch failed venue=%s day=%s err=%s", venue.slug, day, e)
                continue
            counters["rows"] += record_day(conn, venue.slug, day, readings, observed_at=clock())
            counters["days"] += 1
    log.info("poll_bookings done: %s", counters)
    return counters


# ---------- history ----------

@dataclass
class Timeline:
    """Everything recorded about one (start, window), oldest first."""

    start: datetime          # naive, venue wall clock
    minutes: int
    start_utc: datetime
    times: list[datetime] = field(default_factory=list)
    booked: list[int] = field(default_factory=list)      # capacity - remaining, >= 0
    capacity: list[int] = field(default_factory=list)

    def booked_as_of(self, when: datetime) -> int | None:
        """Booked at the window's busiest moment as of `when`; None before it was first seen."""
        i = bisect_right(self.times, when)
        return self.booked[i - 1] if i else None


@dataclass
class History:
    timelines: list[Timeline]
    # service_date -> UTC times that day was read, ascending.
    polls: dict[date, list[datetime]]

    def read_near(self, day: date, when: datetime) -> bool:
        """Was `day` read within FRESH before `when`?"""
        times = self.polls.get(day, [])
        i = bisect_right(times, when)
        return bool(i) and times[i - 1] >= when - FRESH

    def last_read(self, day: date) -> datetime | None:
        times = self.polls.get(day)
        return times[-1] if times else None

    def booked_at(self, t: Timeline, when: datetime) -> int | None:
        """Booked into `t`'s window as of `when`, or None if nobody was looking."""
        if not self.read_near(t.start.date(), when):
            return None
        return t.booked_as_of(when)


def load_history(
    conn: sqlite3.Connection, venue: str, *, since: date, tz: ZoneInfo
) -> History:
    lo = datetime.combine(since, time()).strftime(_START_FMT)
    by_key: dict[tuple[str, int], Timeline] = {}
    for r in conn.execute(
        """
        SELECT session_start, minutes, observed_at, remaining, capacity FROM booking_floor
        WHERE venue = ? AND session_start >= ?
        ORDER BY session_start, minutes, observed_at
        """,
        (venue, lo),
    ):
        key = (r["session_start"], r["minutes"])
        t = by_key.get(key)
        if t is None:
            start = datetime.strptime(r["session_start"], _START_FMT)
            t = by_key[key] = Timeline(
                start=start, minutes=r["minutes"], start_utc=_to_utc(start, tz)
            )
        t.times.append(_parse_utc(r["observed_at"]))
        t.booked.append(max(0, r["capacity"] - r["remaining"]))
        t.capacity.append(r["capacity"])

    polls: dict[date, list[datetime]] = defaultdict(list)
    for r in conn.execute(
        """
        SELECT service_date, polled_at FROM booking_polls
        WHERE venue = ? AND service_date >= ?
        ORDER BY service_date, polled_at
        """,
        (venue, since.isoformat()),
    ):
        polls[date.fromisoformat(r["service_date"])].append(_parse_utc(r["polled_at"]))
    return History(timelines=list(by_key.values()), polls=dict(polls))


# ---------- late-booking pickup ----------

def _day_type(weekday: int) -> str:
    return "mon-thu" if weekday <= 3 else ("fri", "sat", "sun")[weekday - 4]


# Most specific first. Each level is used only when it has enough evidence, so
# a new deployment starts on "any evening at 6" and earns "a Tuesday at 6".
_LEVELS: tuple[Callable[[Timeline], tuple], ...] = (
    lambda t: (t.minutes, t.start.weekday(), t.start.hour),
    lambda t: (t.minutes, _day_type(t.start.weekday()), t.start.hour),
    lambda t: (t.minutes, t.start.hour),
)


@dataclass(frozen=True)
class Analog:
    timeline: Timeline
    final: int       # booked as of its start


class PickupModel:
    """How many more bookings a window gains between some lead time and its start.

    Learned from finished sessions alone: for each, `final` is what it had
    booked when it started, and its value `lead` before the start is read off
    the same change-only history. The estimate for an upcoming one is the mean
    gain over finished ones like it — same window length, weekday and hour when
    there are enough of those, coarser otherwise. Sessions that gained nothing
    count too, as zeroes, or every estimate would be the gain of the busy ones.
    """

    def __init__(self, history: History, *, now: datetime):
        self.history = history
        self.index: list[dict[tuple, list[Analog]]] = [defaultdict(list) for _ in _LEVELS]
        dates: set[date] = set()
        for t in history.timelines:
            if t.start_utc > now:
                continue
            final = history.booked_at(t, t.start_utc)
            if final is None:
                continue
            dates.add(t.start.date())
            a = Analog(t, final)
            for level, key in zip(self.index, _LEVELS):
                level[key(t)].append(a)
        self.learned_dates = len(dates)

    def pickup(self, target: Timeline, lead: timedelta) -> float | None:
        if lead <= timedelta(0):
            return 0.0
        for level, key in zip(self.index, _LEVELS):
            gains: list[int] = []
            dates: set[date] = set()
            for a in level.get(key(target), ()):
                then = self.history.booked_at(a.timeline, a.timeline.start_utc - lead)
                if then is None:
                    continue
                gains.append(a.final - then)
                dates.add(a.timeline.start.date())
            if len(dates) >= MIN_ANALOG_DATES:
                return fmean(gains)
        return None


# ---------- the payload ----------

def forecast(
    conn: sqlite3.Connection,
    venue: str,
    *,
    now: datetime,
    tz: ZoneInfo,
    days: int = 7,
) -> dict[str, Any]:
    """Per day, every 15-minute arrival's busiest booked moment, now and projected.

    `steps[i].now[j]` / `.exp[j]` is for a visit of `LENGTHS[j]` minutes
    starting at `steps[i].t`, None where no such visit fits (a 90-minute
    session can't start half an hour before closing). A joined length's
    projection is its busier part's projection. The page picks the step and
    length the visitor chose, so changing either never refetches.
    """
    today = now.astimezone(tz).date()
    history = load_history(conn, venue, since=today - timedelta(days=HISTORY_DAYS), tz=tz)
    model = PickupModel(history, now=now)

    upcoming: dict[date, list[Timeline]] = defaultdict(list)
    for t in history.timelines:
        if today <= t.start.date() < today + timedelta(days=days):
            upcoming[t.start.date()].append(t)

    capacity: int | None = None
    newest: datetime | None = None
    out_days: list[dict[str, Any]] = []
    for day in sorted(upcoming):
        last_read = history.last_read(day)
        if last_read is None:
            continue
        now_n: dict[tuple[datetime, int], int] = {}
        exp_n: dict[tuple[datetime, int], float] = {}
        projected = True
        for t in upcoming[day]:
            booked = t.booked[-1]
            cap = t.capacity[-1]
            if newest is None or t.times[-1] > newest:
                newest, capacity = t.times[-1], cap
            # Measured from when the number was read, not from the request:
            # a 3am page view of a 10pm read is still a 10pm number.
            gain = model.pickup(t, t.start_utc - last_read)
            if gain is None:
                gain = 0.0
                if booked < cap:
                    projected = False
            now_n[(t.start, t.minutes)] = booked
            # The checkout overbooks (a start slot reading -7 left), so the cap
            # on a projection is the floor or what's already booked, whichever
            # is more.
            exp_n[(t.start, t.minutes)] = min(float(max(cap, booked)), max(0.0, booked + gain))

        starts = sorted({t.start for t in upcoming[day]})
        steps = []
        at = starts[0]
        while at <= starts[-1]:
            steps.append({
                "t": at.strftime("%H:%M"),
                "now": [_visit(now_n, at, w) for w in LENGTHS],
                "exp": [_round(_visit(exp_n, at, w)) for w in LENGTHS],
            })
            at += STEP
        out_days.append({
            "date": day.isoformat(),
            "weekday": day.weekday(),
            "read_at": _utc_stamp(last_read),
            "projected": projected,
            "steps": steps,
        })

    polls = [p for times in history.polls.values() for p in times]
    as_of = max(polls) if polls else None
    return {
        "venue": venue,
        "now_local": now.astimezone(tz).strftime(_START_FMT),
        "as_of": _utc_stamp(as_of) if as_of else None,
        "as_of_local": as_of.astimezone(tz).strftime(_START_FMT) if as_of else None,
        "windows": list(LENGTHS),
        "capacity": capacity,
        "learned_dates": model.learned_dates,
        "days": out_days,
    }


# ---------- helpers ----------

def _visit(by_window: dict[tuple[datetime, int], Any], at: datetime, minutes: int) -> Any:
    """The reading for a visit of `minutes` from `at`: a sold window's own, or
    the busiest of the back-to-back windows a joined length is played as."""
    if minutes in WINDOWS:
        return by_window.get((at, minutes))
    part = JOINED[minutes]
    pieces = [by_window.get((at + timedelta(minutes=k), part)) for k in range(0, minutes, part)]
    return None if None in pieces else max(pieces)


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _to_utc(local: datetime, tz: ZoneInfo) -> datetime:
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def _utc_stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(stamp: str) -> datetime:
    when = datetime.fromisoformat(stamp)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
