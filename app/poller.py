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

from . import catalog as catalog_mod
from . import scraper
from . import streak
from .config import BadgeConfig, PollConfig
from .db import transaction

log = logging.getLogger(__name__)

_poll_lock = asyncio.Lock()


def parse_handles(raw: str) -> list[str]:
    """Parse a comma-separated handle string into a deduped, lowercase list.

    Whitespace is stripped per item. Empty items and exact duplicates are
    discarded. Order is preserved (first occurrence wins).
    """
    seen: set[str] = set()
    out: list[str] = []
    for part in (raw or "").split(","):
        h = part.strip().lower()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out


def format_handles(handles: list[str]) -> str:
    """Canonical storage form: comma + space separated."""
    return ", ".join(handles)


def badge_order(
    conn: sqlite3.Connection, seen_players: dict[int, list[str]]
) -> list[tuple[int, list[str]]]:
    """Players in staleness order: never fetched first, then oldest first.

    The badge leg used to run in score-poll order, which is stable — so when the
    proxy's rate limit cut the leg short it cut the *same* players short every
    night and they starved, one of them never having had a single successful
    fetch. Sorting by what we already hold makes that self-correcting: whoever
    lost last night is first in line tonight, and a limit that only allows five
    handles through rotates over the roster instead of pinning it.

    Ties break on player id purely so the order is deterministic.
    """
    freshness = {
        r["player_id"]: r["updated_at"]
        for r in conn.execute(
            "SELECT player_id, MAX(updated_at) AS updated_at"
            " FROM player_badges GROUP BY player_id"
        ).fetchall()
    }
    # "" sorts before any ISO timestamp, so a player with no badge rows at all
    # leads — that is the one whose fetch has never once succeeded.
    return sorted(
        seen_players.items(), key=lambda kv: (freshness.get(kv[0]) or "", kv[0])
    )


async def fetch_badges_with_retry(
    handle: str,
    *,
    session: AsyncSession,
    badge_cfg: BadgeConfig,
    timeout: float,
) -> list[scraper.BadgeState]:
    """`scraper.fetch_badges`, with a 429 waited out instead of lost.

    Only `RateLimited` is retried: it is the one failure that says nothing about
    the request, so asking again later is the whole fix. A 404 or a 500 means
    the handle is wrong or the upstream is down, and hammering either is rude
    and pointless.

    The wait is the response's own `Retry-After` when it sent one, otherwise a
    doubling backoff from `backoff_seconds`. A wait longer than
    `max_wait_seconds` gives up rather than sleeping it off, because a poll
    holds a global lock: an hour inside this function is an hour the scheduler
    and the admin's Refresh button spend blocked, to save one day of staleness
    on one player's badges.
    """
    delay = badge_cfg.backoff_seconds
    for attempt in range(badge_cfg.max_retries + 1):
        try:
            return await scraper.fetch_badges(
                handle,
                session=session,
                base=badge_cfg.api_base,
                timeout=timeout,
            )
        except scraper.RateLimited as e:
            wait = e.retry_after if e.retry_after is not None else delay
            if attempt >= badge_cfg.max_retries or wait > badge_cfg.max_wait_seconds:
                raise
            log.info(
                "badge fetch rate-limited handle=%s waiting %.1fs (attempt %d/%d)",
                handle, wait, attempt + 1, badge_cfg.max_retries,
            )
            await asyncio.sleep(wait)
            delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover


