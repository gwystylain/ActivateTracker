"""Fetch & parse playactivate.com's player and location data.

The scores page (/scores/<handle>/<location>/scores) is a Nuxt app that renders
nothing of the player server-side: the browser fills it from a JSON API on the
same host, one small endpoint per concern. That API is what we read — it is
what the page itself trusts, and it is far steadier ground than the hydration
blob the old server-rendered page carried, which had to be sliced out of
~370 KB of HTML by brace-walking.

    /api/player/<handle>                          name, profile rank, stars, coins
    /api/player/<handle>/location/<id>            scores, standing, trophyProgress
    /api/player/<handle>/badges/<id>              every badge at <id>, earned or not
    /api/location/<id>/rooms                      the location's scoring rooms
    /api/location/<id>/games                      every gamemode, with its levels
    /api/location/<id>/rooms/<room>/highscores    the location's best per level

Every one is Cloudflare-fronted like the page, so `IMPERSONATE` still applies,
and each answers a bad handle with a 404 HTML page rather than JSON.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlsplit

from curl_cffi.requests import AsyncSession

log = logging.getLogger(__name__)

API_BASE = "https://playactivate.com/api"
PLAYER_URL = API_BASE + "/player/{handle}"
PLAYER_LOCATION_URL = API_BASE + "/player/{handle}/location/{location_id}"
PLAYER_BADGES_URL = API_BASE + "/player/{handle}/badges/{location_id}"
LOCATION_ROOMS_URL = API_BASE + "/location/{location_id}/rooms"
LOCATION_GAMES_URL = API_BASE + "/location/{location_id}/games"
ROOM_HIGHSCORES_URL = API_BASE + "/location/{location_id}/rooms/{room_id}/highscores"

# The site's own badge endpoint above says which badges exist at a location and
# which the player holds, but not how far along an unearned one is, nor what it
# is worth. This community-run proxy in front of an official Activate badge API
# (a bad handle comes back as HTTP 500 with
# {"message": "(Activate API) Internal server error"}, naming its upstream)
# still has both, so it stays as an optional second leg — see poller.poll_all.
# Public and unauthenticated, keyed on the handle alone; overridable via config.
#
# It moved here from api.ryflix.ca, which now answers 503. The response shape is
# unchanged, so only the URL and the origin check below differ.
BADGE_API_BASE = "https://www.activate-scores.ca/api/activate/badges"

# Chrome TLS-fingerprint profile passed to curl_cffi. Required because
# playactivate.com (Cloudflare) rejects non-browser TLS handshakes with 403.
IMPERSONATE = "chrome124"

# `games[].type`. Co-op gamemodes are 0 and are the only ones with levels worth
# the name; competitive games are 4, each listed with a single level 0 that no
# player's `scores` ever carries. Anything else is new, and logged as such.
COOP_GAME_TYPE = 0
COMPETITIVE_GAME_TYPE = 4


class ScrapeError(Exception):
    pass


@dataclass(frozen=True)
class ScrapeResult:
    handle: str
    location_id: int
    player_name: str | None      # canonical case from the site
    # `player.rank` — a property of the profile, not of any location: it comes
    # from /api/player/<handle>, which takes no location at all. Parsed for
    # completeness; not what the dashboard shows — see `standing`.
    profile_rank: int | None
    stars: int | None
    coins: int | None
    # `playerLocation.playerRank` — the page's "RANK 47", which the page itself
    # works out as totalScore // 25000 and draws with a bar to the next one.
    # Also not the leaderboard position.
    location_player_rank: int | None
    yearly_rank: int | None
    # `playerLocation.standing` — what the page renders as "PLAYER POSITION"
    # (formerly "Your Leaderboard Position: #138"). Per location: on one live
    # pull coquitlam read 138 and langley 321 for the same handle.
    standing: int | None
    total_score: int
    yearly_score: int
    scores: list[dict[str, int]]  # [{gameId, levelId, highScore}, ...]
    # Levels the player has posted a score on, and the page's denominator. No
    # endpoint states either: the page counts `scores` and sums `levels` over
    # /games, and so do we — see `parse_games` for why that sum is not the
    # co-op catalog's.
    levels_beat: int = 0
    level_count: int | None = None
    # playerLocation.trophyProgress —
    # {tier: {completedBadges, requiredBadges, progress, earned}} for
    # bronze/silver/gold/platinum. The page's own badge tally; see
    # `badges_from_trophy_progress`. Tolerated absent, since it once was.
    trophy_progress: dict[str, Any] | None = None


@dataclass(frozen=True)
class BadgeState:
    """One badge for one player, as one source reports it.

    `badge_id` is Activate's own id and the only safe key: the catalog has two
    badges named "Untouchable 5.0" (id 111 Piperooni, id 125 Wormholes). The
    site and the badge proxy share this id space. The community badge trackers
    use their *own* ids, which do not line up — joining anything of theirs by
    id silently mismatches most rows.

    None in `progress`, `total_progress` or `stars` means "this source doesn't
    say", never zero: the site's own badge endpoint carries none of the three,
    and writing its silence over the proxy's numbers would erase them.
    """

    badge_id: int
    name: str
    description: str
    earned: bool
    # How far along an unearned badge is. `total_progress` is 0 for the four
    # badges with no denominator (Completionist, Halfway Mark, Activated, The
    # Grand Tour), where `progress` is a bare running count — never divide by it.
    progress: int | None = None
    total_progress: int | None = None
    stars: int | None = None  # the badge's own star value, unrelated to profile stars


@dataclass(frozen=True)
class RoomGame:
    """One co-op gamemode within a room, e.g. Hoops → "Barrage"."""

    game_id: int                 # games[].id; game_id // 100 == room_id
    name: str
    levels: tuple[int, ...]      # level ids, 0-based
    order: int                   # games[].roomIndex — the site's own order
    room_id: int = 0


@dataclass(frozen=True)
class LocationInfo:
    """What a location has, independent of any player: its rooms and gamemodes."""

    location_id: int
    rooms: tuple[dict[str, Any], ...]   # [{"id": 10, "name": "Hoops"}, ...], site order
    games: tuple[RoomGame, ...]         # co-op only — see `parse_games`
    # Every listed game's levels, competitive included: the page's own
    # "Levels Beat" denominator. None when the location lists no games at all.
    level_count: int | None

    @property
    def coop_levels(self) -> int:
        """Levels the catalog should hold once every room is walked."""
        return sum(len(g.levels) for g in self.games)

    def games_in(self, room_id: int) -> tuple[RoomGame, ...]:
        return tuple(g for g in self.games if g.room_id == room_id)


def parse_player_location(
    payload: Any,
    *,
    handle: str,
    location_id: int,
    profile: dict[str, Any] | None = None,
    level_count: int | None = None,
) -> ScrapeResult:
    """A ScrapeResult from /api/player/<handle>/location/<id>.

    `profile` is /api/player/<handle>, which carries the per-account numbers
    (stars, coins, profile rank). Fetched once per handle rather than once per
    location, so it is passed in. `level_count` comes from the location's
    /games for the same reason.
    """
    if not isinstance(payload, dict):
        raise ScrapeError(
            f"player-location payload is {type(payload).__name__}, expected an object"
        )
    said = _int_or_none(payload.get("locationId"))
    if said is not None and said != location_id:
        raise ScrapeError(f"asked for location {location_id}, got {said}")
    profile = profile or {}

    clean_scores = [
        {
            "gameId": int(s.get("gameId", 0)),
            "levelId": int(s.get("levelId", 0)),
            "highScore": int(s.get("highScore", 0)),
        }
        for s in payload.get("scores") or []
        if isinstance(s, dict)
    ]

    return ScrapeResult(
        handle=handle,
        location_id=location_id,
        player_name=profile.get("playerName") or payload.get("playerName"),
        profile_rank=_int_or_none(profile.get("rank")),
        stars=_int_or_none(profile.get("stars")),
        coins=_int_or_none(profile.get("coins")),
        location_player_rank=_int_or_none(payload.get("playerRank")),
        yearly_rank=_int_or_none(payload.get("yearlyRank")),
        standing=_int_or_none(payload.get("standing")),
        total_score=int(payload.get("totalScore") or 0),
        yearly_score=int(payload.get("yearlyScore") or 0),
        scores=clean_scores,
        levels_beat=sum(1 for s in clean_scores if s["highScore"] > 0),
        level_count=level_count,
        trophy_progress=(
            tp if isinstance(tp := payload.get("trophyProgress"), dict) else None
        ),
    )


def parse_games(payload: Any) -> tuple[tuple[RoomGame, ...], int | None]:
    """(co-op gamemodes, the page's level denominator) from /api/location/<id>/games.

    The two numbers differ on purpose. The page's "Levels Beat" divides by
    every listed game's levels, and the list now includes the competitive games
    — one level each, which no player's scores ever carry — so Coquitlam reads
    242/510 where its 49 co-op gamemodes hold 490. We store the page's figure
    against the snapshot, because the dashboard shows the number the page
    names. The catalog holds only the co-op ones: a competitive game has no
    levels and no leaderboard, and listing it on /games would add twenty rows
    that read "No score" forever.
    """
    if not isinstance(payload, list):
        raise ScrapeError(f"games payload is {type(payload).__name__}, expected a list")

    games: list[RoomGame] = []
    level_count = 0
    for g in payload:
        if not isinstance(g, dict):
            continue
        game_id = _int_or_none(g.get("id"))
        if game_id is None:
            continue
        levels = tuple(
            lvl
            for lvl in (_int_or_none(x) for x in (g.get("levels") or []))
            if lvl is not None
        )
        level_count += len(levels)
        kind = _int_or_none(g.get("type"))
        if kind != COOP_GAME_TYPE:
            if kind != COMPETITIVE_GAME_TYPE:
                log.warning(
                    "game %s %r has unrecognised type %r; left out of the catalog",
                    game_id, g.get("name"), g.get("type"),
                )
            continue
        room_id = _int_or_none(g.get("roomId"))
        order = _int_or_none(g.get("roomIndex"))
        games.append(
            RoomGame(
                game_id=game_id,
                name=str(g.get("name") or f"game-{game_id}"),
                levels=levels,
                order=order if order is not None else 0,
                room_id=room_id if room_id is not None else game_id // 100,
            )
        )
    # An unknown location answers [] with a 200. That is no claim about how
    # many levels it has, so it must not read as zero.
    return tuple(games), (level_count if payload else None)


def parse_rooms(payload: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, list):
        raise ScrapeError(f"rooms payload is {type(payload).__name__}, expected a list")
    return _clean_rooms(payload)


def parse_top_scores(payload: Any) -> dict[tuple[int, int], int]:
    """{(game_id, level_id): score} from a room's /highscores.

    The best score anyone *at this location* has posted, not a global best —
    Coquitlam and Langley disagree on every shared Hoops entry. Sparse: a
    missing key means nobody at the location has scored that level.
    """
    if not isinstance(payload, list):
        raise ScrapeError(
            f"highscores payload is {type(payload).__name__}, expected a list"
        )
    top: dict[tuple[int, int], int] = {}
    for s in payload:
        if not isinstance(s, dict):
            continue
        game_id = _int_or_none(s.get("gameId"))
        level_id = _int_or_none(s.get("levelId"))
        if game_id is None or level_id is None:
            continue
        top[(game_id, level_id)] = int(s.get("highScore") or 0)
    return top


def badges_from_trophy_progress(raw: Any) -> tuple[int | None, int | None]:
    """(badges earned, badges possible here) from the page's trophyProgress.

    Each tier states `completedBadges` outright — the same count on every tier
    — and `requiredBadges`, its threshold. Platinum's threshold is the number of
    badges attainable *at that location* (badges needing a room it lacks are
    excluded), so it doubles as the denominator, and it matches the length of
    that location's /badges list exactly.

    Older payloads gave only `progress`, the 2dp fraction of each threshold.
    That is still read as a fallback: the smallest threshold still below 1.0
    recovers the count to ±0.005×threshold, exact up to 75 badges and ±1 past
    gold.
    """
    if not isinstance(raw, dict):
        return (None, None)

    thresholds: list[int] = []
    completed: list[int] = []
    fractions: list[tuple[int, float]] = []
    for rec in raw.values():
        if not isinstance(rec, dict):
            continue
        required = _int_or_none(rec.get("requiredBadges"))
        if required is None or required <= 0:
            continue
        done = _int_or_none(rec.get("completedBadges"))
        progress = rec.get("progress")
        has_fraction = isinstance(progress, (int, float)) and not isinstance(progress, bool)
        if done is None and not has_fraction:
            continue
        thresholds.append(required)
        if done is not None:
            completed.append(done)
        if has_fraction:
            fractions.append((required, float(progress)))

    if not thresholds:
        return (None, None)
    possible = max(thresholds)
    if completed:
        return (max(completed), possible)

    unfinished = sorted(t for t in fractions if t[1] < 1.0)
    required, progress = unfinished[0] if unfinished else max(fractions)
    return (round(progress * required), possible)


def parse_badges(payload: Any) -> list[BadgeState]:
    """Badge states from either badge source's JSON array.

    The two share a shape — {id, name, description, status} — and the proxy adds
    progress, totalProgress and stars, which come through as None from the
    site. Split from the fetches so it can be exercised against a saved payload
    with no network.
    """
    if isinstance(payload, dict) and (payload.get("error") or payload.get("message")):
        # Both spellings: the proxy's old host said {"error": ...} with a 200,
        # the current one says {"message": ...} with a 500 that the fetch
        # already turns away. Kept for the day one of them answers 200 with an
        # error object — an empty result must never read as "no badges".
        raise FetchError(
            f"badge API error: {payload.get('error') or payload.get('message')}"
        )
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
            progress=_int_or_none(b.get("progress")),
            total_progress=_int_or_none(b.get("totalProgress")),
            stars=_int_or_none(b.get("stars")),
        )
    return [states[k] for k in sorted(states)]


def _origin_headers(base: str) -> dict[str, str]:
    """`Origin` for the badge proxy, derived from whatever base it was given.

    The proxy answers an otherwise identical request with
    403 {"message": "Forbidden: invalid origin"} unless it carries an Origin (or
    Referer) naming its own site — a server-side header check, not a TLS
    fingerprint, so `IMPERSONATE` alone does not satisfy it. Derived rather than
    configured so that repointing `badges.api_base` cannot leave a stale origin
    behind that 403s every request.
    """
    parts = urlsplit(base)
    return {"Origin": f"{parts.scheme}://{parts.netloc}"} if parts.netloc else {}


def _seg(value: Any) -> str:
    """One URL path segment. Handles are the user's typing, so encode them."""
    return quote(str(value), safe="")


async def _get_json(
    url: str,
    *,
    session: AsyncSession,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> Any:
    log.info("fetch url=%s", url)
    kw = {"headers": headers} if headers else {}
    resp = await session.get(url, impersonate=IMPERSONATE, timeout=timeout, **kw)
    if resp.status_code == 429:
        # Distinct from the catch-all below because it is not a verdict on the
        # request: the same URL succeeds once the window rolls.
        raise RateLimited(
            f"HTTP 429 for {url}",
            retry_after=retry_after_seconds(resp.headers.get("Retry-After")),
        )
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code} for {url}")
    try:
        return resp.json()
    except ValueError as e:
        # A Cloudflare challenge or an error page served with a 200.
        raise ScrapeError(f"response from {url} is not JSON: {e}") from e


async def fetch_profile(
    handle: str, *, session: AsyncSession, timeout: float = 20.0
) -> dict[str, Any]:
    """/api/player/<handle>: {playerName, rank, stars, coins, highestBadgeTrophy}."""
    payload = await _get_json(
        PLAYER_URL.format(handle=_seg(handle)), session=session, timeout=timeout
    )
    if not isinstance(payload, dict):
        raise ScrapeError(f"profile payload is {type(payload).__name__}, expected an object")
    return payload


async def fetch(
    handle: str,
    location_id: int,
    *,
    session: AsyncSession,
    timeout: float = 20.0,
    profile: dict[str, Any] | None = None,
    level_count: int | None = None,
) -> ScrapeResult:
    payload = await _get_json(
        PLAYER_LOCATION_URL.format(handle=_seg(handle), location_id=int(location_id)),
        session=session,
        timeout=timeout,
    )
    return parse_player_location(
        payload,
        handle=handle,
        location_id=location_id,
        profile=profile,
        level_count=level_count,
    )


async def fetch_location(
    location_id: int, *, session: AsyncSession, timeout: float = 20.0
) -> LocationInfo:
    """A location's rooms and gamemodes: two requests, shared by every player."""
    rooms = parse_rooms(
        await _get_json(
            LOCATION_ROOMS_URL.format(location_id=int(location_id)),
            session=session,
            timeout=timeout,
        )
    )
    games, level_count = parse_games(
        await _get_json(
            LOCATION_GAMES_URL.format(location_id=int(location_id)),
            session=session,
            timeout=timeout,
        )
    )
    return LocationInfo(
        location_id=location_id, rooms=rooms, games=games, level_count=level_count
    )


async def fetch_room_top_scores(
    location_id: int, room_id: int, *, session: AsyncSession, timeout: float = 20.0
) -> dict[tuple[int, int], int]:
    return parse_top_scores(
        await _get_json(
            ROOM_HIGHSCORES_URL.format(location_id=int(location_id), room_id=int(room_id)),
            session=session,
            timeout=timeout,
        )
    )


async def fetch_player_badges(
    handle: str, location_id: int, *, session: AsyncSession, timeout: float = 20.0
) -> list[BadgeState]:
    """Every badge attainable at `location_id`, and whether `handle` holds it.

    The site's own answer, so it is the authority on what is earned. Per
    location in the asking but not in the holding: a badge two locations share
    reads the same at both (99 of 99 shared at Coquitlam and Langley), because
    badges transfer between locations where scores do not.

    An empty list is refused rather than returned. No location offers zero
    badges, but an unknown location id answers [] with a 200, and an empty
    answer taken at its word reads as "holds nothing" — which, once the poller
    retires against it, deletes every row the player has.
    """
    url = PLAYER_BADGES_URL.format(handle=_seg(handle), location_id=int(location_id))
    states = parse_badges(await _get_json(url, session=session, timeout=timeout))
    if not states:
        raise ScrapeError(f"empty badge list from {url}")
    return states


async def fetch_badges(
    handle: str,
    *,
    session: AsyncSession,
    base: str = BADGE_API_BASE,
    timeout: float = 20.0,
) -> list[BadgeState]:
    """Every badge in the catalog for `handle` from the proxy, with progress.

    Per player, not per location: the endpoint takes only a handle, so it also
    covers badges that no tracked location offers.
    """
    return parse_badges(
        await _get_json(
            f"{base.rstrip('/')}/{_seg(handle)}",
            session=session,
            timeout=timeout,
            headers=_origin_headers(base),
        )
    )


def _max_known(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def combine_badges(per_source: list[list[BadgeState]]) -> list[BadgeState]:
    """Merge badge lists — several handles, several locations, both sources.

    A badge counts as earned if *any* list has it earned, and takes the
    furthest progress any list reports. Summing progress would invent a number
    no account actually holds — the same reason `combine_results` picks the
    best rank rather than averaging. A None (a source that doesn't say) never
    beats a number that does.
    """
    merged: dict[int, BadgeState] = {}
    for states in per_source:
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
                progress=_max_known(prior.progress, s.progress),
                total_progress=_max_known(prior.total_progress, s.total_progress),
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


class FetchError(Exception):
    """Raised when the HTTP request itself fails (status, connection, timeout)."""


class RateLimited(FetchError):
    """A 429: the server refused because we asked too often, not because the
    request was wrong.

    Separate from a plain `FetchError` because the two want opposite handling —
    a 404 or a 500 means stop asking, a 429 means ask again later. In practice
    only the badge proxy raises it: playactivate.com served 28 score pages back
    to back without complaint, while somebody's personal server let five badge
    requests through and refused the next six.

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
