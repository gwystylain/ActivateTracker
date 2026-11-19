import json
from datetime import date
from types import SimpleNamespace

from app import brief, rankings
from app import db as db_mod
from app.routes.public import gamemode_rankings

TRIVIAL = 1002


def _conn(tmp_path):
    conn = db_mod.connect(tmp_path / "t.db")
    db_mod.init_schema(conn)
    return conn


def _catalog(conn, location_id, *, with_trivial=True):
    games = [(location_id, 10, "Hoops", 0, 1003, "Barrage", 0, "[0, 1, 2]")]
    if with_trivial:
        games.append((location_id, 10, "Hoops", 0, TRIVIAL, "Trivial", 1, "[0, 1, 2]"))
    games.append((location_id, 28, "Scan", 1, 2802, "Trivia Night", 0, "[0]"))
    conn.executemany(
        "INSERT INTO location_games (location_id, room_id, room_name, room_order,"
        " game_id, game_name, game_order, levels_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        games,
    )


def _top(conn, location_id, level_id, score, game_id=TRIVIAL):
    conn.execute(
        "INSERT INTO location_top_scores (location_id, game_id, level_id, top_score)"
        " VALUES (?, ?, ?, ?)",
        (location_id, game_id, level_id, score),
    )


def _player(conn, handle, name, *, hidden=0):
    pid = conn.execute(
        "INSERT INTO players (handle, display_name, hidden) VALUES (?, ?, ?)",
        (handle, name, hidden),
    ).lastrowid
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 72, 'langley'), (?, 38, 'coquitlam')",
        (pid, pid),
    )
    return pid


def _scores(conn, pid, loc, scores, polled_at="2026-11-18T11:00:00"):
    """scores: {(game_id, level_id): high}"""
    raw = [{"gameId": g, "levelId": lvl, "highScore": s} for (g, lvl), s in scores.items()]
    conn.execute(
        "INSERT INTO score_snapshots (player_id, location_id, polled_at, total_score,"
        " yearly_score, raw_scores_json) VALUES (?, ?, ?, 0, 0, ?)",
        (pid, loc, polled_at, json.dumps(raw)),
    )


# ---------- find_game ----------

CATALOG = [
    {"room": "Hoops", "name": "Trivial", "game_id": 1002, "levels": [0]},
    {"room": "Hoops", "name": "Barrage", "game_id": 1003, "levels": [0]},
    {"room": "Hoops", "name": "Bop", "game_id": 1005, "levels": [0]},
    {"room": "Scan", "name": "Trivia", "game_id": 2802, "levels": [0]},
]


def test_find_game_takes_the_sites_trivial_for_trivia_in_hoops():
    assert rankings.find_game(CATALOG, "hoops", "Trivia")["game_id"] == 1002
    assert rankings.find_game(CATALOG, "HOOPS", "trivial")["game_id"] == 1002


def test_find_game_stays_in_the_named_room():
    assert rankings.find_game(CATALOG, "Scan", "trivia")["game_id"] == 2802
    assert rankings.find_game(CATALOG, "Grid", "trivia") is None


def test_find_game_refuses_a_prefix_two_gamemodes_share():
    assert rankings.find_game(CATALOG, "Hoops", "b") is None
    assert rankings.find_game(CATALOG, "Hoops", "bo")["game_id"] == 1005


# ---------- rank ----------

def test_rank_sums_the_best_per_level_and_counts_records():
    players = [
        {"id": 1, "name": "Ace", "beaten": {(TRIVIAL, 0): 3000, (TRIVIAL, 1): 5000,
                                             (1003, 0): 99_999}},       # other gamemode
        {"id": 2, "name": "Bee", "beaten": {(TRIVIAL, 0): 2000, (TRIVIAL, 9): 70_000}},  # level 9 not listed
        {"id": 3, "name": "Cee", "beaten": {(1003, 0): 10}},
    ]
    standings, never = rankings.rank(TRIVIAL, [0, 1, 2], players, {0: 3000, 1: 6000})

    ace, bee = standings
    assert (ace.name, ace.rank, ace.total, ace.levels_scored, ace.records) == ("Ace", 1, 8000, 2, 1)
    assert ace.levels == {1: 3000, 2: 5000}                  # 1-based, like the site
    assert (bee.name, bee.rank, bee.total, bee.levels_scored, bee.records) == ("Bee", 2, 2000, 1, 0)
    assert never == ["Cee"]


def test_ties_share_a_rank_and_the_next_one_skips():
    players = [
        {"id": i, "name": n, "beaten": {(TRIVIAL, 0): s}}
        for i, (n, s) in enumerate([("Ace", 500), ("Bee", 500), ("Cee", 100)])
    ]
    standings, _ = rankings.rank(TRIVIAL, [0], players, {})
    assert [(s.name, s.rank) for s in standings] == [("Ace", 1), ("Bee", 1), ("Cee", 3)]


# ---------- render ----------

