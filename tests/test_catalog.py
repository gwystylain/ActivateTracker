import asyncio

from app import catalog as catalog_mod
from app import db as db_mod
from app.config import PollConfig
from app.scraper import LocationInfo, RoomGame, ScrapeError

POLL = PollConfig(jitter_seconds=(0.0, 0.0))

COQUITLAM = 38
HOOPS = {"id": 10, "name": "Hoops"}
SCAN = {"id": 28, "name": "Scan"}


def _conn(tmp_path):
    conn = db_mod.connect(tmp_path / "t.db")
    db_mod.init_schema(conn)
    return conn


def _info(games, *, rooms=(HOOPS, SCAN), location_id=COQUITLAM):
    """games: [(room_id, game_id, name, n_levels)]"""
    return LocationInfo(
        location_id=location_id,
        rooms=tuple(rooms),
        games=tuple(
            RoomGame(game_id=gid, name=gname, levels=tuple(range(n)), order=i, room_id=rid)
            for i, (rid, gid, gname, n) in enumerate(games)
        ),
        level_count=None,
    )


class _FakeSession:
    """Stands in for curl_cffi's AsyncSession; catalog only calls the top-score fetch."""


def _refresh(conn, monkeypatch, info, responses):
    """responses: {room_id: {(game_id, level_id): score} | Exception}"""
    calls = []

    async def fake_top_scores(location_id, room_id, *, session, timeout):
        calls.append(room_id)
        result = responses[room_id]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(catalog_mod.scraper, "fetch_room_top_scores", fake_top_scores)
    counters = asyncio.run(
        catalog_mod.refresh_location(conn, _FakeSession(), info=info, poll_cfg=POLL)
    )
    return counters, calls


HOOPS_AND_SCAN = [
    (10, 1001, "Simon Says", 10),
    (10, 1003, "Barrage", 10),
    (28, 2802, "Spot", 10),
]


def test_refresh_stores_rooms_games_and_top_scores(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    counters, calls = _refresh(
        conn,
        monkeypatch,
        _info(HOOPS_AND_SCAN),
        {10: {(1001, 0): 2041, (1003, 5): 6795}, 28: {(2802, 0): 500}},
    )

    assert counters == {"rooms": 2, "errors": 0}
    assert calls == [10, 28]

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


def test_a_top_score_for_a_game_the_catalog_lacks_is_not_stored(tmp_path, monkeypatch):
    """Nothing would ever clear it: top scores are pruned by the room's games."""
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        _info([(10, 1001, "Simon Says", 10)], rooms=(HOOPS,)),
        {10: {(1001, 0): 2041, (1030, 0): 99}},
    )
    assert catalog_mod.top_scores_for_location(conn, COQUITLAM) == {1001: {0: 2041}}


def test_a_room_with_nothing_co_op_costs_no_request(tmp_path, monkeypatch):
    """/rooms lists a room whose only games are competitive — there is no top
    score to ask for, but it still counts as refreshed."""
    conn = _conn(tmp_path)
    counters, calls = _refresh(
        conn,
        monkeypatch,
        _info([(10, 1001, "Simon Says", 10)], rooms=(HOOPS, SCAN)),
        {10: {}},
    )
    assert calls == [10]
    assert counters == {"rooms": 2, "errors": 0}


def test_refresh_records_a_short_catalog_rather_than_claiming_success(tmp_path, monkeypatch):
    """/games is the check on our room walk. When a room fails, catalog_levels
    must record what we actually have, so needs_refresh retries."""
    conn = _conn(tmp_path)
    info = _info(HOOPS_AND_SCAN)
    counters, _ = _refresh(
        conn, monkeypatch, info, {10: {}, 28: ScrapeError("not JSON")}
    )
    assert counters == {"rooms": 1, "errors": 1}

    status = catalog_mod.status_for_location(conn, COQUITLAM)
    assert status["catalog_levels"] == 20   # not 30
    assert status["level_count"] == 30
    assert catalog_mod.needs_refresh(
        conn, COQUITLAM, expected_levels=info.coop_levels, score_changed=False
    )


