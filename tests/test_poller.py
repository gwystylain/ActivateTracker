from datetime import datetime, timezone

import pytest

from app import db as db_mod
from app import poller as poller_mod
from app.config import BadgeConfig, PollConfig
from app.poller import format_handles, parse_handles, persist_snapshot
from app.scraper import BadgeState, ScrapeResult


def _conn(tmp_path):
    conn = db_mod.connect(tmp_path / "t.db")
    db_mod.init_schema(conn)
    return conn


def _insert_player(conn) -> int:
    cur = conn.execute(
        "INSERT INTO players (handle) VALUES (?)", ("gmebagholder",)
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug) VALUES (?, ?, ?)",
        (pid, 72, "langley"),
    )
    return pid


def _result(total: int) -> ScrapeResult:
    return ScrapeResult(
        handle="gmebagholder",
        location_id=72,
        location_slug="langley",
        player_name="GMEbagholder",
        profile_rank=3,
        stars=355,
        coins=145,
        location_player_rank=6,
        yearly_rank=2932,
        standing=287,
        total_score=total,
        yearly_score=total // 2,
        scores=[{"gameId": 1, "levelId": 0, "highScore": total}],
        levels_beat=1,
        level_count=470,
    )


def test_snapshot_persists_rank_and_levels_beat(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    persist_snapshot(conn, pid, _result(100))

    row = conn.execute(
        "SELECT player_rank, leaderboard_position, levels_beat, level_count"
        " FROM score_snapshots"
    ).fetchone()
    assert row["player_rank"] == 6              # the page-header badge rank
    assert row["leaderboard_position"] == 287   # "Your Leaderboard Position"
    assert row["levels_beat"] == 1
    assert row["level_count"] == 470


def test_migrate_adds_new_columns_to_existing_table(tmp_path):
    """A pre-existing deployment's table lacks the new columns; init_schema's
    PRAGMA-guarded ALTERs must add them without touching existing rows."""
    conn = db_mod.connect(tmp_path / "old.db")
    conn.executescript(
        """
        CREATE TABLE score_snapshots (
            id              INTEGER PRIMARY KEY,
            player_id       INTEGER NOT NULL,
            location_id     INTEGER NOT NULL,
            polled_at       TEXT NOT NULL,
            total_score     INTEGER NOT NULL,
            yearly_score    INTEGER NOT NULL,
            player_rank     INTEGER,
            yearly_rank     INTEGER,
            stars           INTEGER,
            coins           INTEGER,
            raw_scores_json TEXT NOT NULL
        );
        INSERT INTO score_snapshots
            (player_id, location_id, polled_at, total_score, yearly_score, raw_scores_json)
        VALUES (1, 72, '2026-01-01T00:00:00', 500, 200, '[]');
        """
    )
    db_mod.init_schema(conn)

    row = conn.execute(
        "SELECT total_score, levels_beat, level_count, leaderboard_position"
        " FROM score_snapshots"
    ).fetchone()
    assert row["total_score"] == 500              # existing row survives
    assert row["levels_beat"] is None             # backfilled as NULL
    assert row["level_count"] is None
    assert row["leaderboard_position"] is None


def test_first_snapshot_inserts_no_visit(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)

    visit = persist_snapshot(conn, pid, _result(100))
    assert visit is False
    snaps = conn.execute("SELECT count(*) AS n FROM score_snapshots").fetchone()["n"]
    visits = conn.execute("SELECT count(*) AS n FROM visits").fetchone()["n"]
    assert snaps == 1
    assert visits == 0


def test_increased_score_inserts_visit(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)

    persist_snapshot(conn, pid, _result(100), now=datetime(2026, 4, 27, tzinfo=timezone.utc))
    inserted = persist_snapshot(conn, pid, _result(150), now=datetime(2026, 4, 28, tzinfo=timezone.utc))
    assert inserted is True

    rows = conn.execute("SELECT visit_date, score_delta FROM visits").fetchall()
    assert len(rows) == 1
    # Detected on 2026-04-28; visit is dated to the day before (refresh lag).
    assert rows[0]["visit_date"] == "2026-04-27"
    assert rows[0]["score_delta"] == 50


def test_unchanged_or_lower_score_inserts_no_visit(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)

    persist_snapshot(conn, pid, _result(100))
    assert persist_snapshot(conn, pid, _result(100)) is False
    assert persist_snapshot(conn, pid, _result(80)) is False
    visits = conn.execute("SELECT count(*) AS n FROM visits").fetchone()["n"]
    assert visits == 0


def test_visits_isolated_per_location(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug) VALUES (?, ?, ?)",
        (pid, 38, "coquitlam"),
    )

    # Establish baselines at both locations.
    persist_snapshot(conn, pid, _result(100))
    coquitlam = ScrapeResult(**{**_result(200).__dict__, "location_id": 38, "location_slug": "coquitlam"})
    persist_snapshot(conn, pid, coquitlam)

    # Increase only langley.
    inserted = persist_snapshot(conn, pid, _result(110))
    assert inserted is True
    visits = conn.execute("SELECT location_id FROM visits").fetchall()
    assert [v["location_id"] for v in visits] == [72]


