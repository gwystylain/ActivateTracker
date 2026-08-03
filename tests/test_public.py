from datetime import date

from app import db as db_mod
from app.routes.public import _build_player_summaries

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
