from datetime import datetime, timezone

import pytest

from app import db as db_mod
from app.poller import persist_snapshot
from app.scraper import ScrapeResult


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
        player_rank=3,
        stars=355,
        coins=145,
        location_player_rank=6,
        yearly_rank=2932,
        standing=287,
        total_score=total,
        yearly_score=total // 2,
        scores=[{"gameId": 1, "levelId": 0, "highScore": total}],
    )


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
    assert rows[0]["visit_date"] == "2026-04-28"
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