def test_parse_handles_dedupes_strips_lowercases():
    assert parse_handles("  Stebb,  STEVO  ,stebb,") == ["stebb", "stevo"]
    assert parse_handles("") == []
    assert parse_handles("   ") == []
    assert parse_handles("alpha") == ["alpha"]


def test_format_handles_canonical_form():
    assert format_handles(["stebb", "stevo"]) == "stebb, stevo"
    assert format_handles(["solo"]) == "solo"


# ---------- catalog refresh from poll_all ----------

ROOMS = ({"id": 10, "name": "Hoops"},)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _wire_poll(monkeypatch, totals):
    """Stub the network. `totals` maps handle -> total_score to report.

    Returns the list of locations refresh_location was called for, so a test
    can assert on how many refreshes one poll actually triggered.
    """
    refreshed = []

    async def fake_fetch(handle, location_id, slug, *, session, timeout):
        return ScrapeResult(**{
            **_result(totals[handle]).__dict__,
            "handle": handle,
            "location_id": location_id,
            "location_slug": slug,
            "rooms": ROOMS,
            "level_count": 10,
        })

    async def fake_refresh(conn, session, *, location_id, **kw):
        refreshed.append(location_id)
        conn.execute(
            "INSERT INTO location_catalog (location_id, level_count, catalog_levels,"
            " fetched_at) VALUES (?, 10, 10, '2026-08-01T00:00:00+00:00')"
            " ON CONFLICT(location_id) DO UPDATE SET catalog_levels = 10",
            (location_id,),
        )
        return {"rooms": 1, "errors": 0}

    monkeypatch.setattr(poller_mod.scraper, "fetch", fake_fetch)
    monkeypatch.setattr(poller_mod.catalog_mod, "refresh_location", fake_refresh)
    monkeypatch.setattr(poller_mod, "AsyncSession", _FakeSession)
    return refreshed


async def test_poll_refreshes_a_location_once_not_once_per_player(tmp_path, monkeypatch):
    """The catalog is player-independent, so two players at one location must
    still cost a single walk of that location's rooms."""
    conn = _conn(tmp_path)
    for handle in ("gmebagholder", "kavo"):
        pid = conn.execute("INSERT INTO players (handle) VALUES (?)", (handle,)).lastrowid
        conn.execute(
            "INSERT INTO player_locations (player_id, location_id, slug)"
            " VALUES (?, 72, 'langley')",
            (pid,),
        )
    refreshed = _wire_poll(monkeypatch, {"gmebagholder": 100, "kavo": 200})

    counters = await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))

    assert counters["polled"] == 2
    assert refreshed == [72]                     # one walk, not two
    assert counters["catalog_locations"] == 1


