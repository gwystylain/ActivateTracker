import json
from datetime import date
from types import SimpleNamespace

from app import db as db_mod
from app.routes.public import _build_player_summaries, chart_data

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


def _snap(conn, pid, loc, polled_at, *, rank, beat, count=470, total=1000):
    conn.execute(
        "INSERT INTO score_snapshots (player_id, location_id, polled_at, total_score,"
        " yearly_score, player_rank, levels_beat, level_count, raw_scores_json)"
        " VALUES (?, ?, ?, ?, 0, ?, ?, ?, '[]')",
        (pid, loc, polled_at, total, rank, beat, count),
    )


def test_summary_surfaces_best_rank_and_top_levels_beat(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", rank=6, beat=40)
    _snap(conn, pid, 38, "2026-08-01T00:00:00", rank=2, beat=25, count=300)

    (p,) = _build_player_summaries(conn, today=TODAY)

    # Header: best rank is the lowest number, levels beat is the highest —
    # carrying that location's own level total.
    assert p["rank"] == 2
    assert p["levels_beat"] == 40
    assert p["level_count"] == 470

    by_name = {loc["name"]: loc for loc in p["locations"]}
    assert by_name["Langley"]["rank"] == 6
    assert by_name["Langley"]["levels_beat"] == 40
    assert by_name["Coquitlam"]["rank"] == 2
    assert by_name["Coquitlam"]["levels_beat"] == 25
    assert by_name["Coquitlam"]["level_count"] == 300


def test_summary_uses_latest_snapshot_per_location(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-07-01T00:00:00", rank=40, beat=10)
    _snap(conn, pid, 72, "2026-08-02T00:00:00", rank=9, beat=31)

    (p,) = _build_player_summaries(conn, today=TODAY)
    langley = next(loc for loc in p["locations"] if loc["name"] == "Langley")
    assert langley["rank"] == 9
    assert langley["levels_beat"] == 31


def test_summary_without_snapshots_reports_none(tmp_path):
    conn = _conn(tmp_path)
    _seed_player(conn)

    (p,) = _build_player_summaries(conn, today=TODAY)
    assert p["rank"] is None
    assert p["levels_beat"] is None
    assert p["level_count"] is None
    assert all(loc["rank"] is None for loc in p["locations"])


async def _chart(conn):
    """chart_data only touches request.app.state.db, so a stub stands in."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=conn)))
    return json.loads((await chart_data(request)).body)


async def test_chart_emits_both_metrics_per_point(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", rank=1, beat=1, total=100)
    _snap(conn, pid, 38, "2026-08-01T00:00:00", rank=1, beat=1, total=500)

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
    _snap(conn, pid, 72, "2026-08-01T00:00:00", rank=1, beat=1, total=100)
    _snap(conn, pid, 38, "2026-08-01T00:00:00", rank=1, beat=1, total=500)
    # Langley climbs but stays below coquitlam: max unchanged, sum up.
    _snap(conn, pid, 72, "2026-08-02T00:00:00", rank=1, beat=1, total=300)

    (player,) = (await _chart(conn))["players"]
    assert [(p["date"], p["top_score"], p["total_score"]) for p in player["points"]] == [
        ("2026-08-01", 500, 600),
        ("2026-08-02", 500, 800),
    ]


async def test_chart_drops_days_where_neither_metric_moves(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", rank=1, beat=1, total=100)
    _snap(conn, pid, 72, "2026-08-02T00:00:00", rank=1, beat=1, total=100)  # no change
    _snap(conn, pid, 72, "2026-08-03T00:00:00", rank=1, beat=1, total=180)

    (player,) = (await _chart(conn))["players"]
    assert [p["date"] for p in player["points"]] == ["2026-08-01", "2026-08-03"]


async def test_chart_excludes_hidden_players(tmp_path):
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    _snap(conn, pid, 72, "2026-08-01T00:00:00", rank=1, beat=1, total=100)
    conn.execute("UPDATE players SET hidden = 1 WHERE id = ?", (pid,))

    assert (await _chart(conn))["players"] == []


def test_summary_ignores_locations_with_null_columns(tmp_path):
    """Rows written before the levels_beat migration have NULLs — they must not
    crash the max()/min() or masquerade as a real value."""
    conn = _conn(tmp_path)
    pid = _seed_player(conn)
    conn.execute(
        "INSERT INTO score_snapshots (player_id, location_id, polled_at, total_score,"
        " yearly_score, raw_scores_json) VALUES (?, 72, '2026-08-01T00:00:00', 5, 0, '[]')",
        (pid,),
    )
    _snap(conn, pid, 38, "2026-08-01T00:00:00", rank=3, beat=12)

    (p,) = _build_player_summaries(conn, today=TODAY)
    assert p["rank"] == 3
    assert p["levels_beat"] == 12
