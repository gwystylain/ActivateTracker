import json
from datetime import date
from types import SimpleNamespace

from app import db as db_mod
from app.routes.public import (
    _build_player_summaries,
    _build_records,
    badge_data,
    chart_data,
    game_data,
)

TODAY = date(2026, 8, 3)


def _conn(tmp_path):
    conn = db_mod.connect(tmp_path / "t.db")
    db_mod.init_schema(conn)
    return conn


def _seed_player(conn, handle="gmebagholder") -> int:
    cur = conn.execute("INSERT INTO players (handle) VALUES (?)", (handle,))
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO player_locations (player_id, location_id, slug)"
        " VALUES (?, 72, 'langley'), (?, 38, 'coquitlam')",
        (pid, pid),
    )
    return pid


def _snap(conn, pid, loc, polled_at, *, position, beat, count=470, total=1000):
    """`position` is playerLocation.standing — the page's "Your Leaderboard
    Position", not the playerRank badge (which the dashboard doesn't show)."""
    conn.execute(
        "INSERT INTO score_snapshots (player_id, location_id, polled_at, total_score,"
        " yearly_score, leaderboard_position, levels_beat, level_count,"
        " raw_scores_json)"
        " VALUES (?, ?, ?, ?, 0, ?, ?, ?, '[]')",
        (pid, loc, polled_at, total, position, beat, count),
    )


