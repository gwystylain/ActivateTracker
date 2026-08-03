import asyncio

from app import catalog as catalog_mod
from app import db as db_mod
from app.config import PollConfig
from app.scraper import RoomGame, RoomScrape, ScrapeError

POLL = PollConfig(jitter_seconds=(0.0, 0.0))

COQUITLAM = 38
ROOMS = [{"id": 10, "name": "Hoops"}, {"id": 28, "name": "Scan"}]


def _conn(tmp_path):
    conn = db_mod.connect(tmp_path / "t.db")
    db_mod.init_schema(conn)
    return conn


def _room(room_id, name, games, *, top=None):
    """games: [(game_id, name, n_levels)]"""
    return RoomScrape(
        room_id=room_id,
        room_name=name,
        games=tuple(
            RoomGame(game_id=gid, name=gname, levels=tuple(range(n)), order=i)
            for i, (gid, gname, n) in enumerate(games)
        ),
        top_scores=top or {},
    )


class _FakeSession:
    """Stands in for curl_cffi's AsyncSession; catalog only calls fetch_room."""


def _refresh(conn, monkeypatch, responses, *, rooms=ROOMS, level_count=70):
    """responses: {room_name: RoomScrape | Exception}"""
    calls = []

    async def fake_fetch_room(handle, location_id, slug, room_name, *, session, timeout):
        calls.append(room_name)
        result = responses[room_name]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(catalog_mod.scraper, "fetch_room", fake_fetch_room)
    counters = asyncio.run(
        catalog_mod.refresh_location(
            conn,
            _FakeSession(),
            location_id=COQUITLAM,
            slug="coquitlam",
            handle="gmebagholder",
            rooms=rooms,
            level_count=level_count,
            poll_cfg=POLL,
        )
    )
    return counters, calls


def test_refresh_stores_rooms_games_and_top_scores(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    counters, calls = _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10), (1003, "Barrage", 10)],
                           top={(1001, 0): 2041, (1003, 5): 6795}),
            "Scan": _room(28, "Scan", [(2802, "Spot", 10)], top={(2802, 0): 500}),
        },
        level_count=30,
    )

    assert counters == {"rooms": 2, "errors": 0}
    assert calls == ["Hoops", "Scan"]

    rooms = catalog_mod.rooms_for_location(conn, COQUITLAM)
    assert [r["name"] for r in rooms] == ["Hoops", "Scan"]
    assert [g["name"] for g in rooms[0]["games"]] == ["Simon Says", "Barrage"]
    assert rooms[0]["games"][0]["levels"] == list(range(10))

    assert catalog_mod.top_scores_for_location(conn, COQUITLAM) == {
        1001: {0: 2041},
        1003: {5: 6795},
        2802: {0: 500},
    }
    status = catalog_mod.status_for_location(conn, COQUITLAM)
    assert status["catalog_levels"] == 30
    assert status["level_count"] == 30


def test_refresh_records_a_short_catalog_rather_than_claiming_success(tmp_path, monkeypatch):
    """The site's levelCount is the check on our room walk. When a room fails,
    catalog_levels must record what we actually have, so needs_refresh retries."""
    conn = _conn(tmp_path)
    counters, _ = _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10)]),
            "Scan": ScrapeError("no roomInfo — redirected"),
        },
        level_count=30,
    )
    assert counters == {"rooms": 1, "errors": 1}

    status = catalog_mod.status_for_location(conn, COQUITLAM)
    assert status["catalog_levels"] == 10   # not 30
    assert status["level_count"] == 30
    assert catalog_mod.needs_refresh(conn, COQUITLAM, level_count=30, score_changed=False)


def test_failed_room_keeps_its_previous_rows(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    good = {
        "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10)]),
        "Scan": _room(28, "Scan", [(2802, "Spot", 10)], top={(2802, 0): 500}),
    }
    _refresh(conn, monkeypatch, good, level_count=20)

    # Scan now fails; Hoops gains a gamemode.
    _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10), (1003, "Barrage", 10)]),
            "Scan": ScrapeError("no roomInfo — redirected"),
        },
        level_count=30,
    )

    rooms = {r["name"]: r for r in catalog_mod.rooms_for_location(conn, COQUITLAM)}
    assert len(rooms["Hoops"]["games"]) == 2      # refreshed
    assert len(rooms["Scan"]["games"]) == 1       # survived the failure
    assert catalog_mod.top_scores_for_location(conn, COQUITLAM) == {2802: {0: 500}}


