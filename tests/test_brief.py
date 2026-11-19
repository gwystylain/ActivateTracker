import json
import random
from datetime import date
from types import SimpleNamespace

from app import brief
from app import db as db_mod
from app.routes.public import _build_records, player_brief

TODAY = date(2026, 11, 19)


def _conn(tmp_path):
    conn = db_mod.connect(tmp_path / "t.db")
    db_mod.init_schema(conn)
    return conn


def _player(conn, handle, display_name=None, hidden=0) -> int:
    pid = conn.execute(
        "INSERT INTO players (handle, display_name, hidden) VALUES (?, ?, ?)",
        (handle, display_name, hidden),
    ).lastrowid
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug) VALUES (?, 38, 'coquitlam')",
        (pid,),
    )
    return pid


def _visit(conn, pid, day, delta, loc=38):
    conn.execute(
        "INSERT INTO visits (player_id, location_id, visit_date, score_delta) VALUES (?, ?, ?, ?)",
        (pid, loc, day, delta),
    )


def _badge(conn, pid, bid, name, earned_on):
    conn.execute(
        "INSERT OR IGNORE INTO badges (badge_id, name, description, first_seen, last_seen)"
        " VALUES (?, ?, 'x', '2026-11-01', '2026-11-01')",
        (bid, name),
    )
    conn.execute(
        "INSERT INTO player_badges (player_id, badge_id, earned, earned_on, updated_at)"
        " VALUES (?, ?, 1, ?, '2026-11-19')",
        (pid, bid, earned_on),
    )


def _rows(conn):
    return conn.execute(
        "SELECT id, handle, display_name, initial_streak, initial_streak_set_at"
        " FROM players ORDER BY id"
    ).fetchall()


def _gather(conn):
    return brief.gather(conn, _rows(conn), records=_build_records(conn), today=TODAY)


def _seed_record(conn, pid):
    """Hoops Barrage level 1 at Coquitlam: below the top on Nov 10, at it on Nov 17."""
    conn.execute(
        "INSERT INTO location_games (location_id, room_id, room_name, room_order,"
        " game_id, game_name, game_order, levels_json)"
        " VALUES (38, 10, 'Hoops', 0, 1003, 'Barrage', 0, '[0, 1]')"
    )
    conn.execute(
        "INSERT INTO location_top_scores (location_id, game_id, level_id, top_score)"
        " VALUES (38, 1003, 0, 5000)"
    )
    for polled, high, act in (("2026-11-10T11:00:00", 4000, "2026-11-09"),
                              ("2026-11-18T11:00:00", 5000, "2026-11-17")):
        conn.execute(
            "INSERT INTO score_snapshots (player_id, location_id, polled_at, activity_date,"
            " total_score, yearly_score, raw_scores_json) VALUES (?, 38, ?, ?, 0, 0, ?)",
            (pid, polled, act, json.dumps([{"gameId": 1003, "levelId": 0, "highScore": high}])),
        )


def test_last_session_collects_points_badges_and_records_from_the_same_day(tmp_path):
    conn = _conn(tmp_path)
    pid = _player(conn, "ace123", "Ace")
    _visit(conn, pid, "2026-11-09", 3000)
    _visit(conn, pid, "2026-11-17", 1200)
    _visit(conn, pid, "2026-11-17", 800, loc=72)   # same day elsewhere: one session
    _badge(conn, pid, 1, "Activ8", "2026-11-17")
    _badge(conn, pid, 2, "Deja Vu", "2026-11-09")
    _badge(conn, pid, 3, "Old News", None)          # held since before tracking
    _seed_record(conn, pid)

    (b,) = _gather(conn)

    assert b.name == "Ace"
    assert b.days_since == 2
    assert b.last_session.day == date(2026, 11, 17)
    assert b.last_session.points == 2000
    assert b.last_session.badges == ["Activ8"]
    assert b.last_session.records == ["Hoops Barrage L1 @ Coquitlam"]
    assert (b.window_points, b.window_visits, b.window_badges, b.window_records) == (5000, 2, 2, 1)
    assert b.records_held == 1
    assert not b.slacking


def test_a_week_away_is_slacking_even_after_a_big_month(tmp_path):
    conn = _conn(tmp_path)
    pid = _player(conn, "z", "Zed")
    _visit(conn, pid, "2026-11-12", 9000)   # exactly SLACK_DAYS ago
    (b,) = _gather(conn)
    assert b.days_since == brief.SLACK_DAYS
    assert b.slacking


def test_render_names_leaders_against_slackers_by_display_name(tmp_path):
    conn = _conn(tmp_path)
    a = _player(conn, "xx_handle_a", "Ace")
    b = _player(conn, "xx_handle_b", "Bee")
    z = _player(conn, "xx_handle_z", "Zed")
    _visit(conn, a, "2026-11-17", 4000)
    _visit(conn, b, "2026-11-18", 1000)
    _visit(conn, z, "2026-10-27", 500)

    text = brief.render(_gather(conn), rng=random.Random(0))
    headline, _, *lines = text.split("\n")

    assert "**Ace** and **Bee**" in headline and "**Zed**" in headline
    assert lines[0].startswith("**Ace**")          # leaders by points
    assert lines[1].startswith("**Bee**")
    assert lines[2].startswith("**Zed**") and "23 days" in lines[2]
    assert "Meanwhile **Ace** banked 4,000 points" in lines[2]
    assert "xx_handle" not in text


def test_a_player_never_seen_is_called_out(tmp_path):
    conn = _conn(tmp_path)
    _player(conn, "ghost", "Ghost")
    (b,) = _gather(conn)
    assert b.days_since is None and b.slacking
    assert "no visit on record" in brief.render([b])


def test_render_fits_one_discord_message(tmp_path):
    conn = _conn(tmp_path)
    for i in range(40):
        pid = _player(conn, f"p{i}", f"Player number {i}")
        _visit(conn, pid, "2026-11-18", 1000 + i)
    assert len(brief.render(_gather(conn))) <= brief.DISCORD_LIMIT


async def _call(conn, players="", days=30):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                db=conn,
                config=SimpleNamespace(poll=SimpleNamespace(timezone="America/Vancouver")),
            )
        )
    )
    resp = await player_brief(request, players=players, days=days)
    return resp.status_code, json.loads(resp.body)


async def test_endpoint_resolves_display_names_handles_and_ids_ignoring_case(tmp_path):
    conn = _conn(tmp_path)
    a = _player(conn, "acehandle, alt_ace", "Ace")
    b = _player(conn, "beehandle", "Bee")
    c = _player(conn, "seehandle", "See")

    status, body = await _call(conn, f"ace, ALT_ACE, BeeHandle, {c}")
    assert status == 200
    assert [p["id"] for p in body["players"]] == [a, b, c]   # Ace named twice, listed once
    assert body["players"][0]["name"] == "Ace"
    assert isinstance(body["text"], str) and body["text"]


async def test_endpoint_names_the_players_it_doesnt_know(tmp_path):
    conn = _conn(tmp_path)
    _player(conn, "acehandle", "Ace")
    _player(conn, "hidden1", "Shy", hidden=1)

    status, body = await _call(conn, "Ace, Nobody, Shy")
    assert status == 404
    assert body["unknown"] == ["Nobody", "Shy"]


async def test_endpoint_without_players_covers_everyone_visible(tmp_path):
    conn = _conn(tmp_path)
    _player(conn, "a", "Ace")
    _player(conn, "b", "Bee")
    _player(conn, "s", "Shy", hidden=1)

    status, body = await _call(conn)
    assert status == 200
    assert sorted(p["name"] for p in body["players"]) == ["Ace", "Bee"]
