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
    hidden                INTEGER NOT NULL DEFAULT 0,
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
    player_rank     INTEGER,   -- playerLocation.playerRank, the header badge
    -- playerLocation.standing — what the page calls "Your Leaderboard Position".
    -- This is the one the dashboard shows.
    leaderboard_position INTEGER,
    yearly_rank     INTEGER,
    stars           INTEGER,
    coins           INTEGER,
    levels_beat     INTEGER,
    level_count     INTEGER,
    -- playerLocation.trophyProgress, reduced to a count. The page's own tally,
    -- kept as a cross-check on the badge API's enumerated list and as the
    -- fallback if that proxy ever goes away. NULL when the page omits the field.
    badges_earned   INTEGER,
    badges_possible INTEGER,
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

-- Game catalog for a location: which rooms it has and which gamemodes live in
-- each. Keyed per location even though the per-room gamemode lists were
-- identical at both locations probed, so one location's layout can never
-- corrupt another's. Refreshed by app/catalog.py, not by the score poll.
CREATE TABLE IF NOT EXISTS location_games (
    location_id INTEGER NOT NULL,
    room_id     INTEGER NOT NULL,
    room_name   TEXT    NOT NULL,
    room_order  INTEGER NOT NULL,   -- position in location.rooms
    game_id     INTEGER NOT NULL,   -- game_id // 100 == room_id
    game_name   TEXT    NOT NULL,
    game_order  INTEGER NOT NULL,   -- roomGames[].roomIndex
    levels_json TEXT    NOT NULL,   -- level ids, e.g. "[0,1,2,...,9]"
    PRIMARY KEY (location_id, room_id, game_id)
);

-- Best score anyone at this location has posted per level. Sparse: a missing
-- row means nobody at the location has ever scored that level.
CREATE TABLE IF NOT EXISTS location_top_scores (
    location_id INTEGER NOT NULL,
    game_id     INTEGER NOT NULL,
    level_id    INTEGER NOT NULL,
    top_score   INTEGER NOT NULL,
    PRIMARY KEY (location_id, game_id, level_id)
);

CREATE TABLE IF NOT EXISTS location_catalog (
    location_id    INTEGER PRIMARY KEY,
    level_count    INTEGER,          -- location.levelCount when last fetched
    catalog_levels INTEGER NOT NULL, -- levels we actually catalogued
    fetched_at     TEXT NOT NULL
);

-- Activate's badge catalog, as the badge API reports it. Upserted on every
-- poll: the response lists every badge applicable to the player, earned or not,
-- so there is no separate catalog fetch. `badge_id` is Activate's own id, and
-- `name` is deliberately not UNIQUE — "Untouchable 5.0" is two different badges
-- (Piperooni and Wormholes), so the name cannot be a key.
CREATE TABLE IF NOT EXISTS badges (
    badge_id    INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    stars       INTEGER,          -- the badge's own value, not profile stars
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

-- One row per player per badge they could earn. No location_id: the endpoint is
-- keyed on the handle alone, and badges transfer between locations where scores
-- and rank do not.
CREATE TABLE IF NOT EXISTS player_badges (
    player_id      INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    badge_id       INTEGER NOT NULL REFERENCES badges(badge_id),
    earned         INTEGER NOT NULL DEFAULT 0,
    progress       INTEGER,
    -- 0 means the badge has no denominator (Completionist, Halfway Mark,
    -- Activated, The Grand Tour): `progress` is a bare count. Never divide.
    total_progress INTEGER,
    -- activity_day of the poll that first saw it earned — the same one-day
    -- backdate the visit rows get, since Activate's data refreshes a day late.
    -- NULL for a badge already earned when we first polled the player.
    earned_on      TEXT,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (player_id, badge_id)
);

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
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent ALTERs for columns added after a deployment's table already
    exists. CREATE TABLE IF NOT EXISTS won't add columns to an existing table,
    so each new column needs a PRAGMA-guarded ALTER here."""
    player_cols = {r["name"] for r in conn.execute("PRAGMA table_info(players)")}
    if "hidden" not in player_cols:
        conn.execute(
            "ALTER TABLE players ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
        )

    snap_cols = {r["name"] for r in conn.execute("PRAGMA table_info(score_snapshots)")}
    for col in (
        "levels_beat",
        "level_count",
        "leaderboard_position",
        "badges_earned",
        "badges_possible",
    ):
        if col not in snap_cols:
            conn.execute(f"ALTER TABLE score_snapshots ADD COLUMN {col} INTEGER")


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
