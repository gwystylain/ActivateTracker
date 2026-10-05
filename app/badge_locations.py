"""Where a badge can be done, and what a location adds to its detail.

Pure functions over one location's catalog — its `location_games` rows, as
`{"room", "game_id", "name", "levels"}` dicts in the site's own order — and the
levels each player has beaten there, so the rules are testable without a
database. `public.badge_data` gathers both and hangs the answers on each badge.

**Availability checks the gamemode, not just the room.** A badge names a room
and, usually, a gamemode in it (`badge_reference`'s `games`). Rooms change what
they run: Langley has a Laser room, but it runs Sneak and Chopper, and Steady
Stream wants Photon Rush. So a room counts only if it still runs what the badge
names; a badge that names no gamemode (Snake Island's competitive Tails, which
the site never catalogs) is placed by its room alone.

Three answers, not two. A room the master document doesn't know as a scoring
room and the location doesn't list is "unknown", never "no": the site lists only
scoring rooms, so the photo room is missing from every location whether or not
the building has one. A badge with no room at all gets no answer — it can be
done anywhere.

**Facts** are a few badges whose answer depends on the location: Riddle 7.0's
list of S games, the level counts behind Activated and Halfway Mark, the rooms
The Grand Tour has to cross, the size of Completionist's checklist. Each fact is
`{"label", "text", "players": {player_id: text}}`, the per-player text being
progress towards it from that player's newest snapshot there.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import Any

from .badge_reference import norm
from .master_document import GAMEMODES

Game = dict[str, Any]          # {"room", "game_id", "name", "levels"}
Beaten = dict[int, set[tuple[int, int]]]   # player id -> {(game_id, level_id)}


def availability(badge: dict[str, Any], games: list[Game]) -> dict[str, Any] | None:
    """{"status": "yes" | "no" | "unknown", "why": str | None} at one location.

    None for a badge with no room, which is location-independent. `why` is a
    short clause for the page to put after the location's name, and is None
    exactly when the answer is yes.
    """
    rooms = tuple(badge.get("rooms") or ())
    if not rooms:
        return None

    running: dict[str, set[str]] = {}
    for g in games:
        running.setdefault(g["room"], set()).add(g["name"])
    wanted: dict[str, list[str]] = {}
    for room, game in badge.get("games") or ():
        wanted.setdefault(room, []).append(game)

    verdicts = [_room_verdict(room, wanted.get(room, []), running) for room in rooms]
    if badge.get("rooms_mode") == "all":
        # Every room is needed, so the worst one decides: a missing room is a
        # firmer answer than one we can't see.
        blocking = [v for v in verdicts if v[0] == "no"] or [
            v for v in verdicts if v[0] == "unknown"
        ]
    else:
        if any(status == "yes" for status, _ in verdicts):
            return {"status": "yes", "why": None}
        blocking = [v for v in verdicts if v[0] == "unknown"] or verdicts
    if not blocking:
        return {"status": "yes", "why": None}
    return {"status": blocking[0][0], "why": "; ".join(why for _, why in blocking)}


def _room_verdict(
    room: str, wanted: list[str], running: dict[str, set[str]]
) -> tuple[str, str | None]:
    if room not in running:
        if room in GAMEMODES:
            return "no", f"no {room} room"
        return "unknown", (
            f"{room} isn't a room the site keeps scores for, so it doesn't say "
            f"which locations have one"
        )
    missing = [g for g in wanted if g not in running[room]]
    if missing:
        return "no", (
            f"{room} room runs {_and(sorted(running[room]))}, not {_and(missing)}"
        )
    return "yes", None


def facts(
    badge: dict[str, Any], games: list[Game], beaten: Beaten
) -> list[dict[str, Any]]:
    """The location-specific lines for one badge's detail panel, if it has any."""
    rule = _RULES.get(norm(badge.get("name")))
    if rule is None or not games:
        return []
    return rule(games, beaten)


# ---------- the rules ----------

_LEVEL_7 = 6   # level ids are 0-based; the site and every badge count from 1


def _riddle_7(games: list[Game], beaten: Beaten) -> list[dict[str, Any]]:
    """"…do it back to back in all games that start with S." The answer
    depends entirely on which rooms the building has — Langley's Pipes and
    Laser add Scramble and Sneak — so it is worth spelling out. Only the
    cooperative games count, which is what the catalog holds."""
    s_games = sorted(
        (g for g in games if g["name"][:1].upper() == "S" and _LEVEL_7 in g["levels"]),
        key=lambda g: g["name"].lower(),
    )
    if not s_games:
        return [{"label": "S games", "text": "none here", "players": {}}]
    players = {}
    for pid, done in beaten.items():
        left = [g["name"] for g in s_games if (g["game_id"], _LEVEL_7) not in done]
        players[str(pid)] = (
            f"level 7 not beaten yet in {', '.join(left)}" if left
            else f"level 7 already beaten in all {len(s_games)}, one at a time"
        )
    return [{
        "label": "S games",
        "text": f"{', '.join(g['name'] for g in s_games)} — {len(s_games)} in all",
        "players": players,
    }]


def _all_levels(games: list[Game]) -> set[tuple[int, int]]:
    return {(g["game_id"], lvl) for g in games for lvl in g["levels"]}


def _levels_towards(need: int, games: list[Game], beaten: Beaten) -> dict[str, str]:
    """Each player's beaten count against `need`. Intersected with the catalog
    so the count can never run past the total it is shown against."""
    levels = _all_levels(games)
    out = {}
    for pid, done in beaten.items():
        have = len(levels & done)
        out[str(pid)] = (
            f"{have} beaten, {need - have} to go" if have < need
            else f"{have} beaten — enough"
        )
    return out


def _activated(games: list[Game], beaten: Beaten) -> list[dict[str, Any]]:
    """"Beat all game levels" means all of *this* location's: the badge tip
    suggests going for it wherever the count is easier to reach."""
    total = len(_all_levels(games))
    return [{
        "label": "Levels",
        "text": f"{total} to beat",
        "players": _levels_towards(total, games, beaten),
    }]


def _halfway(games: list[Game], beaten: Beaten) -> list[dict[str, Any]]:
    # Rounded up, so an odd count is never reported as done a level early.
    total = len(_all_levels(games))
    need = math.ceil(total / 2)
    return [{
        "label": "Halfway",
        "text": f"{need} of {total} levels",
        "players": _levels_towards(need, games, beaten),
    }]


def _grand_tour(games: list[Game], beaten: Beaten) -> list[dict[str, Any]]:
    """"Win a level from every room in a row" — the checklist is the room list,
    in the site's own order."""
    rooms = list(dict.fromkeys(g["room"] for g in games))
    return [{
        "label": "Rooms",
        "text": f"{', '.join(rooms)} — {len(rooms)} in all",
        "players": {},
    }]


def _completionist(games: list[Game], beaten: Beaten) -> list[dict[str, Any]]:
    """Only a floor: the badge counts competitive games too, and those have no
    levels, so the site's catalog never lists them."""
    return [{
        "label": "Games",
        "text": (
            f"{len(games)} cooperative, plus the competitive ones, which the "
            f"site doesn't list"
        ),
        "players": {},
    }]


_RULES: dict[str, Callable[[list[Game], Beaten], list[dict[str, Any]]]] = {
    norm("Riddle 7.0"): _riddle_7,
    norm("Activated"): _activated,
    norm("Halfway Mark"): _halfway,
    norm("The Grand Tour"): _grand_tour,
    norm("Completionist"): _completionist,
}


def _and(items: Iterable[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
