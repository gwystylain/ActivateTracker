"""The ROLLER parser, against real product-availability responses.

tests/fixtures/roller/ holds `/api/checkout/main/product-availability?date=2026-11-04`
for both venues, captured in the same minute and gzipped as received (each is
~240 KB of JSON otherwise). The numbers pinned below are the numbers *in the
capture* — they move as people book — not properties of the venue, except
the capacities and the window rule, which are.
"""
import gzip
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import busy, roller
from app import db as db_mod
from app.config import BookingsConfig, BookingVenue

FIXTURES = Path(__file__).parent / "fixtures" / "roller"
DAY = date(2026, 11, 4)


def _load(venue: str):
    with gzip.open(FIXTURES / f"{venue}_product_availability_2026-11-04.json.gz") as f:
        return json.load(f)


@pytest.mark.parametrize(
    "venue, floor",
    [("activatecoquitlam", (62623, 81)), ("activatelangley", (74065, 91))],
)
def test_the_floor_is_the_location_every_product_shares(venue, floor):
    assert roller.floor_location(_load(venue)) == floor


@pytest.mark.parametrize("venue", ["activatecoquitlam", "activatelangley"])
def test_every_start_gets_a_reading_per_visit_length(venue):
    readings = roller.parse_floor(_load(venue), DAY)
    by_len = {m: sorted(r.start for r in readings if r.minutes == m) for m in (60, 90)}
    # Starts every 15 minutes from 11:00; the 60s run to 22:00, the 90s stop
    # half an hour earlier, so nobody's session runs past closing.
    assert by_len[60][0] == by_len[90][0] == datetime(2026, 11, 4, 11, 0)
    assert by_len[60][-1] == datetime(2026, 11, 4, 22, 0)
    assert by_len[90][-1] == datetime(2026, 11, 4, 21, 30)
    assert len(by_len[60]) == 45 and len(by_len[90]) == 43
    assert all(b - a == timedelta(minutes=15) for a, b in zip(by_len[60], by_len[60][1:]))


@pytest.mark.parametrize("venue", ["activatecoquitlam", "activatelangley"])
def test_a_ninety_minute_window_is_the_lesser_of_its_two_hours(venue):
    # The floor reading is the least left anywhere in the window — which is
    # what makes it the busiest moment of the visit, not an average. Checked
    # on every 90-minute start the capture has.
    readings = {(r.start, r.minutes): r.remaining for r in roller.parse_floor(_load(venue), DAY)}
    for (start, minutes), left in readings.items():
        if minutes != 90:
            continue
        assert left == min(readings[(start, 60)], readings[(start + timedelta(minutes=30), 60)])


@pytest.mark.parametrize("venue", ["activatecoquitlam", "activatelangley"])
def test_a_two_hour_visit_reads_the_same_however_it_is_split(tmp_path, venue):
    # Nobody sells 120 minutes, so the forecast joins two back-to-back hours.
    # Splitting it as 90 + 90 from the start and half an hour later must give
    # the same busiest moment, at every start where two hours fit.
    readings = roller.parse_floor(_load(venue), DAY)
    booked = {(r.start, r.minutes): r.capacity - r.remaining for r in readings}
    conn = db_mod.connect(tmp_path / "t.db")
    db_mod.init_schema(conn)
    read_at = datetime(2026, 11, 4, 8, tzinfo=ZoneInfo("America/Vancouver"))
    busy.record_day(conn, "v", DAY, readings, observed_at=read_at)

    payload = busy.forecast(conn, "v", now=read_at, tz=read_at.tzinfo)

    i = payload["windows"].index(120)
    (day,) = payload["days"]
    joined = {}
    for step in day["steps"]:
        start = datetime.combine(DAY, datetime.strptime(step["t"], "%H:%M").time())
        if step["now"][i] is not None:
            joined[start] = step["now"][i]
    later = timedelta(minutes=30)
    assert joined == {
        s: max(n, booked[(s + later, 90)])
        for (s, m), n in booked.items() if m == 90 and (s + later, 90) in booked
    }
    assert len(joined) == 41   # 11:00 to 21:00: the last start with two hours before 23:00


def test_coquitlam_reads_what_its_start_slots_add_up_to():
    # In the capture, 25 people start at 1:00 (the widget's "Sold out": -7 of
    # 18 left) and 5 at 1:30 (13 left), and nobody else is in that window.
    # The floor says the same: 81 - 51 = 30.
    readings = {(r.start, r.minutes): r for r in roller.parse_floor(_load("activatecoquitlam"), DAY)}
    r = readings[(datetime(2026, 11, 4, 13, 0), 90)]
    assert (r.remaining, r.capacity) == (51, 81)


def test_a_closed_day_reads_as_no_sessions():
    payload = [p for p in _load("activatecoquitlam") if not p.get("sessions")]
    assert roller.parse_floor(payload, DAY) == []


def test_anything_but_a_list_is_refused():
    with pytest.raises(roller.RollerError):
        roller.parse_floor({"message": "Unauthorized"}, DAY)


def test_two_shared_locations_are_refused_rather_than_guessed():
    product = {
        "products": [{"locations": [{"id": 1, "capacity": 80}, {"id": 2, "capacity": 20}]}],
        "sessions": [{"date": "2026-11-04", "startTime": "18:00", "allocations": []}],
    }
    with pytest.raises(roller.RollerError):
        roller.floor_location([product, product])


class _Session:
    def __init__(self, status, payload):
        self.status, self.payload, self.calls = status, payload, []

    async def get(self, url, **kw):
        self.calls.append((url, kw))
        return SimpleNamespace(status_code=self.status, json=lambda: self.payload)


async def test_fetch_day_asks_as_the_checkout_does():
    session = _Session(200, _load("activatelangley"))
    venue = BookingVenue(slug="langley", name="Langley", roller_venue="activatelangley")

    readings = await roller.fetch_day(venue, DAY, cfg=BookingsConfig(), session=session)

    ((url, kw),) = session.calls
    assert url == "https://api.roller.app/api/checkout/main/product-availability"
    assert kw["params"] == {"date": "2026-11-04"}
    assert kw["headers"]["X-Api-Key"] == "activatelangley"
    assert kw["headers"]["X-Checkout-Slug"] == "main"
    assert {r.capacity for r in readings} == {91}


async def test_fetch_day_raises_on_a_refusal():
    venue = BookingVenue(slug="langley", name="Langley", roller_venue="activatelangley")
    with pytest.raises(roller.RollerError):
        await roller.fetch_day(venue, DAY, cfg=BookingsConfig(), session=_Session(401, None))
