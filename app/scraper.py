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
from typing import Any
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

log = logging.getLogger(__name__)

BASE_URL = "https://playactivate.com/scores/{handle}/{location_id}/{slug}/scores"
ROOM_URL = (
    "https://playactivate.com/scores/{handle}/{location_id}/{slug}/{room}/scores"
)

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
    player_rank: int | None      # the player's profile rank (cross-location)
    stars: int | None
    coins: int | None
    location_player_rank: int | None
    yearly_rank: int | None
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
        player_rank=_int_or_none(inner_player.get("rank")),
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
    )


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
        player_rank=_best([r.player_rank for r in results]),
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
    )