def test_failed_room_keeps_its_previous_rows(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        _info([(10, 1001, "Simon Says", 10), (28, 2802, "Spot", 10)]),
        {10: {}, 28: {(2802, 0): 500}},
    )

    # Scan now fails; Hoops gains a gamemode.
    _refresh(
        conn, monkeypatch, _info(HOOPS_AND_SCAN), {10: {}, 28: ScrapeError("not JSON")}
    )

    rooms = {r["name"]: r for r in catalog_mod.rooms_for_location(conn, COQUITLAM)}
    assert len(rooms["Hoops"]["games"]) == 2      # refreshed
    assert len(rooms["Scan"]["games"]) == 1       # survived the failure
    assert catalog_mod.top_scores_for_location(conn, COQUITLAM) == {2802: {0: 500}}


def test_one_room_erroring_unexpectedly_does_not_abort_the_walk(tmp_path, monkeypatch):
    """Skipping the rest would also skip the status write, and a stale
    catalog_levels is what suppresses the retry."""
    conn = _conn(tmp_path)
    info = _info([(10, 1001, "Simon Says", 10), (28, 2802, "Spot", 10)])
    counters, calls = _refresh(
        conn, monkeypatch, info, {10: RuntimeError("something unforeseen"), 28: {}}
    )

    assert calls == [10, 28]                    # walk continued
    assert counters == {"rooms": 1, "errors": 1}
    assert [r["name"] for r in catalog_mod.rooms_for_location(conn, COQUITLAM)] == ["Scan"]
    assert catalog_mod.status_for_location(conn, COQUITLAM)["catalog_levels"] == 10
    assert catalog_mod.needs_refresh(
        conn, COQUITLAM, expected_levels=info.coop_levels, score_changed=False
    )


def test_refresh_replaces_rather_than_accumulates(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        _info([(10, 1001, "Simon Says", 10), (10, 1099, "Retired", 10), (28, 2802, "Spot", 10)]),
        {10: {(1099, 0): 111}, 28: {}},
    )
    # 1099 is gone upstream.
    _refresh(
        conn,
        monkeypatch,
        _info([(10, 1001, "Simon Says", 10), (28, 2802, "Spot", 10)]),
        {10: {}, 28: {}},
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
        _info([(10, 1001, "Simon Says", 10), (28, 2802, "Spot", 10)]),
        {10: {}, 28: {(2802, 0): 500}},
    )
    _refresh(
        conn,
        monkeypatch,
        _info([(10, 1001, "Simon Says", 10)], rooms=(HOOPS,)),
        {10: {}},
    )

    assert [r["name"] for r in catalog_mod.rooms_for_location(conn, COQUITLAM)] == ["Hoops"]
    assert catalog_mod.top_scores_for_location(conn, COQUITLAM) == {}


# ---------- needs_refresh ----------

def test_needs_refresh_bootstraps_when_never_catalogued(tmp_path):
    conn = _conn(tmp_path)
    assert catalog_mod.needs_refresh(conn, COQUITLAM, expected_levels=490, score_changed=False)


def test_needs_refresh_only_on_a_score_change_once_complete(tmp_path, monkeypatch):
    """The requested rule: a complete catalog is only re-read when that
    location's score actually moved."""
    conn = _conn(tmp_path)
    info = _info([(10, 1001, "Simon Says", 10), (28, 2802, "Spot", 10)])
    _refresh(conn, monkeypatch, info, {10: {}, 28: {}})

    assert not catalog_mod.needs_refresh(conn, COQUITLAM, expected_levels=20, score_changed=False)
    assert catalog_mod.needs_refresh(conn, COQUITLAM, expected_levels=20, score_changed=True)


def test_needs_refresh_when_the_site_grew_a_room(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        _info([(10, 1001, "Simon Says", 10), (28, 2802, "Spot", 10)]),
        {10: {}, 28: {}},
    )
    # /games lists more co-op levels: a room or gamemode was added upstream.
    assert catalog_mod.needs_refresh(conn, COQUITLAM, expected_levels=30, score_changed=False)


def test_catalogs_are_isolated_per_location(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _refresh(
        conn,
        monkeypatch,
        _info([(10, 1001, "Simon Says", 10)], rooms=(HOOPS,)),
        {10: {}},
    )
    conn.execute(
        "INSERT INTO location_games (location_id, room_id, room_name, room_order,"
        " game_id, game_name, game_order, levels_json)"
        " VALUES (72, 19, 'Pipes', 0, 1900, 'Piperooni', 0, '[0,1]')"
    )

    assert [r["name"] for r in catalog_mod.rooms_for_location(conn, COQUITLAM)] == ["Hoops"]
    assert [r["name"] for r in catalog_mod.rooms_for_location(conn, 72)] == ["Pipes"]
