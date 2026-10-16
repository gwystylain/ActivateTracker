from datetime import datetime, timezone

import pytest

from app import db as db_mod
from app import poller as poller_mod
from app.config import BadgeConfig, PollConfig
from app.poller import format_handles, parse_handles, persist_snapshot
from app.scraper import BadgeState, LocationInfo, RateLimited, RoomGame, ScrapeResult


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
    assert row["player_rank"] == 6              # the page's "RANK"
    assert row["leaderboard_position"] == 287   # its "PLAYER POSITION"
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
    coquitlam = ScrapeResult(**{**_result(200).__dict__, "location_id": 38})
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


def _location(location_id):
    return LocationInfo(
        location_id=location_id,
        rooms=ROOMS,
        games=(RoomGame(1001, "Simon Says", tuple(range(10)), 0, room_id=10),),
        level_count=13,
    )


def _wire_poll(monkeypatch, totals, *, site_badges=None):
    """Stub the network. `totals` maps handle -> total_score to report.

    `site_badges(handle, location_id)` answers the site's own badge list; by
    default every handle holds badge 1 and not badge 2.

    Returns a dict of call logs, so a test can assert on how many requests and
    refreshes one poll actually made.
    """
    calls = {"refreshed": [], "profiles": [], "scores": [], "site_badges": [], "locations": []}

    async def fake_location(location_id, *, session, timeout):
        calls["locations"].append(location_id)
        return _location(location_id)

    async def fake_profile(handle, *, session, timeout):
        calls["profiles"].append(handle)
        return {"playerName": handle, "rank": 3, "stars": 355, "coins": 145}

    async def fake_fetch(handle, location_id, *, session, timeout, profile, level_count):
        calls["scores"].append((handle, location_id))
        return ScrapeResult(**{
            **_result(totals[handle]).__dict__,
            "handle": handle,
            "location_id": location_id,
            "coins": profile["coins"],
            "level_count": level_count,
        })

    async def fake_site_badges(handle, location_id, *, session, timeout):
        calls["site_badges"].append((handle, location_id))
        if site_badges is not None:
            return site_badges(handle, location_id)
        return _states((1, True), (2, False), progress=False)

    async def fake_refresh(conn, session, *, info, poll_cfg):
        calls["refreshed"].append(info.location_id)
        conn.execute(
            "INSERT INTO location_catalog (location_id, level_count, catalog_levels,"
            " fetched_at) VALUES (?, 10, 10, '2026-08-01T00:00:00+00:00')"
            " ON CONFLICT(location_id) DO UPDATE SET catalog_levels = 10",
            (info.location_id,),
        )
        return {"rooms": 1, "errors": 0}

    monkeypatch.setattr(poller_mod.scraper, "fetch_location", fake_location)
    monkeypatch.setattr(poller_mod.scraper, "fetch_profile", fake_profile)
    monkeypatch.setattr(poller_mod.scraper, "fetch", fake_fetch)
    monkeypatch.setattr(poller_mod.scraper, "fetch_player_badges", fake_site_badges)
    monkeypatch.setattr(poller_mod.catalog_mod, "refresh_location", fake_refresh)
    monkeypatch.setattr(poller_mod, "AsyncSession", _FakeSession)
    return calls


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
    calls = _wire_poll(monkeypatch, {"gmebagholder": 100, "kavo": 200})

    counters = await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))

    assert counters["polled"] == 2
    assert calls["locations"] == [72]            # one location read, not two
    assert calls["refreshed"] == [72]            # one walk, not two
    assert counters["catalog_locations"] == 1