def test_summary_surfaces_best_position_and_top_levels_beat(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", position=321, beat=40)
    _snap(conn, pid, 38, "2026-08-01T00:00:00", position=138, beat=25, count=300)

    (p,) = _build_player_summaries(conn, today=TODAY)

    # Header: best leaderboard position is the lowest number, levels beat is the
    # highest — carrying that location's own level total.
    assert p["position"] == 138
    assert p["levels_beat"] == 40
    assert p["level_count"] == 470

    by_name = {loc["name"]: loc for loc in p["locations"]}
    assert by_name["Langley"]["position"] == 321
    assert by_name["Langley"]["levels_beat"] == 40
    assert by_name["Coquitlam"]["position"] == 138
    assert by_name["Coquitlam"]["levels_beat"] == 25
    assert by_name["Coquitlam"]["level_count"] == 300


def test_summary_position_is_none_when_never_observed(tmp_path):
    """Snapshots predating the leaderboard_position column carry NULL, and NULL
    means "not observed" — never a substitute number from another field."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    conn.execute(
        "INSERT INTO score_snapshots (player_id, location_id, polled_at, total_score,"
        " yearly_score, player_rank, raw_scores_json)"
        " VALUES (?, 72, '2026-08-01T00:00:00', 5, 0, 8, '[]')",
        (pid,),
    )

    (p,) = _build_player_summaries(conn, today=TODAY)
    assert p["position"] is None
    assert all(loc["position"] is None for loc in p["locations"])


def test_summary_uses_latest_snapshot_per_location(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-07-01T00:00:00", position=400, beat=10)
    _snap(conn, pid, 72, "2026-08-02T00:00:00", position=321, beat=31)

    (p,) = _build_player_summaries(conn, today=TODAY)
    langley = next(loc for loc in p["locations"] if loc["name"] == "Langley")
    assert langley["position"] == 321
    assert langley["levels_beat"] == 31


def test_summary_without_snapshots_reports_none(tmp_path):
    conn = _conn(tmp_path)
    _seed_player(conn)

    (p,) = _build_player_summaries(conn, today=TODAY)
    assert p["position"] is None
    assert p["levels_beat"] is None
    assert p["level_count"] is None
    assert all(loc["position"] is None for loc in p["locations"])


async def _chart(conn):
    """chart_data only touches request.app.state.db, so a stub stands in."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=conn)))
    return json.loads((await chart_data(request)).body)


async def test_chart_emits_both_metrics_per_point(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", position=1, beat=1, total=100)
    _snap(conn, pid, 38, "2026-08-01T00:00:00", position=1, beat=1, total=500)

    (player,) = (await _chart(conn))["players"]
    (pt,) = player["points"]
    assert pt["top_score"] == 500     # best single location
    assert pt["total_score"] == 600   # sum across locations
    assert pt["locations"] == {"langley": 100, "coquitlam": 500}


async def test_chart_emits_day_when_only_the_sum_moves(tmp_path):
    """A gain at a non-leading location leaves the max flat. The day must still
    be emitted or the 'all locations' view would lose it entirely."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", position=1, beat=1, total=100)
    _snap(conn, pid, 38, "2026-08-01T00:00:00", position=1, beat=1, total=500)
    # Langley climbs but stays below coquitlam: max unchanged, sum up.
    _snap(conn, pid, 72, "2026-08-02T00:00:00", position=1, beat=1, total=300)

    (player,) = (await _chart(conn))["players"]
    # Dates are the play days, one behind the polls that observed them.
    assert [(p["date"], p["top_score"], p["total_score"]) for p in player["points"]] == [
        ("2026-07-31", 500, 600),
        ("2026-08-01", 500, 800),
    ]


async def test_chart_drops_days_where_neither_metric_moves(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", position=1, beat=1, total=100)
    _snap(conn, pid, 72, "2026-08-02T00:00:00", position=1, beat=1, total=100)  # no change
    _snap(conn, pid, 72, "2026-08-03T00:00:00", position=1, beat=1, total=180)

    (player,) = (await _chart(conn))["players"]
    assert [p["date"] for p in player["points"]] == ["2026-07-31", "2026-08-02"]


async def test_chart_dates_points_to_the_day_of_play(tmp_path):
    """The x-axis must agree with the visit dates the dashboard counts: the
    score seen by the Aug 7 poll was earned on Aug 6 (the site's refresh lag),
    so the point belongs on Aug 6."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-07T11:00:03+00:00", position=1, beat=1, total=100)

    (player,) = (await _chart(conn))["players"]
    assert [p["date"] for p in player["points"]] == ["2026-08-06"]


async def test_chart_excludes_hidden_players(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", position=1, beat=1, total=100)
    conn.execute("UPDATE players SET hidden = 1 WHERE id = ?", (pid,))

    assert (await _chart(conn))["players"] == []


# ---------- /api/game-data ----------

def _seed_catalog(conn, location_id=72):
    conn.executemany(
        "INSERT INTO location_games (location_id, room_id, room_name, room_order,"
        " game_id, game_name, game_order, levels_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (location_id, 10, "Hoops", 0, 1003, "Barrage", 0, "[0, 1, 2]"),
            (location_id, 10, "Hoops", 0, 1001, "Simon Says", 1, "[0, 1, 2]"),
            (location_id, 28, "Scan", 1, 2802, "Spot", 0, "[0, 1]"),
        ],
    )
    conn.executemany(
        "INSERT INTO location_top_scores (location_id, game_id, level_id, top_score)"
        " VALUES (?, ?, ?, ?)",
        [(location_id, 1003, 0, 2062), (location_id, 1003, 1, 3046)],
    )
    conn.execute(
        "INSERT INTO location_catalog (location_id, level_count, catalog_levels, fetched_at)"
        " VALUES (?, 8, 8, '2026-08-03T00:00:00+00:00')",
        (location_id,),
    )


def _snap_scores(conn, pid, loc, polled_at, scores, total=1000):
    conn.execute(
        "INSERT INTO score_snapshots (player_id, location_id, polled_at, total_score,"
        " yearly_score, raw_scores_json) VALUES (?, ?, ?, ?, 0, ?)",
        (pid, loc, polled_at, total, json.dumps(scores)),
    )


async def _game_data(conn, location_id=None):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=conn)))
    return json.loads((await game_data(request, location_id=location_id)).body)


async def test_game_data_returns_catalog_and_sparse_beaten_levels(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn)
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00", [
        {"gameId": 1003, "levelId": 0, "highScore": 2000},
        {"gameId": 1003, "levelId": 1, "highScore": 2500},
    ])

    payload = await _game_data(conn, 72)

    assert [r["name"] for r in payload["rooms"]] == ["Hoops", "Scan"]
    # Gamemodes keep the site's own order within the room, not id order.
    assert [g["name"] for g in payload["rooms"][0]["games"]] == ["Barrage", "Simon Says"]
    assert payload["rooms"][0]["games"][0]["levels"] == [0, 1, 2]
    assert payload["catalog"]["catalog_levels"] == 8

    # Only beaten levels are sent; everything absent is a "No score".
    assert payload["scores"][str(pid)] == {"1003": {"0": 2000, "1": 2500}}
    assert payload["top_scores"] == {"1003": {"0": 2062, "1": 3046}}
    (player,) = payload["players"]
    assert player["levels_beat"] == 2


