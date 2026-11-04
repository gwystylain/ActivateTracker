"""ROLLER, Activate's booking system: how full the floor is for every session.

The site's Book Now button opens ROLLER's hosted checkout in an iframe,
`ecom.roller.app/<venue>/main` (`activatecoquitlam`, `activatelangley`), and
that page fills itself from one request per day:

    GET https://api.roller.app/api/checkout/main/product-availability?date=YYYY-MM-DD
    X-Api-Key: <venue>          the venue's name, not a secret
    X-Checkout-Slug: main

It answers with every product the venue sells — admissions, group and holiday
bookings, gift cards, memberships — ~240 KB of JSON, Brotli'd to a fraction of
that on the wire. There is no per-product filter (`productId` is ignored).

Each session's allocation names the "locations" (ROLLER's word for a capacity
resource) the product draws on, and `resourceTimes` gives what each has left:

- a start slot, 15 minutes wide — 18 per start for admission at Coquitlam,
  20 at Langley, 30 for groups. This is the widget's "N left", and it goes
  negative when a start is overbooked ("-7" reads as Sold out).
- the floor, which *every* product shares — 81 at Coquitlam, 91 at Langley —
  reported as the least it has left anywhere in the session's own window
  (`endMinutes` 60 or 90). That is the number worth having: how many booked
  people are on the floor at the busiest moment of that visit, with every
  product and its true length already counted. A 90-minute window is exactly
  the lesser of the 60-minute windows starting with it and half an hour later,
  which the fixtures confirm.

So the parser finds the floor as the one location every product with sessions
draws on, and reads its window for every session.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
from typing import Any

from .busy import WINDOWS, FloorReading
from .config import BookingsConfig, BookingVenue


class RollerError(Exception):
    pass


async def fetch_day(
    venue: BookingVenue,
    day: date,
    *,
    cfg: BookingsConfig,
    session: Any,
    timeout: float = 20.0,
) -> list[FloorReading]:
    url = f"{cfg.api_base}/checkout/{cfg.checkout}/product-availability"
    resp = await session.get(
        url,
        params={"date": day.isoformat()},
        headers={
            "X-Api-Key": venue.roller_venue,
            "X-Checkout-Slug": cfg.checkout,
            "Accept": "application/json",
            # ~240 KB of JSON a day uncompressed; gzipped, about 7 KB.
            "Accept-Encoding": "gzip, br",
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RollerError(f"HTTP {resp.status_code} for {venue.roller_venue} {day}")
    try:
        payload = resp.json()
    except ValueError as e:
        raise RollerError(f"non-JSON answer for {venue.roller_venue} {day}") from e
    return parse_floor(payload, day)


def floor_location(products: list[dict[str, Any]]) -> tuple[int, int | None] | None:
    """(id, capacity) of the location every product with sessions draws on.

    None when no product has sessions (a closed day). Several shared locations
    would be a layout this was never shown, so that raises rather than guess.
    """
    scheduled = [p for p in products if p.get("sessions")]
    if not scheduled:
        return None
    uses: Counter[int] = Counter()
    capacity: dict[int, int | None] = {}
    for p in scheduled:
        ids: set[int] = set()
        for child in p.get("products") or []:
            for loc in child.get("locations") or []:
                ids.add(loc["id"])
                capacity[loc["id"]] = loc.get("capacity")
        uses.update(ids)
    shared = [i for i, n in uses.items() if n == len(scheduled)]
    if len(shared) != 1:
        raise RollerError(f"expected one floor shared by every product, found {shared}")
    return shared[0], capacity[shared[0]]


def parse_floor(payload: Any, day: date) -> list[FloorReading]:
    """Every (start, window) floor reading for `day`, one per pair.

    Several products report the same window — the 60-minute admission and the
    60-minute group booking both cover an hour from 6:00 — and should agree;
    if they ever don't, the tighter one is kept.
    """
    if not isinstance(payload, list):
        raise RollerError("product-availability is not a list")
    found = floor_location(payload)
    if found is None:
        return []
    floor_id, stated_capacity = found

    best: dict[tuple[datetime, int], tuple[int, int]] = {}
    for p in payload:
        for s in p.get("sessions") or []:
            if s.get("date") != day.isoformat():
                continue
            start = datetime.combine(day, time.fromisoformat(s["startTime"]))
            for alloc in s.get("allocations") or []:
                capacity = next(
                    (loc.get("capacity") for loc in alloc.get("locations") or []
                     if loc.get("id") == floor_id),
                    stated_capacity,
                )
                if capacity is None:
                    continue
                for rt in alloc.get("resourceTimes") or []:
                    if floor_id not in (rt.get("resourceIds") or []):
                        continue
                    if rt.get("startMinutes") != 0:
                        continue
                    minutes = rt["endMinutes"] - rt["startMinutes"]
                    if minutes not in WINDOWS:
                        continue
                    left = rt["remainingCapacity"]
                    key = (start, minutes)
                    if key not in best or left < best[key][0]:
                        best[key] = (left, capacity)
    return [
        FloorReading(start=start, minutes=minutes, remaining=left, capacity=cap)
        for (start, minutes), (left, cap) in sorted(best.items())
    ]