async def test_poll_skips_the_catalog_when_no_score_moved(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert_player(conn)
    refreshed = _wire_poll(monkeypatch, {"gmebagholder": 100})["refreshed"]
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
    refreshed = _wire_poll(monkeypatch, {"gmebagholder": 100})["refreshed"]
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
    refreshed = _wire_poll(monkeypatch, totals)["refreshed"]
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


async def test_a_location_that_fails_to_load_still_polls_its_scores(tmp_path, monkeypatch):
    """The location's /rooms and /games feed only the level count and the
    catalog. Losing them costs those two, never the scores."""
    conn = _conn(tmp_path)
    _insert_player(conn)
    calls = _wire_poll(monkeypatch, {"gmebagholder": 100})

    async def boom(location_id, *, session, timeout):
        raise poller_mod.scraper.FetchError("HTTP 503")

    monkeypatch.setattr(poller_mod.scraper, "fetch_location", boom)
    counters = await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))

    assert counters["location_errors"] == 1
    assert counters["snapshots"] == 1
    assert calls["refreshed"] == []               # no rooms known, nothing to walk
    row = conn.execute("SELECT level_count FROM score_snapshots").fetchone()
    assert row["level_count"] is None             # unknown, not zero


async def test_the_profile_is_read_once_per_handle_not_once_per_location(tmp_path, monkeypatch):
    """Stars, coins and profile rank belong to the account, so a player at two
    locations costs one profile request, while scores and the site's badge
    list are asked of each location."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 38, 'coquitlam')",
        (pid,),
    )
    calls = _wire_poll(monkeypatch, {"gmebagholder": 100})

    await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))

    assert calls["profiles"] == ["gmebagholder"]
    assert calls["scores"] == [("gmebagholder", 38), ("gmebagholder", 72)]
    assert calls["site_badges"] == [("gmebagholder", 38), ("gmebagholder", 72)]
    coins = {r[0] for r in conn.execute("SELECT coins FROM score_snapshots")}
    assert coins == {145}


async def test_an_unknown_handle_is_counted_per_location_but_asked_once(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 38, 'coquitlam')",
        (pid,),
    )
    calls = _wire_poll(monkeypatch, {"gmebagholder": 100})
    asked = []

    async def not_found(handle, *, session, timeout):
        asked.append(handle)
        raise poller_mod.scraper.FetchError("HTTP 404")

    monkeypatch.setattr(poller_mod.scraper, "fetch_profile", not_found)
    counters = await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))

    assert asked == ["gmebagholder"]              # a 404 isn't asked again
    assert counters["errors"] == 2                # but both locations went unpolled
    assert calls["scores"] == []
    assert counters["snapshots"] == 0


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

def _states(*specs, progress=True):
    """(id, earned) pairs -> BadgeState list.

    With `progress`, the proxy's shape: a 0/25 target and a star value. Without,
    the site's, which says earned or not and nothing else.
    """
    return [
        BadgeState(
            badge_id=bid,
            name=f"Badge {bid}",
            description=f"do thing {bid}",
            earned=earned,
            progress=(25 if earned else 4) if progress else None,
            total_progress=25 if progress else None,
            stars=5 if progress else None,
        )
        for bid, earned in specs
    ]


def _earned(conn, pid=None):
    sql = "SELECT badge_id, earned FROM player_badges"
    args = ()
    if pid is not None:
        sql += " WHERE player_id = ?"
        args = (pid,)
    return dict(conn.execute(sql, args).fetchall())


def test_a_first_poll_records_badges_without_inventing_a_date(tmp_path):
    """Everything already earned was earned at some unknown past date. Stamping
    today would be a fabricated date on every one of them."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)

    got = poller_mod.persist_badges(
        conn, pid, _states((1, True), (2, True), (3, False)),
        now=datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc),
    )

    assert got == {"badges": 3, "newly_earned": 0, "retired": 0}
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
        conn, pid, _states((1, True), progress=False),
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


def test_the_catalog_follows_whatever_the_sources_last_said(tmp_path):
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


