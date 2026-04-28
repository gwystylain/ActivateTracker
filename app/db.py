from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id                    INTEGER PRIMARY KEY,
    handle                TEXT NOT NULL UNIQUE,
    display_name          TEXT,
    initial_streak        INTEGER NOT NULL DEFAULT 0,
    initial_streak_set_at TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS player_locations (
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    location_id INTEGER NOT NULL,
    slug        TEXT NOT NULL,
    PRIMARY KEY (player_id, location_id)
);

CREATE TABLE IF NOT EXISTS score_snapshots (
    id              INTEGER PRIMARY KEY,
    player_id       INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
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
CREATE INDEX IF NOT EXISTS ix_snap_player_time
    ON score_snapshots (player_id, location_id, polled_at);

CREATE TABLE IF NOT EXISTS visits (
    id          INTEGER PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    location_id INTEGER NOT NULL,
    visit_date  TEXT NOT NULL,
    score_delta INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_visits_player_date
    ON visits (player_id, visit_date);

CREATE TABLE IF NOT EXISTS login_attempts (
    ip           TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_login_attempts_ip_time
    ON login_attempts (ip, attempted_at);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
