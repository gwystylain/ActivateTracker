from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

import apscheduler.triggers.cron as cron

from app.main import _build_trigger


def _unmappable_zone():
    raise ZoneInfoNotFoundError("British Columbia Standard Time")


def test_poll_triggers_run_in_utc_whatever_the_host_says(monkeypatch):
    """A trigger with no zone asks the OS, which made hour_utc local time on a
    host not set to UTC, and on Windows set to a zone tzlocal can't map it took
    the whole app down at startup."""
    monkeypatch.setattr(cron, "get_localzone", _unmappable_zone)

    daily = _build_trigger("daily", 11)
    crontab = _build_trigger("0 11 * * *", 0)

    noon = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    for trigger in (daily, crontab):
        fire = trigger.get_next_fire_time(None, noon)
        assert fire.utcoffset().total_seconds() == 0
        assert (fire.day, fire.hour) == (2, 11)
    assert _build_trigger("manual", 11) is None