def test_the_site_s_silence_never_erases_the_proxy_s_numbers(tmp_path):
    """The site says earned or not and nothing else. Its None is "doesn't say",
    so last night's progress, target and star value all stand — and
    `progress_at` keeps saying when they were last true."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    poller_mod.persist_badges(
        conn, pid, _states((2, False)),
        now=datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc),
    )

    poller_mod.persist_badges(
        conn, pid, _states((2, False), progress=False),
        now=datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc),
    )

    row = conn.execute(
        "SELECT progress, total_progress, updated_at, progress_at FROM player_badges"
    ).fetchone()
    assert (row["progress"], row["total_progress"]) == (4, 25)
    assert row["updated_at"].startswith("2026-08-11")     # earned state is fresh
    assert row["progress_at"].startswith("2026-08-10")    # progress is not
    assert conn.execute("SELECT stars FROM badges").fetchone()[0] == 5


def test_progress_at_is_backfilled_for_rows_that_came_from_the_proxy(tmp_path):
    """Before the site had a badge list every row was the proxy's, so its
    progress is exactly as old as the row."""
    conn = db_mod.connect(tmp_path / "old.db")
    conn.executescript(
        """
        CREATE TABLE player_badges (
            player_id      INTEGER NOT NULL,
            badge_id       INTEGER NOT NULL,
            earned         INTEGER NOT NULL DEFAULT 0,
            progress       INTEGER,
            total_progress INTEGER,
            earned_on      TEXT,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (player_id, badge_id)
        );
        INSERT INTO player_badges VALUES (1, 7, 0, 4, 25, NULL, '2026-10-05T18:00:10+00:00');
        """
    )
    db_mod.init_schema(conn)

    row = conn.execute("SELECT progress, progress_at FROM player_badges").fetchone()
    assert (row["progress"], row["progress_at"]) == (4, "2026-10-05T18:00:10+00:00")


def test_snapshot_carries_the_page_s_own_badge_tally(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    result = ScrapeResult(**{
        **_result(500).__dict__,
        "trophy_progress": {
            "bronze": {"completedBadges": 32, "requiredBadges": 25, "progress": 1, "earned": True},
            "platinum": {"completedBadges": 32, "requiredBadges": 101, "progress": 0.32},
        },
    })

    persist_snapshot(conn, pid, result)

    row = conn.execute(
        "SELECT badges_earned, badges_possible FROM score_snapshots"
    ).fetchone()
    assert (row["badges_earned"], row["badges_possible"]) == (32, 101)


def test_a_page_without_trophy_progress_leaves_the_tally_null(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    persist_snapshot(conn, pid, _result(500))

    row = conn.execute(
        "SELECT badges_earned, badges_possible FROM score_snapshots"
    ).fetchone()
    assert (row["badges_earned"], row["badges_possible"]) == (None, None)


# ---------- badges from a poll ----------

def _proxy(monkeypatch, answer):
    """Stub the proxy. `answer(handle)` returns states or raises."""
    asked = []

    async def fake_badges(handle, *, session, base, timeout):
        asked.append(handle)
        return answer(handle)

    monkeypatch.setattr(poller_mod.scraper, "fetch_badges", fake_badges)
    return asked


def _two_handles(conn):
    pid = conn.execute(
        "INSERT INTO players (handle) VALUES (?)", ("gmebagholder, kavo",)
    ).lastrowid
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 72, 'langley')",
        (pid,),
    )
    return pid


async def test_the_site_s_badges_land_with_no_proxy_at_all(tmp_path, monkeypatch):
    """Earned badges come from playactivate.com itself, so they no longer wait
    on a third party — and with no badge config the poll never leaves it."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})

    def refuse(handle):
        raise AssertionError("the proxy must not be asked without a config")

    _proxy(monkeypatch, refuse)

    counters = await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))

    assert counters["badges_fetched"] == 1
    assert counters["badges_errors"] == 0
    assert _earned(conn, pid) == {1: 1, 2: 0}

    await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)),
        badge_cfg=BadgeConfig(enabled=False),
    )


async def test_the_proxy_is_asked_once_per_handle_not_once_per_location(tmp_path, monkeypatch):
    """The proxy takes only a handle. A player at two locations must not cost
    two identical requests to somebody else's server."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 38, 'coquitlam')",
        (pid,),
    )
    _wire_poll(monkeypatch, {"gmebagholder": 100})
    asked = _proxy(monkeypatch, lambda h: _states((1, True), (2, False)))

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=BadgeConfig()
    )

    assert asked == ["gmebagholder"]          # two locations, one proxy fetch
    assert counters["polled"] == 2            # but both locations still scored
    assert counters["badges_fetched"] == 1
    assert conn.execute("SELECT COUNT(*) FROM player_badges").fetchone()[0] == 2


async def test_the_proxy_adds_progress_and_the_badges_no_location_offers(tmp_path, monkeypatch):
    """Row By Row needs a room neither tracked location has. The site never
    lists it; the proxy does, earned."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})
    _proxy(monkeypatch, lambda h: _states((1, True), (2, False), (63, True)))

    await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=_badge_cfg()
    )

    assert _earned(conn, pid) == {1: 1, 2: 0, 63: 1}
    row = conn.execute(
        "SELECT progress, total_progress FROM player_badges WHERE badge_id = 2"
    ).fetchone()
    assert (row["progress"], row["total_progress"]) == (4, 25)


