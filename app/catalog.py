"""Per-location game catalog: rooms, the gamemodes in each, and the location's
top score per level.

Kept apart from the score poll because it is player-independent. The player's
own per-level scores come with their location data, so one catalog refresh
serves every tracked player at that location. The gamemodes arrive whole in
the location's /games, which the poll reads anyway for its level count; what
a refresh adds is one /highscores request per *room*, not per player.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from . import scraper
from .config import PollConfig
from .db import transaction

log = logging.getLogger(__name__)


def needs_refresh(
    conn: sqlite3.Connection,
    location_id: int,
    *,
    expected_levels: int | None,
    score_changed: bool,
) -> bool:
    """Whether this location's catalog should be re-fetched on this poll.

    Deliberately conservative about *when*, since a refresh costs one request
    per room. `expected_levels` is the co-op level total the location's /games
    lists; a catalog that stays short of it — one room that keeps failing, say
    — retries on every poll, which is the self-healing behaviour we want at a
    daily cadence.
    """
    row = conn.execute(
        "SELECT level_count, catalog_levels FROM location_catalog WHERE location_id = ?",
        (location_id,),
    ).fetchone()
    if row is None:
        return True  # never catalogued
    if score_changed:
        return True  # someone played here: scores moved, top scores may have too
    if expected_levels is not None and row["catalog_levels"] != expected_levels:
        return True  # upstream added or removed a room / gamemode
    return False


async def refresh_location(
    conn: sqlite3.Connection,
    session: AsyncSession,
    *,
    info: scraper.LocationInfo,
    poll_cfg: PollConfig,
) -> dict[str, int]:
    """Re-read every room's top scores at this location and rewrite its rows.

    Applied room by room rather than as one all-or-nothing swap: a room whose
    top scores fail to fetch keeps its previous rows — gamemodes included —
    instead of blanking, and the short count it leaves is what makes
    `needs_refresh` try again. Rooms that have disappeared from the location's
    /rooms are pruned.
    """
    location_id = info.location_id
    # /rooms carries no ordering field; its own order is the site's.
    rooms = [{**r, "order": i} for i, r in enumerate(info.rooms)]
    counters = {"rooms": 0, "errors": 0}
    jitter_lo, jitter_hi = poll_cfg.jitter_seconds

    for room in rooms:
        games = info.games_in(room["id"])
        if not games:
            # Nothing co-op to hold a top score, so nothing to ask for — but a
            # room that *used* to run some must still lose them.
            _store_room(conn, location_id, room, games, {})
            counters["rooms"] += 1
            continue
        await asyncio.sleep(random.uniform(jitter_lo, jitter_hi))
        try:
            top_scores = await scraper.fetch_room_top_scores(
                location_id,
                room["id"],
                session=session,
                timeout=poll_cfg.request_timeout_sec,
            )
        except (RequestException, scraper.ScrapeError, scraper.FetchError) as e:
            counters["errors"] += 1
            log.warning(
                "catalog fetch failed location=%s room=%s err=%s",
                location_id, room.get("name"), e,
            )
            continue
        except Exception:
            # Anything unexpected is still one room's problem, not the other
            # nine's — and skipping the status write below would leave
            # catalog_levels stale enough to suppress the retry.
            counters["errors"] += 1
            log.exception(
                "catalog fetch errored location=%s room=%s", location_id, room.get("name")
            )
            continue

        _store_room(conn, location_id, room, games, top_scores)
        counters["rooms"] += 1

    _prune_missing_rooms(conn, location_id, [r["id"] for r in rooms])
    catalog_levels = _record_status(conn, location_id, info.coop_levels)

    if catalog_levels != info.coop_levels:
        # /games is the check on our room walk. A mismatch means a room's
        # refresh failed and its rows are stale or missing.
        log.warning(
            "catalog incomplete location=%s catalogued=%s expected=%s",
            location_id, catalog_levels, info.coop_levels,
        )
    log.info("catalog refresh location=%s %s", location_id, counters)
    return counters


def _store_room(
    conn: sqlite3.Connection,
    location_id: int,
    room: dict[str, Any],
    games: Iterable[scraper.RoomGame],
    top_scores: dict[tuple[int, int], int],
) -> None:
    room_id = room["id"]
    room_order = room.get("order", 0)
    games = list(games)
    game_ids = {g.game_id for g in games}

    with transaction(conn):
        # Top scores are keyed by game, not room, so clear the union of the
        # games this room used to hold and the ones it holds now — otherwise a
        # gamemode that was retired keeps its stale top scores forever.
        stale_games = {
            r["game_id"]
            for r in conn.execute(
                "SELECT game_id FROM location_games WHERE location_id = ? AND room_id = ?",
                (location_id, room_id),
            ).fetchall()
        }
        stale_games.update(game_ids)
        if stale_games:
            placeholders = ",".join("?" * len(stale_games))
            conn.execute(
                f"DELETE FROM location_top_scores WHERE location_id = ? "
                f"AND game_id IN ({placeholders})",
                [location_id, *stale_games],
            )
        conn.execute(
            "DELETE FROM location_games WHERE location_id = ? AND room_id = ?",
            (location_id, room_id),
        )
        conn.executemany(
            """
            INSERT INTO location_games
                (location_id, room_id, room_name, room_order,
                 game_id, game_name, game_order, levels_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    location_id,
                    room_id,
                    room["name"],
                    room_order,
                    g.game_id,
                    g.name,
                    g.order,
                    json.dumps(list(g.levels)),
                )
                for g in games
            ],
        )
        # Only the catalogued gamemodes' scores: anything else in the response
        # belongs to a game the catalog doesn't list, and nothing would ever
        # clear it again.
        conn.executemany(
            """
            INSERT INTO location_top_scores (location_id, game_id, level_id, top_score)
            VALUES (?, ?, ?, ?)
            """,
            [
                (location_id, game_id, level_id, score)
                for (game_id, level_id), score in sorted(top_scores.items())
                if game_id in game_ids
            ],
        )