async def test_game_data_treats_a_zero_high_score_as_no_score(tmp_path):
    """Same rule as levels_beat: a zero entry is a level the player hasn't beaten."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn)
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00", [
        {"gameId": 1003, "levelId": 0, "highScore": 0},
        {"gameId": 1003, "levelId": 1, "highScore": 900},
    ])

    payload = await _game_data(conn, 72)
    assert payload["scores"][str(pid)] == {"1003": {"1": 900}}
    assert payload["players"][0]["levels_beat"] == 1


async def test_game_data_uses_only_the_newest_snapshot(tmp_path):
    """Historic per-level data has no value here — only the current run counts."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn)
    _snap_scores(conn, pid, 72, "2026-07-01T00:00:00", [
        {"gameId": 1003, "levelId": 0, "highScore": 100},
    ])
    _snap_scores(conn, pid, 72, "2026-08-02T00:00:00", [
        {"gameId": 1003, "levelId": 0, "highScore": 2000},
        {"gameId": 1001, "levelId": 2, "highScore": 4000},
    ])

    payload = await _game_data(conn, 72)
    assert payload["scores"][str(pid)] == {"1003": {"0": 2000}, "1001": {"2": 4000}}


async def test_game_data_scopes_to_one_location(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn, location_id=72)
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 0, "highScore": 111}])
    _snap_scores(conn, pid, 38, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 1, "highScore": 222}])

    langley = await _game_data(conn, 72)
    coquitlam = await _game_data(conn, 38)

    assert langley["scores"][str(pid)] == {"1003": {"0": 111}}
    assert coquitlam["scores"][str(pid)] == {"1003": {"1": 222}}
    # Coquitlam was never catalogued: the page has to cope with an empty one.
    assert coquitlam["rooms"] == []
    assert coquitlam["catalog"] is None


async def test_game_data_defaults_to_the_first_tracked_location(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap_scores(conn, pid, 38, "2026-08-01T00:00:00", [])

    payload = await _game_data(conn)
    # _tracked_locations orders by slug, so coquitlam leads langley.
    assert payload["location_id"] == 38
    assert [loc["name"] for loc in payload["locations"]] == ["Coquitlam", "Langley"]


async def test_game_data_excludes_hidden_players(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn)
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 0, "highScore": 111}])
    conn.execute("UPDATE players SET hidden = 1 WHERE id = ?", (pid,))

    payload = await _game_data(conn, 72)
    assert payload["players"] == []
    assert payload["scores"] == {}
    assert payload["locations"] == []


async def test_game_data_with_no_players_at_all(tmp_path):
    conn = _conn(tmp_path)
    payload = await _game_data(conn)
    assert payload["location_id"] is None
    assert payload["rooms"] == []
    assert payload["players"] == []


async def test_game_data_carries_each_gamemodes_reference_data(tmp_path):
    """Master document text and figures, keyed by room and gamemode name."""
    conn = _conn(tmp_path)
    _seed_player(conn)
    _seed_catalog(conn)

    payload = await _game_data(conn, 72)

    by_room = {r["name"]: {g["name"]: g for g in r["games"]} for r in payload["rooms"]}
    hoops = by_room["Hoops"]
    assert hoops["Barrage"]["description"].startswith("Sink the required number of baskets")
    assert hoops["Simon Says"]["description"].startswith("Recreate the sequence of colours")
    assert by_room["Scan"]["Spot"]["description"].startswith("Spot the difference.")

    # Barrage plays best as a pair, Simon Says as a five.
    assert hoops["Barrage"]["optimal_players"] == 2
    assert hoops["Simon Says"]["optimal_players"] == 5
    assert hoops["Barrage"]["optimal_disputed"] is False


async def test_game_data_sends_nulls_for_an_uncovered_gamemode(tmp_path):
    """The document lags the site; an unknown gamemode renders with neither."""
    conn = _conn(tmp_path)
    _seed_player(conn)
    conn.execute(
        "INSERT INTO location_games (location_id, room_id, room_name, room_order,"
        " game_id, game_name, game_order, levels_json)"
        " VALUES (72, 10, 'Hoops', 0, 1099, 'Brand New Mode', 0, '[0]')"
    )

    payload = await _game_data(conn, 72)
    game = payload["rooms"][0]["games"][0]
    assert game["description"] is None
    assert game["optimal_players"] is None
    assert game["optimal_disputed"] is False


