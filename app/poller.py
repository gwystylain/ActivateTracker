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

# What a failed request to playactivate.com looks like. Anything else escaping
# a fetch is a bug, and is left to propagate.
_FETCH_FAILURES = (RequestException, scraper.ScrapeError, scraper.FetchError)


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
    """Players in proxy-staleness order: never fetched first, then oldest first.

    The proxy leg used to run in score-poll order, which is stable — so when the
    proxy's rate limit cut the leg short it cut the *same* players short every
    night and they starved, one of them never having had a single successful
    fetch. Sorting by what we already hold makes that self-correcting: whoever
    lost last night is first in line tonight, and a limit that only allows five
    handles through rotates over the roster instead of pinning it.

    Read off `progress_at`, not `updated_at`: the site's own badge list now
    refreshes every row nightly, so `updated_at` would call everyone fresh and
    hand the order straight back to player id.

    Ties break on player id purely so the order is deterministic.
    """
    freshness = {
        r["player_id"]: r["progress_at"]
        for r in conn.execute(
            "SELECT player_id, MAX(progress_at) AS progress_at"
            " FROM player_badges GROUP BY player_id"
        ).fetchall()
    }
    # "" sorts before any ISO timestamp, so a player with no proxy data at all
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
    on one player's badge progress.
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

    Four legs, in order, all but the third on playactivate.com:

    1. Each tracked location's rooms and gamemodes, once per location — the
       level count every snapshot there is measured against.
    2. Each (player, location): the handle's profile (once per handle), its
       scores there, and the site's badge list there.
    3. `badge_cfg`, opt-in: the community proxy, for badge progress and stars,
       which the site's own list doesn't carry. Without it the poll never
       leaves playactivate.com, so no caller reaches a third party by omission.
    4. The catalog walk, throttled to locations where a score moved.

    `force_catalog` re-walks every location's rooms even when nothing suggests
    it changed. Normally the walk is throttled to score changes (see
    `catalog.needs_refresh`) — but that also means the locations' *top* scores
    only get re-read when one of our own players moves. Other people at the
    venue keep setting new ones, so the admin page offers this as an explicit
    override.
    """
    if _poll_lock.locked():
        log.info("poll_all skipped: another poll is in progress")
        return {"skipped": 1}

    async with _poll_lock:
        rows = conn.execute(
            """
            SELECT p.id          AS player_id,
                   p.handle      AS handle,
                   pl.location_id AS location_id
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
            "location_errors": 0,
            "catalog_locations": 0,
            "catalog_rooms": 0,
            "catalog_errors": 0,
            "badges_fetched": 0,
            "badges_newly_earned": 0,
            "badges_errors": 0,
            "badges_skipped": 0,
            "badges_retired": 0,
        }
        if not rows:
            return counters

        jitter_lo, jitter_hi = poll_cfg.jitter_seconds
        first_request = True

        async def pace() -> None:
            nonlocal first_request
            if not first_request:
                await asyncio.sleep(random.uniform(jitter_lo, jitter_hi))
            first_request = False

        timeout = poll_cfg.request_timeout_sec
        proxy_on = badge_cfg is not None and badge_cfg.enabled

        async with AsyncSession() as session:
            # ---- 1. locations ----
            locations: dict[int, scraper.LocationInfo] = {}
            for location_id in sorted({r["location_id"] for r in rows}):
                await pace()
                try:
                    locations[location_id] = await scraper.fetch_location(
                        location_id, session=session, timeout=timeout
                    )
                except _FETCH_FAILURES as e:
                    # Scores still poll without it; they just carry no level
                    # count, and the catalog isn't walked this time.
                    counters["location_errors"] += 1
                    log.warning("location fetch failed location=%s err=%s", location_id, e)

            # ---- 2. scores and the site's badges ----
            profiles: dict[str, dict | None] = {}
            # Sticky per location: any player's score rising there is enough to
            # re-read its rooms, and several players at one location still cost
            # one walk.
            changed: dict[int, bool] = {}
            seen_players: dict[int, list[str]] = {}
            site_badges: dict[int, list[list[scraper.BadgeState]]] = {}
            site_failed: dict[int, int] = {}
            # Each location's offer, from whichever handles' badge lists landed.
            offers: dict[int, set[int]] = {}

            for row in rows:
                player_id = row["player_id"]
                location_id = row["location_id"]
                handles = parse_handles(row["handle"])
                seen_players.setdefault(player_id, handles)
                site_badges.setdefault(player_id, [])
                info = locations.get(location_id)

                results: list[scraper.ScrapeResult] = []
                for handle in handles:
                    if handle not in profiles:
                        await pace()
                        try:
                            profiles[handle] = await scraper.fetch_profile(
                                handle, session=session, timeout=timeout
                            )
                        except _FETCH_FAILURES as e:
                            # Most often a handle the site doesn't know, which
                            # 404s on every endpoint — so not retried per
                            # location, only counted.
                            profiles[handle] = None
                            log.warning("profile fetch failed handle=%s err=%s", handle, e)
                    if profiles[handle] is None:
                        counters["errors"] += 1
                        site_failed[player_id] = site_failed.get(player_id, 0) + 1
                        continue

                    await pace()
                    try:
                        results.append(
                            await scraper.fetch(
                                handle,
                                location_id,
                                session=session,
                                timeout=timeout,
                                profile=profiles[handle],
                                level_count=info.level_count if info else None,
                            )
                        )
                    except _FETCH_FAILURES as e:
                        counters["errors"] += 1
                        site_failed[player_id] = site_failed.get(player_id, 0) + 1
                        log.warning(
                            "poll failed handle=%s location=%s err=%s",
                            handle, location_id, e,
                        )
                        continue

                    await pace()
                    try:
                        states = await scraper.fetch_player_badges(
                            handle, location_id, session=session, timeout=timeout
                        )
                        site_badges[player_id].append(states)
                        offers.setdefault(location_id, set()).update(
                            s.badge_id for s in states
                        )
                    except _FETCH_FAILURES as e:
                        counters["badges_errors"] += 1
                        site_failed[player_id] = site_failed.get(player_id, 0) + 1
                        log.warning(
                            "badge fetch failed handle=%s location=%s err=%s",
                            handle, location_id, e,
                        )

                if not results:
                    continue

                combined = scraper.combine_results(results)
                inserted_visit = persist_snapshot(conn, player_id, combined)
                counters["polled"] += 1
                counters["snapshots"] += 1
                if inserted_visit:
                    counters["visits_inserted"] += 1
                changed[location_id] = changed.get(location_id, False) or inserted_visit

            for location_id, badge_ids in offers.items():
                persist_location_badges(conn, location_id, badge_ids)

            # ---- 3. the proxy, for progress and stars ----
            proxy_badges: dict[int, list[list[scraper.BadgeState]]] = {}
            proxy_failed: dict[int, int] = {}
            if proxy_on:
                badge_lo, badge_hi = badge_cfg.spacing_seconds
                first_badge = True
                for player_id, handles in badge_order(conn, seen_players):
                    proxy_badges[player_id] = []
                    for handle in handles:
                        if not first_badge:
                            await asyncio.sleep(random.uniform(badge_lo, badge_hi))
                        first_badge = False
                        try:
                            proxy_badges[player_id].append(
                                await fetch_badges_with_retry(
                                    handle,
                                    session=session,
                                    badge_cfg=badge_cfg,
                                    timeout=timeout,
                                )
                            )
                        except Exception as e:
                            # Somebody else's server: the least reliable leg of
                            # the poll, and it must never take the scores down —
                            # those are already committed.
                            proxy_failed[player_id] = proxy_failed.get(player_id, 0) + 1
                            counters["badges_errors"] += 1
                            log.warning("badge proxy failed handle=%s err=%s", handle, e)

            for player_id in seen_players:
                _write_badges(
                    conn,
                    player_id,
                    site=site_badges.get(player_id, []),
                    site_whole=not site_failed.get(player_id),
                    proxy=proxy_badges.get(player_id, []),
                    proxy_whole=proxy_on and not proxy_failed.get(player_id),
                    proxy_on=proxy_on,
                    counters=counters,
                )

            # ---- 4. the catalog ----
            for location_id, info in locations.items():
                if not info.rooms:
                    continue  # nothing to walk — the location lists no rooms
                if not force_catalog and not catalog_mod.needs_refresh(
                    conn,
                    location_id,
                    expected_levels=info.coop_levels,
                    score_changed=changed.get(location_id, False),
                ):
                    continue
                try:
                    got = await catalog_mod.refresh_location(
                        conn, session, info=info, poll_cfg=poll_cfg
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


def _write_badges(
    conn: sqlite3.Connection,
    player_id: int,
    *,
    site: list[list[scraper.BadgeState]],
    site_whole: bool,
    proxy: list[list[scraper.BadgeState]],
    proxy_whole: bool,
    proxy_on: bool,
    counters: dict[str, int],
) -> None:
    """Persist one player's badges from whichever sources answered in full.

    A partial answer is never used. `combine_badges` can only OR together what
    it was handed and `persist_badges` writes `earned` from that, so one handle
    of a two-handle player — or one of a player's locations — would clear every
    badge only the missing answer holds. Each source is therefore taken whole
    or not at all; with neither whole, last poll's rows stand, older but true.

    Retiring is stricter still, since it deletes. The proxy lists the entire
    catalog, so its answer alone defines it; the site lists only the badges
    attainable at the tracked locations, so it defines the catalog only when
    the proxy isn't in play. Retiring on the site's answer while the proxy is
    merely down tonight would delete every badge no tracked location offers —
    Row By Row, say, earned at a Climb room elsewhere.
    """
    use_site = site_whole and bool(site)
    use_proxy = proxy_whole and bool(proxy)
    if not use_site and not use_proxy:
        if site or proxy:
            counters["badges_skipped"] += 1
            log.warning("badge write skipped player=%s: no source answered in full", player_id)
        return

    lists = (site if use_site else []) + (proxy if use_proxy else [])
    try:
        got = persist_badges(
            conn,
            player_id,
            scraper.combine_badges(lists),
            retire=use_proxy or (use_site and not proxy_on),
        )
    except Exception:
        counters["badges_errors"] += 1
        log.exception("badge persist failed player=%s", player_id)
        return
    counters["badges_fetched"] += 1
    counters["badges_newly_earned"] += got["newly_earned"]
    counters["badges_retired"] += got["retired"]


def persist_location_badges(
    conn: sqlite3.Connection, location_id: int, badge_ids: Iterable[int]
) -> None:
    """Replace one location's offer with what the site just listed.

    Called only with ids from answers that arrived whole, and every whole
    answer is the location's entire offer — the list doesn't vary by who asks —
    so a location none of tonight's answers covered keeps last night's rows
    instead of being emptied.
    """
    ids = sorted(set(badge_ids))
    if not ids:
        return
    with transaction(conn):
        conn.execute("DELETE FROM location_badges WHERE location_id = ?", (location_id,))
        conn.executemany(
            "INSERT INTO location_badges (location_id, badge_id) VALUES (?, ?)",
            [(location_id, b) for b in ids],
        )


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
                # Two different numbers, both per location: the page's "RANK"
                # (totalScore // 25000), and the "PLAYER POSITION" it states
                # outright. The dashboard shows the second; the first is kept
                # because it has history.
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
    retire: bool = True,
) -> dict[str, int]:
    """Upsert one player's badges. Returns counts for logging.

    Both sources restate everything they know on every call, so `badges` is
    refreshed from them rather than fetched separately.

    A None progress or star value is the site saying nothing, not zero, and is
    never written over a number the proxy gave earlier: `progress_at` records
    when the proxy last spoke, and those columns keep its last word until it
    speaks again.

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
                    stars       = COALESCE(excluded.stars, badges.stars),
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
                     earned_on, updated_at, progress_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, badge_id) DO UPDATE SET
                    earned         = excluded.earned,
                    progress       = COALESCE(excluded.progress, player_badges.progress),
                    total_progress = COALESCE(excluded.total_progress,
                                              player_badges.total_progress),
                    earned_on      = excluded.earned_on,
                    updated_at     = excluded.updated_at,
                    progress_at    = COALESCE(excluded.progress_at, player_badges.progress_at)
                """,
                (
                    player_id,
                    s.badge_id,
                    1 if s.earned else 0,
                    s.progress,
                    s.total_progress,
                    earned_on,
                    updated_at,
                    updated_at if s.progress is not None else None,
                ),
            )

        retired = _retire_badges(conn, player_id, prior, states) if retire else 0

    return {"badges": len(states), "newly_earned": newly_earned, "retired": retired}


