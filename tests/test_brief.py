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


def test_the_roast_names_players_by_display_name_never_handle(tmp_path):
    conn = _conn(tmp_path)
    for h, name, day in (("xx_a", "Ace", "2026-11-17"), ("xx_b", "Bee", "2026-11-18"),
                         ("xx_z", "Zed", "2026-10-27")):
        _visit(conn, _player(conn, h, name), day, 1000)

    text = brief.render(_gather(conn), today=TODAY, rng=random.Random(0))

    assert "**Ace**" in text and "**Bee**" in text and "**Zed**" in text
    assert "xx_" not in text and "@" not in text


# ---------- render, from hand-built briefs ----------

NOV14 = date(2026, 11, 14)
NIGHT = [f"Badge {i}" for i in range(9)]


def _b(pid, name, *, days=2, pts=1000, visits=3, day=None, badges=(), wb=0, wr=0, disc=25):
    """A PlayerBrief with only what render reads; a session unless never seen."""
    session = None
    if days is not None:
        session = brief.Session(
            day=day or date.fromordinal(TODAY.toordinal() - days), points=100,
            badges=list(badges),
        )
    return brief.PlayerBrief(
        id=pid, name=name, days_since=days, last_session=session,
        window_points=pts, window_visits=visits, window_badges=wb,
        window_records=wr, records_held=wr, discount_pct=disc,
    )


def _crew():
    """Our four, as the live data stood on Nov 19."""
    return [
        _b(1, "Edward", days=5, day=NOV14, pts=800_384, visits=9, badges=NIGHT, wb=38, wr=23),
        _b(2, "Aaron", days=5, day=NOV14, pts=799_666, visits=9, badges=NIGHT, wb=36, wr=15),
        _b(3, "Landon", days=5, day=NOV14, pts=468_120, visits=6, badges=NIGHT, wb=30, wr=6),
        _b(4, "Michael", days=3, pts=365_946, visits=4, badges=["x"], wb=9, wr=3),
    ]


def _render(briefs, seed=0, **kw):
    return brief.render(briefs, today=TODAY, rng=random.Random(seed), **kw)


def _player_lines(text):
    return [ln for ln in text.split("\n") if "** · " in ln]


def test_layout_is_heading_subtext_headline_ranks_then_extras():
    text = _render(_crew())
    sections = text.split("\n\n")

    assert sections[0] == f"{brief.HEADING}\n-# last 30 days · as of Nov 19"
    assert "**Michael**" in sections[1]                       # last place, by name
    ranks = sections[2].split("\n")
    assert [ln.split(" ")[0] for ln in ranks] == ["🥇", "🥈", "🥉", "🐢"]
    assert [ln.split("**")[1] for ln in ranks] == ["Edward", "Aaron", "Landon", "Michael"]
    assert sections[-1].startswith(("🔥", "🏅", "🎯", "💸"))   # awards close it
    # No lists of badge or record names any more.
    assert "Badge 0" not in text


def test_each_player_line_carries_one_abbreviated_number():
    lines = _player_lines(_render(_crew()))
    assert "· 800k." in lines[0]
    assert "· 468k." in lines[2]
    assert "· 366k." in lines[3]


def test_a_seeded_rng_gives_the_same_post_and_different_seeds_vary():
    assert _render(_crew(), seed=7) == _render(_crew(), seed=7)
    assert len({_render(_crew(), seed=s) for s in range(12)}) > 1


def test_no_taunt_repeats_within_one_post():
    briefs = [_b(i, f"P{i}", pts=10_000 * (10 - i), visits=3) for i in range(8)]
    taunts = [ln.split(". ", 1)[1] for ln in _player_lines(_render(briefs))]
    assert len(taunts) == len(set(taunts))


def test_a_near_tie_is_called_out_as_the_gap():
    text = _render(_crew())
    aaron = _player_lines(text)[1]
    assert "· 718 points behind." in aaron
    assert aaron.split(". ", 1)[1].format(ahead="Edward") in {
        t.format(ahead="Edward") for t in brief._NEAR_TIE
    }
    # And the leader is reminded how narrow it was.
    edward = _player_lines(text)[0]
    assert edward.split(". ", 1)[1] in {
        t.format(gap="718") for t in brief._LEADER_BARELY
    }