# ---------- dashboard: records held ----------
# _seed_catalog's top scores are 1003/0 = 2062 and 1003/1 = 3046, and nothing
# for 1001 or 2802 — the sparse shape a real location has.

def test_records_are_the_levels_where_the_player_matches_the_top_score(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn)
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00", [
        {"gameId": 1003, "levelId": 0, "highScore": 2062},   # equals the top: held
        {"gameId": 1003, "levelId": 1, "highScore": 3000},   # under it: not held
        {"gameId": 1001, "levelId": 0, "highScore": 9999},   # nobody has scored it: not held
    ])

    (player,) = _build_records(conn)

    assert player["id"] == pid
    assert player["total"] == 1
    assert player["rows"] == [
        {
            "location": "Langley",
            "room": "Hoops",
            "game": "Barrage",
            "held": 1,
            "level_count": 3,
            "levels": [1],   # level ids are 0-based; the site numbers from 1
        }
    ]


def test_records_count_for_both_players_on_a_tie(tmp_path):
    conn = _conn(tmp_path)
    ed = _seed_player(conn)
    sam = _seed_player(conn, handle="hoopsfan")
    _seed_catalog(conn)
    for pid in (ed, sam):
        _snap_scores(conn, pid, 72, "2026-08-01T00:00:00",
                     [{"gameId": 1003, "levelId": 0, "highScore": 2062}])

    records = _build_records(conn)
    assert [p["handle"] for p in records] == ["gmebagholder", "hoopsfan"]
    assert [p["total"] for p in records] == [1, 1]


def test_records_include_a_score_above_a_stale_top(tmp_path):
    """Room pages are re-walked on a throttle, so a player who just beat the
    board reads higher than the recorded top until the next catalog refresh."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn)
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 1, "highScore": 5000}])   # top is 3046

    (player,) = _build_records(conn)
    assert player["rows"][0]["levels"] == [2]


def test_records_span_locations_and_use_the_newest_snapshot(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn, location_id=72)
    _seed_catalog(conn, location_id=38)
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 0, "highScore": 2062}])
    # Coquitlam: an older snapshot held a record, the current one doesn't.
    _snap_scores(conn, pid, 38, "2026-07-01T00:00:00",
                 [{"gameId": 1003, "levelId": 1, "highScore": 3046}])
    _snap_scores(conn, pid, 38, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 1, "highScore": 3046},
                  {"gameId": 1003, "levelId": 0, "highScore": 2062}])

    (player,) = _build_records(conn)
    assert [(r["location"], r["levels"]) for r in player["rows"]] == [
        ("Coquitlam", [1, 2]),
        ("Langley", [1]),
    ]
    assert player["total"] == 3


def test_records_omit_players_and_locations_with_nothing_held(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn, location_id=72)          # coquitlam is never catalogued
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 0, "highScore": 500}])
    _snap_scores(conn, pid, 38, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 0, "highScore": 99999}])

    assert _build_records(conn) == []


def test_records_exclude_hidden_players(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_catalog(conn)
    _snap_scores(conn, pid, 72, "2026-08-01T00:00:00",
                 [{"gameId": 1003, "levelId": 0, "highScore": 2062}])
    conn.execute("UPDATE players SET hidden = 1 WHERE id = ?", (pid,))

    assert _build_records(conn) == []


def test_summary_ignores_locations_with_null_columns(tmp_path):
    """Rows written before a migration have NULLs — they must not crash the
    max()/min() or masquerade as a real value."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    conn.execute(
        "INSERT INTO score_snapshots (player_id, location_id, polled_at, total_score,"
        " yearly_score, raw_scores_json) VALUES (?, 72, '2026-08-01T00:00:00', 5, 0, '[]')",
        (pid,),
    )
    _snap(conn, pid, 38, "2026-08-01T00:00:00", position=138, beat=12)

    (p,) = _build_player_summaries(conn, today=TODAY)
    assert p["position"] == 138
    assert p["levels_beat"] == 12
    by_name = {loc["name"]: loc for loc in p["locations"]}
    assert by_name["Langley"]["position"] is None   # NULL stays an em dash
    assert by_name["Coquitlam"]["position"] == 138


# ---------- badges ----------