async def test_a_proxy_failure_does_not_fail_the_score_poll(tmp_path, monkeypatch):
    """The proxy is somebody else's server — the least reliable leg of the
    poll, and the one neither the scores nor earned badges depend on."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})

    def boom(handle):
        raise RuntimeError("proxy down")

    _proxy(monkeypatch, boom)

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=BadgeConfig()
    )

    assert counters["badges_errors"] == 1
    assert counters["badges_fetched"] == 1    # the site's answer still landed
    assert counters["polled"] == 1            # and so did the scores
    assert _earned(conn, pid) == {1: 1, 2: 0}


async def test_a_night_without_the_proxy_retires_nothing(tmp_path, monkeypatch):
    """The site lists only what the tracked locations offer. Taking that as the
    whole catalog while the proxy is merely down would delete every badge only
    the proxy reports — Row By Row, earned elsewhere."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    poller_mod.persist_badges(conn, pid, _states((1, False), (2, False), (63, True)))
    _wire_poll(monkeypatch, {"gmebagholder": 100})

    def boom(handle):
        raise RuntimeError("proxy down")

    _proxy(monkeypatch, boom)

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=BadgeConfig()
    )

    assert counters["badges_retired"] == 0
    assert _earned(conn, pid) == {1: 1, 2: 0, 63: 1}   # badge 1 updated, 63 kept


async def test_with_the_proxy_switched_off_the_site_defines_the_catalog(tmp_path, monkeypatch):
    """Turned off, not down: then nothing else will ever speak for a badge the
    tracked locations don't offer, and the denominator is theirs."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    poller_mod.persist_badges(conn, pid, _states((1, True), (2, False), (63, True)))
    _wire_poll(monkeypatch, {"gmebagholder": 100})

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)),
        badge_cfg=BadgeConfig(enabled=False),
    )

    assert counters["badges_retired"] == 1
    assert _earned(conn, pid) == {1: 1, 2: 0}


async def test_a_location_the_site_fails_on_falls_back_to_the_proxy(tmp_path, monkeypatch):
    """Half the site's answer is not an answer. The proxy's whole one is, and it
    alone is enough to write — and retire — from."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 38, 'coquitlam')",
        (pid,),
    )
    poller_mod.persist_badges(conn, pid, _states((1, True), (2, False), (50, True)))

    def site(handle, location_id):
        if location_id == 38:
            raise poller_mod.scraper.FetchError("HTTP 502")
        return _states((1, True), (2, False), progress=False)

    _wire_poll(monkeypatch, {"gmebagholder": 100}, site_badges=site)
    _proxy(monkeypatch, lambda h: _states((1, True), (2, True)))

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=_badge_cfg()
    )

    assert _earned(conn, pid) == {1: 1, 2: 1}         # the proxy's word, 50 retired
    assert counters["badges_retired"] == 1
    assert counters["badges_errors"] == 1