def test_one_room_erroring_unexpectedly_does_not_abort_the_walk(tmp_path, monkeypatch):
    """Skipping the rest would also skip the status write, and a stale
    catalog_levels is what suppresses the retry."""
    conn = _conn(tmp_path)
    counters, calls = _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": RuntimeError("something unforeseen"),
            "Scan": _room(28, "Scan", [(2802, "Spot", 10)]),
        },
        level_count=20,
    )

    assert calls == ["Hoops", "Scan"]           # walk continued
    assert counters == {"rooms": 1, "errors": 1}
    assert [r["name"] for r in catalog_mod.rooms_for_location(conn, COQUITLAM)] == ["Scan"]
    assert catalog_mod.status_for_location(conn, COQUITLAM)["catalog_levels"] == 10
    assert catalog_mod.needs_refresh(conn, COQUITLAM, level_count=20, score_changed=False)


def test_refresh_replaces_rather_than_accumulates(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10), (1099, "Retired", 10)],
                           top={(1099, 0): 111}),
            "Scan": _room(28, "Scan", [(2802, "Spot", 10)]),
        },
        level_count=30,
    )
    # 1099 is gone upstream.
    _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10)]),
            "Scan": _room(28, "Scan", [(2802, "Spot", 10)]),
        },
        level_count=20,
    )

    rooms = {r["name"]: r for r in catalog_mod.rooms_for_location(conn, COQUITLAM)}
    assert [g["game_id"] for g in rooms["Hoops"]["games"]] == [1001]
    # A retired gamemode must not leave its top scores behind.
    assert 1099 not in catalog_mod.top_scores_for_location(conn, COQUITLAM)
    assert catalog_mod.status_for_location(conn, COQUITLAM)["catalog_levels"] == 20


def test_refresh_prunes_a_room_the_location_no_longer_has(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10)]),
            "Scan": _room(28, "Scan", [(2802, "Spot", 10)], top={(2802, 0): 500}),
        },
        level_count=20,
    )
    _refresh(
        conn,
        monkeypatch,
        {"Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10)])},
        rooms=[{"id": 10, "name": "Hoops"}],
        level_count=10,
    )

    assert [r["name"] for r in catalog_mod.rooms_for_location(conn, COQUITLAM)] == ["Hoops"]
    assert catalog_mod.top_scores_for_location(conn, COQUITLAM) == {}


# ---------- needs_refresh ----------

def test_needs_refresh_bootstraps_when_never_catalogued(tmp_path):
    conn = _conn(tmp_path)
    assert catalog_mod.needs_refresh(conn, COQUITLAM, level_count=490, score_changed=False)


def test_needs_refresh_only_on_a_score_change_once_complete(tmp_path, monkeypatch):
    """The requested rule: a complete catalog is only re-read when that
    location's score actually moved."""
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10)]),
            "Scan": _room(28, "Scan", [(2802, "Spot", 10)]),
        },
        level_count=20,
    )

    assert not catalog_mod.needs_refresh(conn, COQUITLAM, level_count=20, score_changed=False)
    assert catalog_mod.needs_refresh(conn, COQUITLAM, level_count=20, score_changed=True)


def test_needs_refresh_when_the_site_grew_a_room(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        {
            "Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10)]),
            "Scan": _room(28, "Scan", [(2802, "Spot", 10)]),
        },
        level_count=20,
    )
    # levelCount climbs: a room or gamemode was added upstream.
    assert catalog_mod.needs_refresh(conn, COQUITLAM, level_count=30, score_changed=False)


def test_catalogs_are_isolated_per_location(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        {"Hoops": _room(10, "Hoops", [(1001, "Simon Says", 10)])},
        rooms=[{"id": 10, "name": "Hoops"}],
        level_count=10,
    )
    conn.execute(
        "INSERT INTO location_games (location_id, room_id, room_name, room_order,"
        " game_id, game_name, game_order, levels_json)"
        " VALUES (72, 19, 'Pipes', 0, 1900, 'Piperooni', 0, '[0,1]')"
    )

    assert [r["name"] for r in catalog_mod.rooms_for_location(conn, COQUITLAM)] == ["Hoops"]
    assert [r["name"] for r in catalog_mod.rooms_for_location(conn, 72)] == ["Pipes"]