async def test_poll_skips_the_catalog_when_no_score_moved(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert_player(conn)
    refreshed = _wire_poll(monkeypatch, {"gmebagholder": 100})
    cfg = PollConfig(jitter_seconds=(0.0, 0.0))

    await poller_mod.poll_all(conn, cfg)          # bootstraps the catalog
    assert refreshed == [72]

    await poller_mod.poll_all(conn, cfg)          # same score: nothing to re-read
    assert refreshed == [72]


async def test_force_catalog_rewalks_even_when_nothing_moved(tmp_path, monkeypatch):
    """The admin override. Top scores are set by everyone at the venue, not
    just tracked players, so a quiet poll leaving them alone has to be
    defeatable by hand."""
    conn = _conn(tmp_path)
    _insert_player(conn)
    refreshed = _wire_poll(monkeypatch, {"gmebagholder": 100})
    cfg = PollConfig(jitter_seconds=(0.0, 0.0))

    await poller_mod.poll_all(conn, cfg)
    await poller_mod.poll_all(conn, cfg)                          # throttled
    assert refreshed == [72]

    await poller_mod.poll_all(conn, cfg, force_catalog=True)      # override
    assert refreshed == [72, 72]


async def test_poll_refreshes_the_catalog_when_a_score_moved(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert_player(conn)
    totals = {"gmebagholder": 100}
    refreshed = _wire_poll(monkeypatch, totals)
    cfg = PollConfig(jitter_seconds=(0.0, 0.0))

    await poller_mod.poll_all(conn, cfg)
    totals["gmebagholder"] = 150                  # they played
    await poller_mod.poll_all(conn, cfg)

    assert refreshed == [72, 72]


async def test_catalog_failure_does_not_fail_the_score_poll(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})

    async def boom(*a, **kw):
        raise RuntimeError("catalog exploded")

    monkeypatch.setattr(poller_mod.catalog_mod, "refresh_location", boom)

    counters = await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))

    assert counters["catalog_errors"] == 1
    assert counters["snapshots"] == 1             # the score still landed
    assert conn.execute("SELECT count(*) AS n FROM score_snapshots").fetchone()["n"] == 1


def test_cascade_delete_removes_snapshots_and_visits(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    persist_snapshot(conn, pid, _result(100))
    persist_snapshot(conn, pid, _result(150))  # produces 1 visit

    conn.execute("DELETE FROM players WHERE id = ?", (pid,))
    snaps = conn.execute("SELECT count(*) AS n FROM score_snapshots").fetchone()["n"]
    visits = conn.execute("SELECT count(*) AS n FROM visits").fetchone()["n"]
    locs = conn.execute("SELECT count(*) AS n FROM player_locations").fetchone()["n"]
    assert snaps == 0
    assert visits == 0
    assert locs == 0


# ---------- badges ----------

def _states(*specs):
    """(id, earned) pairs -> BadgeState list, with a 0/25 progress target."""
    return [
        BadgeState(
            badge_id=bid,
            name=f"Badge {bid}",
            description=f"do thing {bid}",
            earned=earned,
            progress=25 if earned else 4,
            total_progress=25,
            stars=5,
        )
        for bid, earned in specs
    ]


def test_a_first_poll_records_badges_without_inventing_a_date(tmp_path):
    """Everything already earned was earned at some unknown past date. Stamping
    today would be a fabricated date on every one of them."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)

    got = poller_mod.persist_badges(
        conn, pid, _states((1, True), (2, True), (3, False)),
        now=datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc),
    )

    assert got == {"badges": 3, "newly_earned": 0}
    rows = conn.execute(
        "SELECT badge_id, earned, earned_on FROM player_badges ORDER BY badge_id"
    ).fetchall()
    assert [r["earned"] for r in rows] == [1, 1, 0]
    assert all(r["earned_on"] is None for r in rows)


def test_earning_a_badge_dates_it_the_day_before_the_poll(tmp_path):
    """Same one-day backdate the visit rows get: Activate's data refreshes a day
    late, so a badge first seen in Tuesday's poll was earned on Monday."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    poller_mod.persist_badges(
        conn, pid, _states((1, False)),
        now=datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc),
    )

    got = poller_mod.persist_badges(
        conn, pid, _states((1, True)),
        now=datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc),
    )

    assert got["newly_earned"] == 1
    row = conn.execute("SELECT earned, earned_on FROM player_badges").fetchone()
    assert (row["earned"], row["earned_on"]) == (1, "2026-08-10")