async def test_one_handle_failing_does_not_wipe_the_others_badges(tmp_path, monkeypatch):
    """`combine_badges` can only OR together what it was handed, and
    `persist_badges` writes `earned` from that. So persisting one handle of a
    two-handle player would clear every badge only the other profile holds -
    last poll's rows are older but true, these would be wrong. With both
    sources short a handle, nothing is written and nothing is retired."""
    conn = _conn(tmp_path)
    pid = _two_handles(conn)
    # Last night, when both handles answered: badges 2 and 50 are kavo's.
    poller_mod.persist_badges(conn, pid, _states((1, True), (2, True), (50, True)))

    def site(handle, location_id):
        if handle == "kavo":
            raise poller_mod.scraper.FetchError("HTTP 502")
        return _states((1, True), (2, False), progress=False)

    def proxy(handle):
        if handle == "kavo":
            raise RateLimited("HTTP 429", retry_after=None)
        return _states((1, True), (2, False))

    _wire_poll(monkeypatch, {"gmebagholder": 100, "kavo": 200}, site_badges=site)
    _no_sleep(monkeypatch)
    _proxy(monkeypatch, proxy)

    counters = await poller_mod.poll_all(
        conn,
        PollConfig(jitter_seconds=(0.0, 0.0)),
        badge_cfg=_badge_cfg(max_retries=0),
    )

    assert _earned(conn, pid) == {1: 1, 2: 1, 50: 1}   # kavo's badges survive
    assert counters["badges_skipped"] == 1
    assert counters["badges_fetched"] == 0             # nothing was written at all
    assert counters["badges_retired"] == 0


async def test_either_source_whole_is_enough_for_a_two_handle_player(tmp_path, monkeypatch):
    """The site answered for both handles; the proxy only for one. The site's
    answer is whole, so it is written — the proxy's half is not mixed in."""
    conn = _conn(tmp_path)
    pid = _two_handles(conn)

    def site(handle, location_id):
        return _states((1, True), (2, handle == "kavo"), progress=False)

    def proxy(handle):
        if handle == "kavo":
            raise RateLimited("HTTP 429", retry_after=None)
        return _states((1, True), (2, False), (3, False))

    _wire_poll(monkeypatch, {"gmebagholder": 100, "kavo": 200}, site_badges=site)
    _no_sleep(monkeypatch)
    _proxy(monkeypatch, proxy)

    counters = await poller_mod.poll_all(
        conn,
        PollConfig(jitter_seconds=(0.0, 0.0)),
        badge_cfg=_badge_cfg(max_retries=0),
    )

    assert _earned(conn, pid) == {1: 1, 2: 1}          # no badge 3: half an answer
    assert counters["badges_fetched"] == 1


# ---------- each location's offer ----------

def _offer(conn, location_id):
    return {
        r[0] for r in conn.execute(
            "SELECT badge_id FROM location_badges WHERE location_id = ?", (location_id,)
        )
    }


async def test_each_location_s_offer_is_kept_from_the_lists_the_poll_already_read(
    tmp_path, monkeypatch
):
    """No extra requests: the player's badge list at a location *is* that
    location's offer, earned flags aside."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 38, 'coquitlam')",
        (pid,),
    )

    def site(handle, location_id):
        extra = (125,) if location_id == 38 else (111,)
        return _states((1, True), (2, False), *((b, False) for b in extra), progress=False)

    calls = _wire_poll(monkeypatch, {"gmebagholder": 100}, site_badges=site)
    await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))

    assert len(calls["site_badges"]) == 2
    assert _offer(conn, 38) == {1, 2, 125}
    assert _offer(conn, 72) == {1, 2, 111}


async def test_one_whole_answer_defines_a_location_and_none_leaves_it_alone(
    tmp_path, monkeypatch
):
    """The list is the same whoever asks, so one handle that answered is enough
    — unlike a player's earned set, where a missing handle is a missing half.
    A location nobody answered for keeps last night's rows."""
    conn = _conn(tmp_path)
    _two_handles(conn)
    poller_mod.persist_location_badges(conn, 72, [9])

    def kavo_fails(handle, location_id):
        if handle == "kavo":
            raise poller_mod.scraper.FetchError("HTTP 502")
        return _states((1, True), (2, False), progress=False)

    _wire_poll(monkeypatch, {"gmebagholder": 100, "kavo": 200}, site_badges=kavo_fails)
    await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))
    assert _offer(conn, 72) == {1, 2}            # replaced, not merged with 9

    def all_fail(handle, location_id):
        raise poller_mod.scraper.FetchError("HTTP 502")

    _wire_poll(monkeypatch, {"gmebagholder": 100, "kavo": 200}, site_badges=all_fail)
    await poller_mod.poll_all(conn, PollConfig(jitter_seconds=(0.0, 0.0)))
    assert _offer(conn, 72) == {1, 2}            # untouched