def _prune_missing_rooms(
    conn: sqlite3.Connection, location_id: int, room_ids: list[int]
) -> None:
    if not room_ids:
        return
    placeholders = ",".join("?" * len(room_ids))
    with transaction(conn):
        gone = [
            r["game_id"]
            for r in conn.execute(
                f"SELECT game_id FROM location_games WHERE location_id = ? "
                f"AND room_id NOT IN ({placeholders})",
                [location_id, *room_ids],
            ).fetchall()
        ]
        if gone:
            gone_placeholders = ",".join("?" * len(gone))
            conn.execute(
                f"DELETE FROM location_top_scores WHERE location_id = ? "
                f"AND game_id IN ({gone_placeholders})",
                [location_id, *gone],
            )
        conn.execute(
            f"DELETE FROM location_games WHERE location_id = ? "
            f"AND room_id NOT IN ({placeholders})",
            [location_id, *room_ids],
        )


def _record_status(
    conn: sqlite3.Connection, location_id: int, expected_levels: int | None
) -> int:
    """Recount levels from the table itself so a partial refresh is recorded
    honestly, then upsert the location's catalog status. Returns the count."""
    catalog_levels = 0
    for r in conn.execute(
        "SELECT levels_json FROM location_games WHERE location_id = ?", (location_id,)
    ).fetchall():
        try:
            catalog_levels += len(json.loads(r["levels_json"]))
        except (json.JSONDecodeError, TypeError):
            continue

    with transaction(conn):
        conn.execute(
            """
            INSERT INTO location_catalog
                (location_id, level_count, catalog_levels, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(location_id) DO UPDATE SET
                level_count    = excluded.level_count,
                catalog_levels = excluded.catalog_levels,
                fetched_at     = excluded.fetched_at
            """,
            (
                location_id,
                expected_levels,
                catalog_levels,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return catalog_levels


# ---------- read helpers ----------

def rooms_for_location(conn: sqlite3.Connection, location_id: int) -> list[dict[str, Any]]:
    """Catalog for one location, as rooms each holding their gamemodes."""
    rows = conn.execute(
        """
        SELECT room_id, room_name, room_order, game_id, game_name, game_order, levels_json
        FROM location_games
        WHERE location_id = ?
        ORDER BY room_order, room_id, game_order, game_id
        """,
        (location_id,),
    ).fetchall()

    rooms: dict[int, dict[str, Any]] = {}
    for r in rows:
        room = rooms.setdefault(
            r["room_id"],
            {"room_id": r["room_id"], "name": r["room_name"], "games": []},
        )
        try:
            levels = json.loads(r["levels_json"])
        except (json.JSONDecodeError, TypeError):
            levels = []
        room["games"].append(
            {
                "game_id": r["game_id"],
                "name": r["game_name"],
                "levels": [int(x) for x in levels],
            }
        )
    return list(rooms.values())


def top_scores_for_location(
    conn: sqlite3.Connection, location_id: int
) -> dict[int, dict[int, int]]:
    """{game_id: {level_id: top_score}} — sparse, see location_top_scores."""
    out: dict[int, dict[int, int]] = {}
    for r in conn.execute(
        "SELECT game_id, level_id, top_score FROM location_top_scores WHERE location_id = ?",
        (location_id,),
    ).fetchall():
        out.setdefault(r["game_id"], {})[r["level_id"]] = r["top_score"]
    return out


def status_for_location(conn: sqlite3.Connection, location_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT location_id, level_count, catalog_levels, fetched_at
        FROM location_catalog WHERE location_id = ?
        """,
        (location_id,),
    ).fetchone()
    return dict(row) if row else None