def test_a_later_poll_does_not_restamp_a_badge_already_earned(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    for day in (10, 11):
        poller_mod.persist_badges(
            conn, pid, _states((1, day > 10)),
            now=datetime(2026, 8, day, 11, 0, tzinfo=timezone.utc),
        )

    got = poller_mod.persist_badges(
        conn, pid, _states((1, True)),
        now=datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc),
    )

    assert got["newly_earned"] == 0
    assert conn.execute("SELECT earned_on FROM player_badges").fetchone()[0] == "2026-08-10"


def test_the_catalog_follows_whatever_the_api_last_said(tmp_path):
    """The badge list is restated in full on every call, so `badges` is a mirror
    of upstream rather than something we accumulate."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    poller_mod.persist_badges(conn, pid, _states((1, False)))

    renamed = [
        BadgeState(1, "Renamed", "new wording", False, 4, 25, 10),
    ]
    poller_mod.persist_badges(conn, pid, renamed)

    row = conn.execute("SELECT name, description, stars FROM badges").fetchone()
    assert (row["name"], row["description"], row["stars"]) == ("Renamed", "new wording", 10)


def test_snapshot_carries_the_page_s_own_badge_tally(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    result = ScrapeResult(**{
        **_result(500).__dict__,
        "trophy_progress": {
            "bronze": {"progress": 0.44, "requiredBadges": 25},
            "platinum": {"progress": 0.09, "requiredBadges": 118},
        },
    })

    persist_snapshot(conn, pid, result)

    row = conn.execute(
        "SELECT badges_earned, badges_possible FROM score_snapshots"
    ).fetchone()
    assert (row["badges_earned"], row["badges_possible"]) == (11, 118)


def test_a_page_without_trophy_progress_leaves_the_tally_null(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    persist_snapshot(conn, pid, _result(500))

    row = conn.execute(
        "SELECT badges_earned, badges_possible FROM score_snapshots"
    ).fetchone()
    assert (row["badges_earned"], row["badges_possible"]) == (None, None)


async def test_badges_are_fetched_once_per_handle_not_once_per_location(tmp_path, monkeypatch):
    """The badge endpoint takes only a handle. A player at two locations must
    not cost two identical badge requests."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 38, 'coquitlam')",
        (pid,),
    )
    _wire_poll(monkeypatch, {"gmebagholder": 100})
    asked = []

    async def fake_badges(handle, *, session, base, timeout):
        asked.append(handle)
        return _states((1, True), (2, False))

    monkeypatch.setattr(poller_mod.scraper, "fetch_badges", fake_badges)

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=BadgeConfig()
    )

    assert asked == ["gmebagholder"]          # two locations, one badge fetch
    assert counters["polled"] == 2            # but both locations still scored
    assert counters["badges_fetched"] == 1
    assert conn.execute("SELECT COUNT(*) FROM player_badges").fetchone()[0] == 2


async def test_a_badge_failure_does_not_fail_the_score_poll(tmp_path, monkeypatch):
    """Badges come from a third party's proxy — the least reliable leg of the
    poll, and the one the scores must not depend on."""
    conn = _conn(tmp_path)
    _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})

    async def boom(handle, *, session, base, timeout):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(poller_mod.scraper, "fetch_badges", boom)

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=BadgeConfig()
    )

    assert counters["badges_errors"] == 1
    assert counters["badges_fetched"] == 0
    assert counters["polled"] == 1            # scores landed anyway
    assert conn.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0] == 1


async def test_badges_are_skipped_without_a_config(tmp_path, monkeypatch):
    """Opt-in: the one leg that leaves playactivate.com is never taken by
    default, so a caller can't reach a third party by omission."""
    conn = _conn(tmp_path)
    _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})

    async def boom(handle, **kw):
        raise AssertionError("badges must not be fetched without a config")

    monkeypatch.setattr(poller_mod.scraper, "fetch_badges", boom)

    counters = await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))
    assert counters["badges_fetched"] == 0
    assert counters["badges_errors"] == 0

    await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)),
        badge_cfg=BadgeConfig(enabled=False),
    )
