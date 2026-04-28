"""Polling logic. Pulls scores for every (player, location) and infers visits."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from . import scraper
from .config import PollConfig
from .db import transaction

log = logging.getLogger(__name__)

_poll_lock = asyncio.Lock()


async def poll_all(conn: sqlite3.Connection, poll_cfg: PollConfig) -> dict[str, int]:
    """Poll every tracked (player, location). Returns counters for logging.

    Serialised on a global lock so the manual button can't pile up concurrent
    polls or race the daily scheduler.
    """
    if _poll_lock.locked():
        log.info("poll_all skipped: another poll is in progress")
        return {"skipped": 1}

    async with _poll_lock:
        rows = conn.execute(
            """
            SELECT p.id          AS player_id,
                   p.handle      AS handle,
                   pl.location_id AS location_id,
                   pl.slug       AS slug
            FROM players p
            JOIN player_locations pl ON pl.player_id = p.id
            ORDER BY p.id, pl.location_id
            """
        ).fetchall()

        counters = {"polled": 0, "errors": 0, "visits_inserted": 0, "snapshots": 0}
        if not rows:
            return counters

        jitter_lo, jitter_hi = poll_cfg.jitter_seconds

        async with AsyncSession() as session:
            for i, row in enumerate(rows):
                if i > 0:
                    await asyncio.sleep(random.uniform(jitter_lo, jitter_hi))
                try:
                    result = await scraper.fetch(
                        row["handle"],
                        row["location_id"],
                        row["slug"],
                        session=session,
                        timeout=poll_cfg.request_timeout_sec,
                    )
                except (RequestException, scraper.ScrapeError, scraper.FetchError) as e:
                    counters["errors"] += 1
                    log.warning(
                        "poll failed handle=%s location=%s err=%s",
                        row["handle"], row["location_id"], e,
                    )
                    continue

                inserted_visit = persist_snapshot(conn, row["player_id"], result)
                counters["polled"] += 1
                counters["snapshots"] += 1
                if inserted_visit:
                    counters["visits_inserted"] += 1

        log.info("poll_all done: %s", counters)
        return counters


def persist_snapshot(
    conn: sqlite3.Connection,
    player_id: int,
    result: scraper.ScrapeResult,
    *,
    now: datetime | None = None,
) -> bool:
    """Insert snapshot. If totalScore went up vs last snapshot, also insert a visit.

    Returns True iff a visit was inserted.
    """
    now = now or datetime.now(timezone.utc)
    polled_at = now.isoformat(timespec="seconds")
    visit_date = now.date().isoformat()

    with transaction(conn):
        prior = conn.execute(
            """
            SELECT total_score
            FROM score_snapshots
            WHERE player_id = ? AND location_id = ?
            ORDER BY polled_at DESC
            LIMIT 1
            """,
            (player_id, result.location_id),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO score_snapshots
                (player_id, location_id, polled_at, total_score, yearly_score,
                 player_rank, yearly_rank, stars, coins, raw_scores_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player_id,
                result.location_id,
                polled_at,
                result.total_score,
                result.yearly_score,
                result.location_player_rank,
                result.yearly_rank,
                result.stars,
                result.coins,
                json.dumps(result.scores),
            ),
        )

        if prior is not None and result.total_score > prior["total_score"]:
            delta = result.total_score - prior["total_score"]
            conn.execute(
                """
                INSERT INTO visits (player_id, location_id, visit_date, score_delta)
                VALUES (?, ?, ?, ?)
                """,
                (player_id, result.location_id, visit_date, delta),
            )
            return True

    return False


def player_locations_for_admin(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.id, p.handle, p.display_name, p.initial_streak,
               p.initial_streak_set_at, p.created_at
        FROM players p
        ORDER BY p.handle
        """
    ).fetchall()