async def poll_all(
    conn: sqlite3.Connection,
    poll_cfg: PollConfig,
    *,
    force_catalog: bool = False,
    badge_cfg: BadgeConfig | None = None,
) -> dict[str, int]:
    """Poll every tracked (player, location). Returns counters for logging.

    Serialised on a global lock so the manual button can't pile up concurrent
    polls or race the daily scheduler.

    `badge_cfg` is opt-in: without it the poll fetches scores and the catalog
    only. Badges are the one leg that leaves playactivate.com for a third
    party's proxy, so a caller asks for them explicitly.

    `force_catalog` re-walks every location's rooms even when nothing suggests
    it changed. Normally the walk is throttled to score changes (see
    `catalog.needs_refresh`), which keeps a quiet poll to one request per
    player — but that also means the locations' *top* scores only get re-read
    when one of our own players moves. Other people at the venue keep setting
    new ones, so the admin page offers this as an explicit override.
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

        counters = {
            "polled": 0,
            "errors": 0,
            "visits_inserted": 0,
            "snapshots": 0,
            "catalog_locations": 0,
            "catalog_rooms": 0,
            "catalog_errors": 0,
            "badges_fetched": 0,
            "badges_newly_earned": 0,
            "badges_errors": 0,
            "badges_skipped": 0,
        }
        if not rows:
            return counters

        jitter_lo, jitter_hi = poll_cfg.jitter_seconds
        first_request = True
        # One entry per location, not per player: the catalog is player-
        # independent, so several players at one location still cost one
        # refresh. `changed` is sticky — any player's score rising there is
        # enough to re-read the room pages.
        seen_locations: dict[int, dict] = {}
        # Badges are per player, not per (player, location) — the endpoint takes
        # only a handle — so a player at three locations still costs one fetch
        # per handle, taken once after the score loop.
        seen_players: dict[int, list[str]] = {}

        async with AsyncSession() as session:
            for row in rows:
                handles = parse_handles(row["handle"])
                seen_players.setdefault(row["player_id"], handles)
                results: list[scraper.ScrapeResult] = []
                for handle in handles:
                    if not first_request:
                        await asyncio.sleep(random.uniform(jitter_lo, jitter_hi))
                    first_request = False
                    try:
                        results.append(
                            await scraper.fetch(
                                handle,
                                row["location_id"],
                                row["slug"],
                                session=session,
                                timeout=poll_cfg.request_timeout_sec,
                            )
                        )
                    except (RequestException, scraper.ScrapeError, scraper.FetchError) as e:
                        counters["errors"] += 1
                        log.warning(
                            "poll failed handle=%s location=%s err=%s",
                            handle, row["location_id"], e,
                        )
                        continue

                if not results:
                    continue

                combined = scraper.combine_results(results)
                inserted_visit = persist_snapshot(conn, row["player_id"], combined)
                counters["polled"] += 1
                counters["snapshots"] += 1
                if inserted_visit:
                    counters["visits_inserted"] += 1

                loc = seen_locations.setdefault(
                    combined.location_id,
                    {
                        "slug": row["slug"],
                        # A handle that just fetched cleanly, so the room pages
                        # will resolve too. Not `combined.handle`, which is the
                        # comma-joined list for a multi-handle player.
                        "handle": results[0].handle,
                        "rooms": combined.rooms,
                        "level_count": combined.level_count,
                        "changed": False,
                    },
                )
                loc["changed"] = loc["changed"] or inserted_visit
                if combined.rooms:
                    loc["rooms"] = combined.rooms
                    loc["level_count"] = combined.level_count

            # Opt-in: no config means scores and catalog only. Badges are the
            # one leg that leaves playactivate.com for a third party's proxy, so
            # a caller has to ask for them rather than get them by default.
            if badge_cfg is not None and badge_cfg.enabled:
                badge_lo, badge_hi = badge_cfg.spacing_seconds
                first_badge = True
                for player_id, handles in badge_order(conn, seen_players):
                    per_handle: list[list[scraper.BadgeState]] = []
                    failed = 0
                    for handle in handles:
                        if not first_badge:
                            await asyncio.sleep(random.uniform(badge_lo, badge_hi))
                        first_badge = False
                        try:
                            per_handle.append(
                                await fetch_badges_with_retry(
                                    handle,
                                    session=session,
                                    badge_cfg=badge_cfg,
                                    timeout=poll_cfg.request_timeout_sec,
                                )
                            )
                        except Exception as e:
                            # Badges come from a third party's proxy, so they are
                            # the least reliable leg of the poll and must never
                            # take the scores down — those are already committed.
                            failed += 1
                            counters["badges_errors"] += 1
                            log.warning(
                                "badge fetch failed handle=%s err=%s", handle, e
                            )
                    # `not per_handle` also covers a player with no handles at
                    # all, who would otherwise be "fetched" with an empty list.
                    if failed or not per_handle:
                        # A partial answer is not authoritative. `persist_badges`
                        # writes `earned` from what it is handed, so persisting
                        # one handle of a two-handle player would clear every
                        # badge only the other profile holds — `combine_badges`
                        # can only OR together what it was given. Last poll's
                        # rows are older but true; these would be wrong, and the
                        # page's "As of" column says which it is showing.
                        if per_handle:
                            counters["badges_skipped"] += 1
                            log.warning(
                                "badge write skipped player=%s: %d of %d handles failed",
                                player_id, failed, len(handles),
                            )
                        continue
                    try:
                        got = persist_badges(
                            conn, player_id, scraper.combine_badges(per_handle)
                        )
                    except Exception:
                        counters["badges_errors"] += 1
                        log.exception("badge persist failed player=%s", player_id)
                        continue
                    counters["badges_fetched"] += 1
                    counters["badges_newly_earned"] += got["newly_earned"]

            for location_id, loc in seen_locations.items():
                if not loc["rooms"]:
                    continue  # nothing to walk — the location page gave no rooms
                if not force_catalog and not catalog_mod.needs_refresh(
                    conn,
                    location_id,
                    level_count=loc["level_count"],
                    score_changed=loc["changed"],
                ):
                    continue
                try:
                    got = await catalog_mod.refresh_location(
                        conn,
                        session,
                        location_id=location_id,
                        slug=loc["slug"],
                        handle=loc["handle"],
                        rooms=loc["rooms"],
                        level_count=loc["level_count"],
                        poll_cfg=poll_cfg,
                    )
                except Exception:
                    # A catalog problem must never take the score poll down —
                    # the scores are already committed by this point.
                    counters["catalog_errors"] += 1
                    log.exception("catalog refresh failed location=%s", location_id)
                    continue
                counters["catalog_locations"] += 1
                counters["catalog_rooms"] += got["rooms"]
                counters["catalog_errors"] += got["errors"]

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

    Returns True iff a visit was inserted. The visit is dated to the day the
    play actually happened rather than the day we noticed — `streak.activity_day`
    owns that rule, shared with the chart's x-axis so the two can't drift.
    """
    now = now or datetime.now(timezone.utc)
    polled_at = now.isoformat(timespec="seconds")
    visit_date = streak.activity_day(now).isoformat()

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
                 player_rank, leaderboard_position, yearly_rank, stars, coins,
                 levels_beat, level_count, badges_earned, badges_possible,
                 raw_scores_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player_id,
                result.location_id,
                polled_at,
                result.total_score,
                result.yearly_score,
                # Two different numbers, both per location: the page-header
                # badge rank, and the leaderboard position the page states
                # outright ("Your Leaderboard Position: #138"). The dashboard
                # shows the second; the first is kept because it has history.
                result.location_player_rank,
                result.standing,
                result.yearly_rank,
                result.stars,
                result.coins,
                result.levels_beat,
                result.level_count,
                *scraper.badges_from_trophy_progress(result.trophy_progress),
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


