"""Fetch & parse playactivate.com per-player score pages.

The page is server-rendered HTML containing a JSON hydration blob. We locate
the player object by its unique substring marker and walk braces to find the
matching closing brace, then json.loads the slice.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

log = logging.getLogger(__name__)

BASE_URL = "https://playactivate.com/scores/{handle}/{location_id}/{slug}/scores"
ROOM_URL = (
    "https://playactivate.com/scores/{handle}/{location_id}/{slug}/{room}/scores"
)

# Badges live nowhere on the score pages above — the site shows them only on the
# in-store score-checker iPad. This is a community-run proxy in front of an
# official Activate badge API (a bad handle comes back as
# {"error": "Activate API returned 500"}), public and unauthenticated, keyed on
# the handle alone. Overridable via config so pointing at the upstream directly,
# if its URL ever becomes known, is a config edit rather than a code change.
BADGE_API_BASE = "https://api.ryflix.ca/api/badges"

# Chrome TLS-fingerprint profile passed to curl_cffi. Required because
# playactivate.com (Cloudflare) rejects non-browser TLS handshakes with 403.
IMPERSONATE = "chrome124"

# The hydrated blob always contains  ..."player":{"player":{"playerName":...
# This substring is sufficiently unique to anchor on.
_ANCHOR = re.compile(r'"player"\s*:\s*\{\s*"player"\s*:\s*\{\s*"playerName"')


class ScrapeError(Exception):
    pass


@dataclass(frozen=True)
class ScrapeResult:
    handle: str
    location_id: int
    location_slug: str
    player_name: str | None      # canonical case from the site
    # `player.rank` — a property of the profile, not of any location: every
    # location's page returns the same value for a given handle (verified live,
    # langley and coquitlam both 4). Parsed for completeness; not what the
    # dashboard shows — see `standing`.
    profile_rank: int | None
    stars: int | None
    coins: int | None
    # `playerLocation.playerRank` — the number in the site's own page header,
    # next to the Rank_N-M badge. Also not the leaderboard position.
    location_player_rank: int | None
    yearly_rank: int | None
    # `playerLocation.standing` — what the page renders as
    # "Your Leaderboard Position: #138". Per location: on one live pull
    # coquitlam read 138 and langley 321 for the same handle.
    standing: int | None
    total_score: int
    yearly_score: int
    scores: list[dict[str, int]]  # [{gameId, levelId, highScore}, ...]
    # Levels the player has posted a score on, and how many the location has in
    # total. The page has no explicit "levels beat" field — one entry in
    # `scores` with a non-zero highScore is one level beaten.
    levels_beat: int = 0
    level_count: int | None = None
    # location.rooms — [{"id": 10, "name": "Hoops"}, ...]. The rooms this
    # location has, which is what drives catalog refresh (see app/catalog.py).
    rooms: tuple[dict[str, Any], ...] = ()
    # playerLocation.trophyProgress — {tier: {progress, requiredBadges}} for
    # bronze/silver/gold/platinum. The page's own badge tally, which corroborates
    # the badge API's (see `badges_from_trophy_progress`). Absent on older
    # captures — the committed langley fixture predates the field entirely.
    trophy_progress: dict[str, Any] | None = None


@dataclass(frozen=True)
class BadgeState:
    """One badge for one player, as the badge API reports it.

    `badge_id` is Activate's own id and the only safe key: the API returns 118
    badges under 117 distinct names, because "Untouchable 5.0" is two different
    badges (id 111 Piperooni, id 125 Wormholes). Note that the community badge
    trackers use their *own* id space, which does not line up with this one —
    joining anything of theirs by id silently mismatches most rows.
    """

    badge_id: int
    name: str
    description: str
    earned: bool
    # How far along an unearned badge is. `total_progress` is 0 for the four
    # badges with no denominator (Completionist, Halfway Mark, Activated, The
    # Grand Tour), where `progress` is a bare running count — never divide by it.
    progress: int
    total_progress: int
    stars: int | None  # the badge's own star value, unrelated to profile stars


@dataclass(frozen=True)
class RoomGame:
    """One gamemode within a room, e.g. Hoops → "Barrage"."""

    game_id: int                 # roomGames[].id; game_id // 100 == room_id
    name: str
    levels: tuple[int, ...]      # level ids, 0-based
    order: int                   # roomGames[].roomIndex — the site's own order


@dataclass(frozen=True)
class RoomScrape:
    room_id: int
    room_name: str
    games: tuple[RoomGame, ...]
    # roomScores — the best score anyone *at this location* has posted, keyed
    # by (game_id, level_id). Verified per-location: Coquitlam and Langley
    # disagree on every shared Hoops entry. Sparse — a missing key means nobody
    # at the location has ever scored that level.
    top_scores: dict[tuple[int, int], int]

    @property
    def level_total(self) -> int:
        return sum(len(g.levels) for g in self.games)


def extract_player_blob(html: str) -> dict[str, Any]:
    """Slice the JSON object that begins at  "player":{"player":{...  ."""
    match = _ANCHOR.search(html)
    if match is None:
        raise ScrapeError("player blob anchor not found in HTML")

    # Position cursor on the '{' that opens the outer player value.
    obj_start = html.find("{", match.start() + len('"player"'))
    if obj_start < 0:
        raise ScrapeError("opening brace for player blob not found")

    end = _find_matching(html, obj_start)
    if end < 0:
        raise ScrapeError("matching brace for player blob not found")

    raw = html[obj_start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ScrapeError(f"player blob is not valid JSON: {e}") from e


_PAIRS = {"{": "}", "[": "]"}


def _find_matching(s: str, start: int) -> int:
    """Return the index closing the '{' or '[' at `start`, ignoring strings."""
    opener = s[start]
    closer = _PAIRS.get(opener)
    if closer is None:
        return -1
    depth = 0
    i = start
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _extract_json_value(html: str, key: str) -> Any | None:
    """json.loads the object/array that `"key":` introduces, or None.

    The room page's interesting props (roomInfo / roomGames / roomScores) are
    siblings of the player blob inside a ~360 KB Inertia payload. Slicing each
    one by name is the same trick `extract_player_blob` uses, generalised to
    arrays, and avoids parsing the whole payload to read three keys.
    """
    marker = f'"{key}":'
    idx = html.find(marker)
    if idx < 0:
        return None
    i = idx + len(marker)
    while i < len(html) and html[i] in " \t\r\n":
        i += 1
    if i >= len(html) or html[i] not in _PAIRS:
        return None
    end = _find_matching(html, i)
    if end < 0:
        return None
    try:
        return json.loads(html[i : end + 1])
    except json.JSONDecodeError:
        return None


def parse_html(html: str, *, handle: str, location_id: int, slug: str) -> ScrapeResult:
    blob = extract_player_blob(html)
    inner_player = blob.get("player") or {}
    player_loc = blob.get("playerLocation") or {}
    location = blob.get("location") or {}
    scores = player_loc.get("scores") or []

    clean_scores = [
        {
            "gameId": int(s.get("gameId", 0)),
            "levelId": int(s.get("levelId", 0)),
            "highScore": int(s.get("highScore", 0)),
        }
        for s in scores
        if isinstance(s, dict)
    ]

    return ScrapeResult(
        handle=handle,
        location_id=location_id,
        location_slug=slug,
        player_name=inner_player.get("playerName"),
        profile_rank=_int_or_none(inner_player.get("rank")),
        stars=_int_or_none(inner_player.get("stars")),
        coins=_int_or_none(inner_player.get("coins")),
        location_player_rank=_int_or_none(player_loc.get("playerRank")),
        yearly_rank=_int_or_none(player_loc.get("yearlyRank")),
        standing=_int_or_none(player_loc.get("standing")),
        total_score=int(player_loc.get("totalScore") or 0),
        yearly_score=int(player_loc.get("yearlyScore") or 0),
        scores=clean_scores,
        levels_beat=sum(1 for s in clean_scores if s["highScore"] > 0),
        level_count=_int_or_none(location.get("levelCount")),
        rooms=_clean_rooms(location.get("rooms")),
        trophy_progress=(
            tp if isinstance(tp := player_loc.get("trophyProgress"), dict) else None
        ),
    )


def badges_from_trophy_progress(raw: Any) -> tuple[int | None, int | None]:
    """(badges earned, badges possible here) from the page's trophyProgress.

    The site states each trophy tier as a fraction of its threshold rather than
    a count: bronze 0.44 of 25, silver 0.22 of 50, gold 0.15 of 75, platinum
    0.09 of 118 all describe the same 11 badges. Platinum's threshold is the
    number of badges attainable *at that location* — badges needing a room the
    location lacks are excluded — so it doubles as the denominator.

    `progress` is rounded to 2dp, so a tier recovers the count to only
    ±0.005×threshold. Reading the smallest threshold still below 1.0 keeps that
    under ±0.375 and so exactly roundable up to 75 badges; past gold only
    platinum's threshold is left and the count can be off by one. Nothing else
    in the app depends on it being exact — it is the corroborating number, and
    `player_badges` holds the enumerated truth.
    """
    if not isinstance(raw, dict):
        return (None, None)

    tiers: list[tuple[int, float]] = []
    for rec in raw.values():
        if not isinstance(rec, dict):
            continue
        required = _int_or_none(rec.get("requiredBadges"))
        progress = rec.get("progress")
        if required is None or required <= 0 or not isinstance(progress, (int, float)):
            continue
        tiers.append((required, float(progress)))

    if not tiers:
        return (None, None)

    unfinished = sorted(t for t in tiers if t[1] < 1.0)
    required, progress = unfinished[0] if unfinished else max(tiers)
    return (round(progress * required), max(t[0] for t in tiers))


def parse_badges(payload: Any) -> list[BadgeState]:
    """Badge states from the badge API's JSON array.

    Split from `fetch_badges` so it can be exercised against a saved payload
    with no network, the way `parse_html` is.
    """
    if isinstance(payload, dict) and payload.get("error"):
        raise FetchError(f"badge API error: {payload['error']}")
    if not isinstance(payload, list):
        raise ScrapeError(f"badge payload is {type(payload).__name__}, expected a list")

    # Keyed rather than appended: a duplicate id would otherwise inflate the
    # "possible" count while collapsing to one row on insert.
    states: dict[int, BadgeState] = {}
    for b in payload:
        if not isinstance(b, dict):
            continue
        badge_id = _int_or_none(b.get("id"))
        if badge_id is None:
            continue
        states[badge_id] = BadgeState(
            badge_id=badge_id,
            name=str(b.get("name") or f"badge-{badge_id}"),
            description=str(b.get("description") or ""),
            earned=bool(b.get("status")),
            progress=_int_or_none(b.get("progress")) or 0,
            total_progress=_int_or_none(b.get("totalProgress")) or 0,
            stars=_int_or_none(b.get("stars")),
        )
    return [states[k] for k in sorted(states)]


async def fetch_badges(
    handle: str,
    *,
    session: AsyncSession,
    base: str = BADGE_API_BASE,
    timeout: float = 20.0,
) -> list[BadgeState]:
    """Every badge applicable to `handle`, earned or not.

    Per player, not per location: the endpoint takes only a handle, and badges
    transfer between Activate locations where scores and rank do not.
    """
    url = f"{base.rstrip('/')}/activate-sync/{quote(handle, safe='')}"
    log.info("fetch badges url=%s", url)
    resp = await session.get(url, impersonate=IMPERSONATE, timeout=timeout)
    if resp.status_code == 429:
        # Distinct from the catch-all below because it is not a verdict on the
        # handle: the proxy answers this one in milliseconds without touching
        # its upstream, and the same handle succeeds once the window rolls.
        raise RateLimited(
            f"HTTP 429 for {url}",
            retry_after=retry_after_seconds(resp.headers.get("Retry-After")),
        )
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code} for {url}")
    try:
        payload = resp.json()
    except ValueError as e:
        raise ScrapeError(f"badge response is not valid JSON: {e}") from e
    return parse_badges(payload)


def combine_badges(per_handle: list[list[BadgeState]]) -> list[BadgeState]:
    """Merge one multi-handle player's badge lists into a single set.

    A badge counts as earned if *any* of the player's profiles has it, and an
    unearned badge takes the furthest progress of any profile. Summing progress
    would invent a number no account actually holds — the same reason
    `combine_results` picks the best rank rather than averaging.
    """
    merged: dict[int, BadgeState] = {}
    for states in per_handle:
        for s in states:
            prior = merged.get(s.badge_id)
            if prior is None:
                merged[s.badge_id] = s
                continue
            merged[s.badge_id] = BadgeState(
                badge_id=s.badge_id,
                name=prior.name,
                description=prior.description,
                earned=prior.earned or s.earned,
                progress=max(prior.progress, s.progress),
                total_progress=max(prior.total_progress, s.total_progress),
                stars=prior.stars if prior.stars is not None else s.stars,
            )
    return [merged[k] for k in sorted(merged)]


def _clean_rooms(raw: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, list):
        return ()
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        room_id = _int_or_none(r.get("id"))
        if room_id is None:
            continue
        out.append({"id": room_id, "name": str(r.get("name") or f"room-{room_id}")})
    return tuple(out)


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def fetch(
    handle: str,
    location_id: int,
    slug: str,
    *,
    session: AsyncSession,
    timeout: float = 20.0,
) -> ScrapeResult:
    url = BASE_URL.format(handle=handle, location_id=location_id, slug=slug)
    log.info("fetch url=%s", url)
    resp = await session.get(url, impersonate=IMPERSONATE, timeout=timeout)
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code} for {url}")
    return parse_html(resp.text, handle=handle, location_id=location_id, slug=slug)


class FetchError(Exception):
    """Raised when the HTTP request itself fails (status, connection, timeout)."""


class RateLimited(FetchError):
    """A 429: the server refused because we asked too often, not because the
    request was wrong.

    Separate from a plain `FetchError` because the two want opposite handling —
    a 404 or a 500 means stop asking, a 429 means ask again later. Only the
    badge proxy raises it: playactivate.com served 28 score pages back to back
    without complaint, while somebody's personal server let five badge requests
    through and refused the next six.

    `retry_after` is the seconds the response asked us to wait, or None if it
    didn't say. Honour it when present — guessing at a stranger's rate limit is
    how you get banned from it.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def retry_after_seconds(raw: Any, *, now: datetime | None = None) -> float | None:
    """Seconds to wait, from a `Retry-After` header value.

    RFC 9110 allows either a delta in seconds or an HTTP-date, and both turn up
    in the wild. A date already in the past yields 0.0 — "go now" — rather than
    a negative sleep. Anything unparseable is None, which leaves the caller on
    its own backoff instead of on a number it made up.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


def room_slug(room_name: str) -> str:
    """URL segment for a room's scores page: the room name, lowercased.

    Not the marketing slug you see under /rooms — those are a different id
    space and the route rejects them. `Mega Grid` is `mega%20grid`; the
    marketing slug `mega-grid` 302s back to the location page, which parses
    fine but carries no roomInfo (see `parse_room_html`).
    """
    return quote(room_name.strip().lower(), safe="")


def parse_room_html(html: str, *, room_name: str) -> RoomScrape:
    """Read roomInfo / roomGames / roomScores out of a room scores page."""
    info = _extract_json_value(html, "roomInfo")
    if not isinstance(info, dict) or _int_or_none(info.get("id")) is None:
        # An unrecognised room segment doesn't 404 — it redirects to the
        # location page, which is valid HTML with no roomInfo in it. Without
        # this check that soft failure would quietly write an empty catalog.
        raise ScrapeError(
            f"no roomInfo for room {room_name!r} — bad slug or redirect to the location page"
        )

    games: list[RoomGame] = []
    for g in _extract_json_value(html, "roomGames") or []:
        if not isinstance(g, dict):
            continue
        game_id = _int_or_none(g.get("id"))
        if game_id is None:
            continue
        order = _int_or_none(g.get("roomIndex"))
        levels = [
            lvl
            for lvl in (_int_or_none(x) for x in (g.get("levels") or []))
            if lvl is not None
        ]
        games.append(
            RoomGame(
                game_id=game_id,
                name=str(g.get("name") or f"game-{game_id}"),
                levels=tuple(levels),
                order=order if order is not None else 0,
            )
        )

    top_scores: dict[tuple[int, int], int] = {}
    for s in _extract_json_value(html, "roomScores") or []:
        if not isinstance(s, dict):
            continue
        game_id = _int_or_none(s.get("gameId"))
        level_id = _int_or_none(s.get("levelId"))
        if game_id is None or level_id is None:
            continue
        top_scores[(game_id, level_id)] = int(s.get("highScore") or 0)

    return RoomScrape(
        room_id=int(info["id"]),
        room_name=str(info.get("name") or room_name),
        games=tuple(games),
        top_scores=top_scores,
    )


async def fetch_room(
    handle: str,
    location_id: int,
    slug: str,
    room_name: str,
    *,
    session: AsyncSession,
    timeout: float = 20.0,
) -> RoomScrape:
    url = ROOM_URL.format(
        handle=handle,
        location_id=location_id,
        slug=slug,
        room=room_slug(room_name),
    )
    log.info("fetch room url=%s", url)
    resp = await session.get(url, impersonate=IMPERSONATE, timeout=timeout)
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code} for {url}")
    return parse_room_html(resp.text, room_name=room_name)


def combine_results(results: list[ScrapeResult]) -> ScrapeResult:
    """Sum scores across multiple handles for the same (player, location).

    Used when one tracked player has multiple Activate profiles (typed in
    the admin form as a comma-separated handle list). Totals are summed and
    leaderboard ranks take the best (lowest) value.

    The per-level `scores` list is merged by (gameId, levelId) keeping the
    better highScore, so a level cleared under either profile reads as beaten
    at that player's best score on the /games page. `levels_beat` counts the
    merged entries, so a level both profiles cleared is still one level beaten.

    Every rank-shaped field — `standing` (the displayed leaderboard position),
    `location_player_rank`, `profile_rank` — takes the best (lowest) across the
    handles. Each candidate is a number one of the player's own profiles
    reported; the combine picks among them and never averages or otherwise
    invents one.
    """
    if not results:
        raise ValueError("combine_results requires at least one ScrapeResult")
    if len(results) == 1:
        return results[0]

    base = results[0]

    def _best(values: list[int | None]) -> int | None:
        nonempty = [v for v in values if v is not None]
        return min(nonempty) if nonempty else None

    def _sum(values: list[int | None]) -> int | None:
        nonempty = [v for v in values if v is not None]
        return sum(nonempty) if nonempty else None

    merged: dict[tuple[int, int], int] = {}
    for r in results:
        for s in r.scores:
            key = (s["gameId"], s["levelId"])
            if s["highScore"] > merged.get(key, -1):
                merged[key] = s["highScore"]
    scores = [
        {"gameId": game_id, "levelId": level_id, "highScore": high}
        for (game_id, level_id), high in sorted(merged.items())
    ]

    return ScrapeResult(
        handle=",".join(r.handle for r in results),
        location_id=base.location_id,
        location_slug=base.location_slug,
        player_name=results[0].player_name,
        profile_rank=_best([r.profile_rank for r in results]),
        stars=_sum([r.stars for r in results]),
        coins=_sum([r.coins for r in results]),
        location_player_rank=_best([r.location_player_rank for r in results]),
        yearly_rank=_best([r.yearly_rank for r in results]),
        standing=_best([r.standing for r in results]),
        total_score=sum(r.total_score for r in results),
        yearly_score=sum(r.yearly_score for r in results),
        scores=scores,
        levels_beat=sum(1 for s in scores if s["highScore"] > 0),
        level_count=base.level_count,
        rooms=base.rooms,
        # Badges belong to an Activate account, and two of the player's accounts
        # hold overlapping sets, so the tally that means something is the best
        # single account's — not a sum, which no account would recognise. Same
        # pick-don't-blend rule as the ranks above.
        trophy_progress=max(
            (r.trophy_progress for r in results if r.trophy_progress is not None),
            key=lambda tp: badges_from_trophy_progress(tp)[0] or 0,
            default=None,
        ),
    )