def _loc(name, standings=(), never=(), game_id=TRIVIAL):
    return rankings.LocationRanking(1, name, game_id, "Trivial", 10, list(standings), list(never))


def _st(rank, name, total, levels=10, records=0):
    return rankings.Standing(id=rank, name=name, rank=rank, total=total,
                             levels_scored=levels, records=records)


def test_render_has_a_section_per_location_with_medals():
    text = rankings.render("Hoops", "Trivial", [
        _loc("Langley", [_st(1, "Ace", 52_340, records=3), _st(2, "Bee", 48_000, 9),
                         _st(3, "Cee", 9_000, 2), _st(4, "Dee", 400, 1)], never=["Eve"]),
        _loc("Coquitlam", game_id=None),
    ], today=date(2026, 11, 19))

    assert text.split("\n")[:2] == [
        "# 🏆 Hoops Trivial rankings",
        "-# best score on each level, added up · as of Nov 19",
    ]
    assert "## Langley\n🥇 **Ace** · 52k · 10/10 levels · 👑 3\n🥈 **Bee** · 48k · 9/10 levels" in text
    assert "4. **Dee** · 400 · 1/10 levels" in text
    assert "-# Never played: **Eve**" in text
    assert "## Coquitlam\n-# No Trivial here." in text


def test_render_fits_one_discord_message_cutting_whole_lines():
    many = [_st(i + 1, f"Player with a long name {i}", 1000 * (200 - i)) for i in range(120)]
    text = rankings.render("Hoops", "Trivial", [_loc("Langley", many)])
    assert len(text) <= brief.DISCORD_LIMIT
    assert text.endswith("\n…")
    rows = text.split("\n")
    assert rows[3] == "## Langley"
    assert all(ln.endswith("levels") for ln in rows[4:-1])


# ---------- endpoint ----------

async def _call(conn, **params):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db=conn, config=SimpleNamespace(poll=SimpleNamespace(timezone="America/Vancouver")),
    )))
    defaults = dict(room=rankings.DEFAULT_ROOM, game=rankings.DEFAULT_GAME, players="", location="")
    resp = await gamemode_rankings(request, **{**defaults, **params})
    return resp.status_code, json.loads(resp.body)


def _seed(conn):
    _catalog(conn, 72)
    _catalog(conn, 38)
    _top(conn, 72, 0, 3000)
    ace = _player(conn, "ace_h", "Ace")
    bee = _player(conn, "bee_h", "Bee")
    _player(conn, "shy_h", "Shy", hidden=1)
    _scores(conn, ace, 72, {(TRIVIAL, 0): 3000, (TRIVIAL, 1): 4000})
    _scores(conn, bee, 72, {(TRIVIAL, 0): 2500})
    _scores(conn, ace, 38, {(1003, 0): 500})
    _scores(conn, bee, 38, {(TRIVIAL, 2): 9000})
    return ace, bee


async def test_defaults_to_hoops_trivia_at_every_tracked_location(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    status, body = await _call(conn)

    assert status == 200
    assert (body["room"], body["game"]) == ("Hoops", "Trivial")
    by_name = {loc["name"]: loc for loc in body["locations"]}
    assert set(by_name) == {"Langley", "Coquitlam"}
    langley = by_name["Langley"]
    assert [(s["name"], s["rank"], s["total"], s["records"]) for s in langley["standings"]] == [
        ("Ace", 1, 7000, 1), ("Bee", 2, 2500, 0),
    ]
    assert langley["standings"][0]["levels"] == {"1": 3000, "2": 4000}
    coquitlam = by_name["Coquitlam"]
    assert [s["name"] for s in coquitlam["standings"]] == ["Bee"]
    assert coquitlam["never_played"] == ["Ace"]
    assert "Shy" not in body["text"]
    assert "## Coquitlam" in body["text"] and "## Langley" in body["text"]


async def test_players_and_location_narrow_it(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    status, body = await _call(conn, players="bee", location="Langley")

    assert status == 200
    (loc,) = body["locations"]
    assert loc["name"] == "Langley"
    assert [s["name"] for s in loc["standings"]] == ["Bee"] and loc["never_played"] == []


async def test_a_location_without_the_gamemode_says_so(tmp_path):
    conn = _conn(tmp_path)
    _catalog(conn, 72)
    _catalog(conn, 38, with_trivial=False)
    _player(conn, "ace_h", "Ace")

    status, body = await _call(conn)
    assert status == 200
    coquitlam = next(loc for loc in body["locations"] if loc["name"] == "Coquitlam")
    assert coquitlam["game_id"] is None
    assert "No Trivial here." in body["text"]


async def test_unknown_names_are_404s(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    assert (await _call(conn, game="Nonsense"))[0] == 404
    assert (await _call(conn, location="Kelowna"))[0] == 404
    status, body = await _call(conn, players="Ace, Shy")
    assert status == 404 and body["unknown"] == ["Shy"]
