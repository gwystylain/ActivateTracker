"""How tracked players rank against each other in one gamemode, per location.

Written for the Discord bot, next to `brief`: `/api/rankings` defaults to
Hoops' Trivial (the site's name for Trivia) and answers for every tracked
location, Langley and Coquitlam today.

A player's standing in a gamemode is the **sum of their best score on each of
its levels** at that location. Activate banks only the best-ever run per level
and publishes no per-gamemode total, so summing the bests is the one figure
that rewards both clearing more levels and scoring higher on them. Two
supporting numbers ride along: levels with a score, and records — levels where
the player's best is the location's top score, the `>=` rule
`public._build_records` uses. Ties share a rank (1, 1, 3).

Pure functions over rows the route reads, so the ranking and wording are
tested without a database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from .brief import DISCORD_LIMIT, MEDALS, short

DEFAULT_ROOM = "Hoops"
DEFAULT_GAME = "Trivia"


@dataclass
class Standing:
    id: int
    name: str
    rank: int
    total: int
    levels_scored: int
    records: int
    #: Best score per level, 1-based as the site numbers them.
    levels: dict[int, int] = field(default_factory=dict)


@dataclass
class LocationRanking:
    location_id: int
    name: str
    game_id: int | None
    game: str | None
    level_count: int
    standings: list[Standing]
    never_played: list[str]

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        for s in out["standings"]:
            s["levels"] = {str(k): v for k, v in s["levels"].items()}
        return out


def find_game(games: list[dict[str, Any]], room: str, game: str) -> dict[str, Any] | None:
    """The gamemode in one location's catalog rows (`room`, `name`, ...) that
    `room`/`game` name, ignoring case. An exact name wins; otherwise a prefix
    that only one gamemode in the room has — the site calls Hoops' trivia game
    "Trivial", and "trivia" should still find it."""
    room_cf, game_cf = room.strip().casefold(), game.strip().casefold()
    in_room = [g for g in games if g["room"].casefold() == room_cf]
    exact = [g for g in in_room if g["name"].casefold() == game_cf]
    if exact:
        return exact[0]
    prefixed = [g for g in in_room if g["name"].casefold().startswith(game_cf)]
    return prefixed[0] if len(prefixed) == 1 else None


def rank(
    game_id: int,
    level_ids: list[int],
    players: list[dict[str, Any]],
    top_scores: dict[int, int],
) -> tuple[list[Standing], list[str]]:
    """Standings for one gamemode at one location, and who has never scored in it.

    `players` are `{id, name, beaten}` with `beaten` the `{(game_id, level_id):
    high}` of their newest snapshot there (`public._beaten_levels`, zeros
    already dropped). `top_scores` is the location's `{level_id: top}` for this
    gamemode. Levels the catalog doesn't list are ignored, as the catalog is
    what says the gamemode has them.
    """
    wanted = set(level_ids)
    scored: list[Standing] = []
    never: list[str] = []
    for p in players:
        levels = {
            lvl: high
            for (gid, lvl), high in p["beaten"].items()
            if gid == game_id and lvl in wanted
        }
        if not levels:
            never.append(p["name"])
            continue
        scored.append(
            Standing(
                id=p["id"],
                name=p["name"],
                rank=0,
                total=sum(levels.values()),
                levels_scored=len(levels),
                records=sum(
                    1 for lvl, high in levels.items()
                    if top_scores.get(lvl) and high >= top_scores[lvl]
                ),
                levels={lvl + 1: high for lvl, high in sorted(levels.items())},
            )
        )

    scored.sort(key=lambda s: (-s.total, s.name))
    for i, s in enumerate(scored):
        s.rank = scored[i - 1].rank if i and s.total == scored[i - 1].total else i + 1
    return scored, sorted(never)


def render(
    room: str, game: str, rankings: list[LocationRanking], *, today: date | None = None
) -> str:
    """Discord Markdown: a heading, then a section per location."""
    lines = [f"# 🏆 {room} {game} rankings"]
    sub = "-# best score on each level, added up"
    lines.append(sub + (f" · as of {today:%b} {today.day}" if today else ""))

    for loc in rankings:
        lines += ["", f"## {loc.name}"]
        if loc.game_id is None:
            lines.append(f"-# No {game} here.")
            continue
        if not loc.standings:
            lines.append("-# Nobody has scored here yet.")
        for s in loc.standings:
            emoji = MEDALS[s.rank - 1] if s.rank <= len(MEDALS) else f"{s.rank}."
            line = f"{emoji} **{s.name}** · {short(s.total)} · {s.levels_scored}/{loc.level_count} levels"
            if s.records:
                line += f" · 👑 {s.records}"
            lines.append(line)
        if loc.never_played:
            lines.append("-# Never played: " + ", ".join(f"**{n}**" for n in loc.never_played))

    text = "\n".join(lines)
    if len(text) > DISCORD_LIMIT:
        # Whole lines from the bottom, so no number is ever cut in half.
        rows = text.split("\n")
        while len("\n".join(rows)) > DISCORD_LIMIT - 2:
            rows.pop()
        text = "\n".join(rows) + "\n…"
    return text