async def _badges(conn):
    """badge_data only touches request.app.state.db, so a stub stands in."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=conn)))
    return json.loads((await badge_data(request)).body)


def _seed_badges(conn, pid, *specs, earned_on=None):
    """(badge_id, earned, progress, total) tuples."""
    for bid, earned, progress, total in specs:
        conn.execute(
            "INSERT OR IGNORE INTO badges (badge_id, name, description, stars,"
            " first_seen, last_seen)"
            " VALUES (?, ?, ?, 5, '2026-08-01T00:00:00', '2026-08-01T00:00:00')",
            (bid, f"Badge {bid}", f"do thing {bid}"),
        )
        conn.execute(
            "INSERT INTO player_badges (player_id, badge_id, earned, progress,"
            " total_progress, earned_on, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, '2026-08-03T00:00:00')",
            (pid, bid, 1 if earned else 0, progress, total, earned_on),
        )


async def test_badge_data_is_an_empty_shape_when_nothing_is_polled(tmp_path):
    """Same keys, empty values — the page never has to branch on "no data"."""
    payload = await _badges(_conn(tmp_path))
    assert payload == {"players": [], "badges": [], "states": {}, "locations": []}


async def test_badge_data_reports_counts_and_the_trophy_earned(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_badges(conn, pid, *[(i, i <= 26, 1, 1) for i in range(1, 119)])

    payload = await _badges(conn)
    p = payload["players"][0]

    assert (p["earned"], p["possible"]) == (26, 118)
    assert p["tier"] == "bronze"                     # 25 earns it, 50 is next
    assert (p["next_tier"], p["to_next"]) == ("silver", 24)
    assert len(payload["badges"]) == 118


async def test_a_trophy_the_location_cannot_offer_is_not_dangled(tmp_path):
    """40 badges exist here, so silver's 50 is unreachable — the next trophy
    within reach is platinum, all 40 of them."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_badges(conn, pid, *[(i, i <= 26, 1, 1) for i in range(1, 41)])

    p = (await _badges(conn))["players"][0]
    assert p["tier"] == "bronze"
    assert (p["next_tier"], p["to_next"]) == ("platinum", 14)


async def test_platinum_is_every_badge_the_location_offers_not_a_fixed_count(tmp_path):
    """"100% of badges, not 100 badges" — and how many that is depends on which
    rooms the location has."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_badges(conn, pid, *[(i, True, 1, 1) for i in range(1, 31)])

    p = (await _badges(conn))["players"][0]
    assert (p["earned"], p["possible"]) == (30, 30)
    assert (p["tier"], p["next_tier"]) == ("platinum", None)


async def test_the_page_s_own_tally_rides_along_rather_than_being_reconciled(tmp_path):
    """Two endpoints, two answers. A disagreement is worth seeing, so both are
    sent and neither overwrites the other."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_badges(conn, pid, (1, True, 1, 1), (2, False, 0, 1))
    conn.execute(
        "INSERT INTO score_snapshots (player_id, location_id, polled_at, total_score,"
        " yearly_score, badges_earned, badges_possible, raw_scores_json)"
        " VALUES (?, 72, '2026-08-03T00:00:00', 1, 0, 7, 118, '[]')",
        (pid,),
    )

    p = (await _badges(conn))["players"][0]
    assert p["earned"] == 1                # what we enumerated
    assert p["reported_earned"] == 7       # what the score page said
    assert p["reported_possible"] == 118


async def test_a_player_never_polled_for_badges_is_absent_not_zeroed(tmp_path):
    """Nothing recorded is not the same claim as "has no badges"."""
    conn = _conn(tmp_path)
    seen = _seed_player(conn)
    _seed_player(conn, handle="kavo")
    _seed_badges(conn, seen, (1, True, 1, 1))

    payload = await _badges(conn)
    assert [p["id"] for p in payload["players"]] == [seen]
    assert set(payload["states"]) == {str(seen)}