# ---------- badges: the proxy's rate limit ----------

def _no_sleep(monkeypatch):
    """Swallow every await asyncio.sleep and record what it was asked to wait."""
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(poller_mod.asyncio, "sleep", fake_sleep)
    return slept


def _badge_cfg(**kw):
    """A badge config with the production waits stripped out."""
    return BadgeConfig(**{"spacing_seconds": (0.0, 0.0), "backoff_seconds": 5.0, **kw})


async def test_a_rate_limited_badge_fetch_is_waited_out_not_lost(tmp_path, monkeypatch):
    """The proxy once let five handles through and refused the rest with an
    instant 429. Dropping those is what pinned the same players' badges ten
    days stale, since the fetch order was stable and so the losers never
    changed."""
    conn = _conn(tmp_path)
    _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})
    slept = _no_sleep(monkeypatch)
    attempts = []

    def answer(handle):
        attempts.append(handle)
        if len(attempts) == 1:
            raise RateLimited("HTTP 429", retry_after=None)
        return _states((1, True), (2, False), (3, False))

    _proxy(monkeypatch, answer)

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=_badge_cfg()
    )

    assert attempts == ["gmebagholder", "gmebagholder"]
    assert counters["badges_fetched"] == 1
    assert counters["badges_errors"] == 0
    assert 5.0 in slept                       # the configured backoff, not a guess
    # Badge 3 is the proxy's alone, so its row is the proof the retry landed.
    assert conn.execute("SELECT COUNT(*) FROM player_badges").fetchone()[0] == 3


async def test_the_proxys_own_retry_after_beats_our_backoff(tmp_path, monkeypatch):
    """Guessing at a stranger's rate limit is how you get banned from it."""
    conn = _conn(tmp_path)
    _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})
    slept = _no_sleep(monkeypatch)
    attempts = []

    def answer(handle):
        attempts.append(handle)
        if len(attempts) == 1:
            raise RateLimited("HTTP 429", retry_after=47.0)
        return _states((1, True))

    _proxy(monkeypatch, answer)

    await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=_badge_cfg()
    )

    assert 47.0 in slept
    assert 5.0 not in slept


async def test_retries_are_bounded_and_the_failure_is_still_counted(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})
    _no_sleep(monkeypatch)
    attempts = []

    def answer(handle):
        attempts.append(handle)
        raise RateLimited("HTTP 429", retry_after=None)

    _proxy(monkeypatch, answer)

    counters = await poller_mod.poll_all(
        conn,
        PollConfig(jitter_seconds=(0.0, 0.0)),
        badge_cfg=_badge_cfg(max_retries=2),
    )

    assert len(attempts) == 3                 # the first try plus two retries
    assert counters["badges_errors"] == 1
    assert counters["polled"] == 1            # scores landed anyway


async def test_a_wait_longer_than_the_poll_can_afford_is_not_slept_off(tmp_path, monkeypatch):
    """A poll holds a global lock. An hour in here is an hour the scheduler and
    the admin's Refresh button spend blocked - tomorrow's poll is a better place
    to try again than the inside of today's."""
    conn = _conn(tmp_path)
    _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})
    slept = _no_sleep(monkeypatch)
    attempts = []

    def answer(handle):
        attempts.append(handle)
        raise RateLimited("HTTP 429", retry_after=3600.0)

    _proxy(monkeypatch, answer)

    counters = await poller_mod.poll_all(
        conn,
        PollConfig(jitter_seconds=(0.0, 0.0)),
        badge_cfg=_badge_cfg(max_wait_seconds=120.0),
    )

    assert len(attempts) == 1                 # gave up rather than waiting it out
    assert 3600.0 not in slept
    assert counters["badges_errors"] == 1