def test_a_shared_night_out_is_one_line_not_one_per_player():
    text = _render(_crew())
    outings = [ln for ln in text.split("\n") if ln.startswith(("🎉", "🤝"))]
    assert len(outings) == 1
    assert "Nov 14" in outings[0] and "9 badges" in outings[0]
    assert "**Edward**, **Aaron** and **Landon**" in outings[0]
    assert "Michael" not in outings[0]


def test_one_player_with_a_big_night_gets_a_line_alone():
    briefs = [
        _b(1, "Ace", day=NOV14, days=5, badges=NIGHT, pts=5000),
        _b(2, "Bee", pts=4000, badges=["one"]),
    ]
    outings = [ln for ln in _render(briefs).split("\n") if ln.startswith("🎉")]
    assert len(outings) == 1 and "**Ace**" in outings[0] and "Bee" not in outings[0]


def test_slackers_are_roasted_on_days_gone_and_the_worst_is_the_headline():
    briefs = _crew()[:3] + [
        _b(4, "Michael", days=21, pts=41_300, visits=1, disc=0),
        _b(5, "Zed", days=9, pts=0, visits=0, disc=10),
    ]
    text = _render(briefs)
    lines = _player_lines(text)

    assert lines[3].startswith("💤 **Zed** · 9 days gone.")
    assert lines[4].startswith("🪦 **Michael** · 21 days gone.")
    assert any(t in lines[4] for t in brief._DISCOUNT_GONE)
    assert "10%" in lines[3]
    headline = text.split("\n\n")[1]
    assert "**Michael**" in headline and "Zed" not in headline
    assert "💸 Discount gone: **Michael**" in text


def test_a_player_never_seen_is_a_ghost_and_last_place():
    text = _render([_b(1, "Ace"), _b(2, "Casper", days=None, pts=0, visits=0, disc=0)])
    assert _player_lines(text)[-1].startswith("👻 **Casper** · never seen.")
    assert "**Casper**" in text.split("\n\n")[1]


def test_fewer_visits_but_more_per_visit_gets_a_grudging_nod():
    briefs = [_b(1, "Ace", pts=900_000, visits=10), _b(2, "Bee", pts=500_000, visits=3),
              _b(3, "Cee", pts=100_000, visits=8)]
    bee = _player_lines(_render(briefs))[1]
    assert bee.split(". ", 1)[1] in {t.format(leader="Ace") for t in brief._MIDDLE_EFFICIENT}


def test_one_player_alone_gets_no_headline_and_no_awards():
    text = _render([_b(1, "Ace")])
    sections = text.split("\n\n")
    assert len(sections) == 2 and sections[1].startswith("🥇 **Ace**")


def test_short_numbers():
    assert [brief.short(n) for n in (718, 4_500, 4_000, 468_120, 999_600, 1_234_567)] == [
        "718", "4.5k", "4k", "468k", "1M", "1.2M",
    ]


def test_a_crowded_post_fits_one_discord_message_by_trimming_whole_lines():
    briefs = [_b(i, f"Player with a long name {i}", pts=10_000 + i, visits=2, wb=i)
              for i in range(60)]
    text = _render(briefs)
    assert len(text) <= brief.DISCORD_LIMIT
    assert "…" not in text                       # nothing cut mid-line
    assert text.startswith(brief.HEADING)


def test_trimming_drops_awards_then_middle_taunts_then_the_rest(monkeypatch):
    def has_taunt(line):
        return len(line.split(". ", 1)) == 2

    full = _render(_crew())
    awards = full.split("\n\n")[-1]

    # Just too long: the awards go, every taunt stays.
    monkeypatch.setattr(brief, "DISCORD_LIMIT", len(full) - 1)
    text = _render(_crew())
    assert awards not in text and "🎉" in text
    assert all(has_taunt(ln) for ln in _player_lines(text))

    # Shorter still: only the leader and last place keep theirs.
    monkeypatch.setattr(brief, "DISCORD_LIMIT", len(full) - len(awards) - 10)
    lines = _player_lines(_render(_crew()))
    assert [has_taunt(ln) for ln in lines] == [True, False, False, True]


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
