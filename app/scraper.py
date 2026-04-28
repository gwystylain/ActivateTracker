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

from curl_cffi.requests import AsyncSession

log = logging.getLogger(__name__)

BASE_URL = "https://playactivate.com/scores/{handle}/{location_id}/{slug}/scores"

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


def extract_player_blob(html: str) -> dict[str, Any]:
    """Slice the JSON object that begins at  "player":{"player":{...  ."""
    match = _ANCHOR.search(html)
    if match is None:
        raise ScrapeError("player blob anchor not found in HTML")

    # Position cursor on the '{' that opens the outer player value.
    obj_start = html.find("{", match.start() + len('"player"'))
    if obj_start < 0:
        raise ScrapeError("opening brace for player blob not found")

    end = _find_matching_brace(html, obj_start)
    if end < 0:
        raise ScrapeError("matching brace for player blob not found")

    raw = html[obj_start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ScrapeError(f"player blob is not valid JSON: {e}") from e


def _find_matching_brace(s: str, start: int) -> int:
    """Return index of the '}' matching the '{' at `start`, accounting for strings."""
    if s[start] != "{":
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
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def parse_html(html: str, *, handle: str, location_id: int, slug: str) -> ScrapeResult:
    blob = extract_player_blob(html)
    inner_player = blob.get("player") or {}
    player_loc = blob.get("playerLocation") or {}
    scores = player_loc.get("scores") or []

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
        scores=[
            {
                "gameId": int(s.get("gameId", 0)),
                "levelId": int(s.get("levelId", 0)),
                "highScore": int(s.get("highScore", 0)),
            }
            for s in scores
            if isinstance(s, dict)
        ],
    )


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