async def test_only_a_429_is_retried(tmp_path, monkeypatch):
    """A 404 or a 500 says the handle is wrong or the upstream is down.
    Hammering either is rude and pointless."""
    conn = _conn(tmp_path)
    _insert_player(conn)
    _wire_poll(monkeypatch, {"gmebagholder": 100})
    _no_sleep(monkeypatch)
    attempts = []

    def answer(handle):
        attempts.append(handle)
        raise poller_mod.scraper.FetchError("HTTP 500")

    _proxy(monkeypatch, answer)

    counters = await poller_mod.poll_all(
        conn, PollConfig(jitter_seconds=(0.0, 0.0)), badge_cfg=_badge_cfg()
    )

    assert len(attempts) == 1
    assert counters["badges_errors"] == 1


def test_the_stalest_player_goes_first_so_the_same_ones_cannot_starve(tmp_path):
    """The fetch order used to be the score-poll order, which is stable - so a
    rate limit that cuts the leg short cut the same players short every night.
    Whoever lost last night leads tonight."""
    conn = _conn(tmp_path)
    fresh = conn.execute("INSERT INTO players (handle) VALUES ('fresh')").lastrowid
    stale = conn.execute("INSERT INTO players (handle) VALUES ('stale')").lastrowid
    never = conn.execute("INSERT INTO players (handle) VALUES ('never')").lastrowid

    poller_mod.persist_badges(
        conn, fresh, _states((1, True)),
        now=datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
    )
    poller_mod.persist_badges(
        conn, stale, _states((1, True)),
        now=datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc),
    )
    # The site refreshes everyone's earned state nightly. That must not make a
    # player look fresh to the proxy: their progress is still as old as it was.
    for pid in (stale, never):
        poller_mod.persist_badges(
            conn, pid, _states((1, True), progress=False),
            now=datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc),
        )

    seen = {fresh: ["fresh"], stale: ["stale"], never: ["never"]}
    assert [pid for pid, _ in poller_mod.badge_order(conn, seen)] == [never, stale, fresh]


# ---------- badges: retiring what upstream dropped ----------

def test_a_badge_upstream_no_longer_lists_is_dropped_not_left_behind(tmp_path):
    """Three badges went in the proxy's move to activate-scores.ca. Upserting
    alone would count an existing player out of the old 118 while a newly added
    one was counted out of 115 - a denominator that depends on when you joined."""
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    poller_mod.persist_badges(conn, pid, _states((1, True), (50, True), (2, False)))

    got = poller_mod.persist_badges(conn, pid, _states((1, True), (2, False)))

    assert got["retired"] == 1
    held = [r[0] for r in conn.execute(
        "SELECT badge_id FROM player_badges WHERE player_id = ? ORDER BY badge_id", (pid,)
    )]
    assert held == [1, 2]
    # The catalog mirrors upstream, so the row goes too.
    assert [r[0] for r in conn.execute("SELECT badge_id FROM badges ORDER BY badge_id")] == [1, 2]


def test_an_answer_that_is_not_the_whole_catalog_retires_nothing(tmp_path):
    conn = _conn(tmp_path)
    pid = _insert_player(conn)
    poller_mod.persist_badges(conn, pid, _states((1, True), (50, True)))

    got = poller_mod.persist_badges(conn, pid, _states((1, True)), retire=False)

    assert got["retired"] == 0
    assert _earned(conn, pid) == {1: 1, 50: 1}


def test_a_retired_badge_survives_while_another_player_still_holds_it(tmp_path):
    """The foreign key makes the order mandatory rather than tidy: a player
    whose own poll was skipped keeps their row until theirs lands."""
    conn = _conn(tmp_path)
    one = _insert_player(conn)
    two = conn.execute("INSERT INTO players (handle) VALUES ('kavo')").lastrowid
    for pid in (one, two):
        poller_mod.persist_badges(conn, pid, _states((1, True), (50, True)))

    poller_mod.persist_badges(conn, one, _states((1, True)))

    # Gone for the player who was polled, still there for the one who wasn't.
    assert conn.execute(
        "SELECT COUNT(*) FROM player_badges WHERE badge_id = 50"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM badges WHERE badge_id = 50"
    ).fetchone()[0] == 1

    poller_mod.persist_badges(conn, two, _states((1, True)))
    assert conn.execute("SELECT COUNT(*) FROM badges WHERE badge_id = 50").fetchone()[0] == 0