def persist_badges(
    conn: sqlite3.Connection,
    player_id: int,
    states: list[scraper.BadgeState],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Upsert one player's badges. Returns counts for logging.

    The badge API restates the whole catalog every call, so `badges` is
    refreshed from it rather than fetched separately.

    `earned_on` is stamped only on an observed false→true transition, and dated
    `streak.activity_day` — the same one-day backdate the visit rows get, for the
    same reason: Activate's data refreshes about a day late, so a badge first
    seen earned in today's poll was earned yesterday. Sharing that helper is what
    stops a badge and the visit from the same session disagreeing about the date.

    On a player's *first* poll there is no transition to observe — everything
    already earned was earned at some unknown past date, so it is left NULL
    rather than backdated to today, which would be a fabricated date.
    """
    now = now or datetime.now(timezone.utc)
    updated_at = now.isoformat(timespec="seconds")
    earned_day = streak.activity_day(now).isoformat()

    newly_earned = 0
    with transaction(conn):
        prior = {
            r["badge_id"]: r
            for r in conn.execute(
                "SELECT badge_id, earned, earned_on FROM player_badges WHERE player_id = ?",
                (player_id,),
            ).fetchall()
        }
        backfill = not prior

        for s in states:
            conn.execute(
                """
                INSERT INTO badges
                    (badge_id, name, description, stars, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(badge_id) DO UPDATE SET
                    name        = excluded.name,
                    description = excluded.description,
                    stars       = excluded.stars,
                    last_seen   = excluded.last_seen
                """,
                (s.badge_id, s.name, s.description, s.stars, updated_at, updated_at),
            )

            was = prior.get(s.badge_id)
            if not s.earned or (was is not None and was["earned"]):
                # Not earned, or earned long since — carry any date forward.
                # Never cleared: an earned_on we once observed stays observed.
                earned_on = was["earned_on"] if was else None
            elif backfill:
                earned_on = None
            else:
                earned_on = earned_day
                newly_earned += 1

            conn.execute(
                """
                INSERT INTO player_badges
                    (player_id, badge_id, earned, progress, total_progress,
                     earned_on, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, badge_id) DO UPDATE SET
                    earned         = excluded.earned,
                    progress       = excluded.progress,
                    total_progress = excluded.total_progress,
                    earned_on      = excluded.earned_on,
                    updated_at     = excluded.updated_at
                """,
                (
                    player_id,
                    s.badge_id,
                    1 if s.earned else 0,
                    s.progress,
                    s.total_progress,
                    earned_on,
                    updated_at,
                ),
            )

    return {"badges": len(states), "newly_earned": newly_earned}


def player_locations_for_admin(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.id, p.handle, p.display_name, p.initial_streak,
               p.initial_streak_set_at, p.created_at
        FROM players p
        ORDER BY p.handle
        """
    ).fetchall()