def _retire_badges(
    conn: sqlite3.Connection,
    player_id: int,
    prior: dict[int, sqlite3.Row],
    states: list[scraper.BadgeState],
) -> int:
    """Drop rows for badges no source lists any more. Returns how many.

    `badges` is a mirror of upstream, and upstream does retire badges — three
    went in the proxy's move to activate-scores.ca (Go For Gold, Recollection,
    Sniper Shot 1.0), and Mascot after. Upserting alone would leave every
    existing player counted out of the old total while a newly added one was
    counted out of the new, which is a denominator that depends on when you
    joined.

    Safe only because the caller has already established the payload defines
    the whole catalog — see `_write_badges`, which passes `retire=False` for
    anything less, so a short list never reads as "upstream dropped these".
    """
    stale = sorted(set(prior) - {s.badge_id for s in states})
    if not stale:
        return 0

    conn.executemany(
        "DELETE FROM player_badges WHERE player_id = ? AND badge_id = ?",
        [(player_id, b) for b in stale],
    )
    # Then the catalog row, but only once no player is left holding it — the
    # foreign key makes that order mandatory rather than merely tidy, and a
    # player whose own poll was skipped keeps their row until theirs lands.
    conn.executemany(
        "DELETE FROM badges WHERE badge_id = ?"
        "   AND NOT EXISTS (SELECT 1 FROM player_badges WHERE badge_id = ?)",
        [(b, b) for b in stale],
    )
    log.info("retired %d badge(s) for player=%s: %s", len(stale), player_id, stale)
    return len(stale)


def player_locations_for_admin(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.id, p.handle, p.display_name, p.initial_streak,
               p.initial_streak_set_at, p.created_at
        FROM players p
        ORDER BY p.handle
        """
    ).fetchall()