async def test_hidden_players_are_left_out(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_badges(conn, pid, (1, True, 1, 1))
    conn.execute("UPDATE players SET hidden = 1 WHERE id = ?", (pid,))

    assert (await _badges(conn))["players"] == []


async def test_progress_and_earned_dates_survive_the_round_trip(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_badges(conn, pid, (1, True, 25, 25), earned_on="2026-08-02")
    _seed_badges(conn, pid, (2, False, 12, 25))
    _seed_badges(conn, pid, (3, False, 159, 0))   # the no-denominator kind

    states = (await _badges(conn))["states"][str(pid)]
    assert states["1"] == {
        "earned": True, "progress": 25, "total_progress": 25,
        "earned_on": "2026-08-02",
    }
    assert states["2"]["progress"] == 12
    # Sent as-is: the front end is what decides a 0 total means "no bar", and
    # nothing on the way here may divide by it.
    assert states["3"]["total_progress"] == 0


def test_summary_badge_column_counts_once_for_the_player(tmp_path):
    """Badges transfer between locations, so the count belongs to the player and
    must not be repeated per location the way levels beat is."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _seed_badges(conn, pid, (1, True, 1, 1), (2, True, 1, 1), (3, False, 0, 1))

    row = _build_player_summaries(conn, today=TODAY)[0]
    assert (row["badges_earned"], row["badges_possible"]) == (2, 3)
    assert len(row["locations"]) == 2
    assert all("badges_earned" not in loc for loc in row["locations"])


def test_summary_badge_column_is_none_when_never_polled(tmp_path):
    conn = _conn(tmp_path)
    _seed_player(conn)
    row = _build_player_summaries(conn, today=TODAY)[0]
    assert (row["badges_earned"], row["badges_possible"]) == (None, None)


async def test_badge_data_carries_the_community_detail(tmp_path):
    """Merged at the API boundary like the gamemode rules on /games, and keyed
    on name *and* description, so the two Untouchable 5.0 badges keep their own
    rooms instead of both getting whichever came first."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    for bid, name, desc in (
        (111, "Untouchable 5.0", "Win level 5 of Piperooni without losing a life"),
        (125, "Untouchable 5.0", "Win level 5 of Wormholes without losing a life"),
    ):
        conn.execute(
            "INSERT INTO badges (badge_id, name, description, stars, first_seen,"
            " last_seen) VALUES (?, ?, ?, 10, '2026-08-01', '2026-08-01')",
            (bid, name, desc),
        )
        conn.execute(
            "INSERT INTO player_badges (player_id, badge_id, earned, progress,"
            " total_progress, updated_at) VALUES (?, ?, 0, 0, 1, '2026-08-03')",
            (pid, bid),
        )

    by_id = {b["badge_id"]: b for b in (await _badges(conn))["badges"]}

    assert by_id[111]["rooms"] == ["Pipes"]
    assert by_id[125]["rooms"] == ["Portals"]
    assert by_id[111]["difficulty"] == "Hard"


async def test_badge_data_sends_each_location_s_rooms(tmp_path):
    """So the page can say where a badge is obtainable. The locations really do
    differ, which is the only reason it's worth saying."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    conn.execute(
        "INSERT INTO badges (badge_id, name, description, stars, first_seen,"
        " last_seen) VALUES (1, 'X', 'x', 5, '2026-08-01', '2026-08-01')"
    )
    conn.execute(
        "INSERT INTO player_badges (player_id, badge_id, earned, progress,"
        " total_progress, updated_at) VALUES (?, 1, 0, 0, 1, '2026-08-03')",
        (pid,),
    )
    conn.executemany(
        "INSERT INTO location_games (location_id, room_id, room_name, room_order,"
        " game_id, game_name, game_order, levels_json)"
        " VALUES (?, ?, ?, 0, ?, 'g', 0, '[0]')",
        [(72, 1, "Pipes", 101), (38, 2, "Portals", 201)],
    )

    locations = {l["name"]: l["rooms"] for l in (await _badges(conn))["locations"]}
    assert locations == {"Langley": ["Pipes"], "Coquitlam": ["Portals"]}


async def test_a_location_with_no_catalog_is_left_out_of_the_rooms_list(tmp_path):
    """An empty room list would read as "this location has no rooms", which the
    page would turn into "you can't get this here"."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    conn.execute(
        "INSERT INTO badges (badge_id, name, description, stars, first_seen,"
        " last_seen) VALUES (1, 'X', 'x', 5, '2026-08-01', '2026-08-01')"
    )
    conn.execute(
        "INSERT INTO player_badges (player_id, badge_id, earned, progress,"
        " total_progress, updated_at) VALUES (?, 1, 0, 0, 1, '2026-08-03')",
        (pid,),
    )

    assert (await _badges(conn))["locations"] == []
