"""The /badges export: what the ticked players still lack, written up for Claude.

The badge farmer already ranks what is missing by how many players one go would
pay. What a ranking can't see is that one game can count for several badges at
once — a careful Zones level 5 is Tiptoeing *and* one of 5 For 5's five, and The
Marathon's three relays are three more — because that takes reading each
badge's wording against the others'. The export hands that reading to Claude: a
brief asking for a route, then every missing badge with Activate's wording and
the community detail, then every gamemode at the location with the levels each
player has beaten, which is the evidence for "a level 5 we can win".

**Everything the selected players lack goes in**, whatever the page's room,
difficulty and search filters say: stacking only shows up across rooms and
grades, so a route built from a filtered list would miss exactly what it is
for. The location does narrow it, the way the page's Location filter does,
because a plan is for a building — with one difference. "Any of my locations"
on the page keeps a badge somebody already holds even where no tracked location
can host it, since badges transfer; a route can't visit such a badge, so here it
is dropped.

Pure, over the /api/badge-data payload and `public._badge_locations`' per-location
catalog, so it is tested without a database.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from . import master_document

# The page's "Any of my locations" — the one location choice that isn't an id.
TRACKED = "tracked"

# The document's own scale, in its own order. Unrecorded sorts after all of it.
DIFFICULTIES = ("Easy", "Medium", "Hard", "Very Hard")

BRIEF = """\
I play Activate — rooms of short cooperative games, each gamemode with numbered \
levels — with the people under "The group". Below is every badge we still lack \
{scope}, then every gamemode {there} with the levels each of us has already \
beaten. Please plan our next visit so we collect as many of these badges as we \
can in as few games as we can.

What I'd like back:

1. An ordered route: for each game, the room, gamemode and level, which badges \
it earns or advances, and for whom.
2. Stack badges wherever one game can count for several. For example, a Zones \
level 5 cleared without stepping on a wrong colour is Tiptoeing and also one of \
the five different level-5 wins 5 For 5 wants, and The Marathon's three relays \
at level 5, played back to back, can be three more of those five.
3. Where badges need games "in a row", say where each streak starts, what is in \
it, and what would break it.
4. Badges that run for a whole visit (Keener, for one) — say what to do from \
the first game on.
5. Who should play each game when it matters: a badge's player count, or which \
of us still needs it.
6. What to leave for another visit and why: too hard for now, needs more \
players, or can't be done here.

How to read the data:

- As far as we know, a badge goes to everyone in the game that meets its \
condition, so one win can earn it for several of us. Someone who already holds \
a badge can still play to make up the numbers.
- "Requirement" is Activate's own wording and is the authority on what a badge \
needs. Room, difficulty, player count, tips and answers come from community \
documents and the players' Discord, which can be wrong or out of date; \
"Players" is their best player count, not a hard rule.
- Treat "in a row" as consecutive games played by the same person, so a game in \
between — a loss included — breaks the streak unless it counts too.
- Levels are numbered from 1, as the site shows them. A level listed as beaten \
means that player has a score on it: the best evidence we have of what we can \
win again.
- "Progress" is Activate's own count towards a badge, where it keeps one."""


def build(
    *,
    players: list[dict[str, Any]],
    badges: list[dict[str, Any]],
    states: dict[str, dict[str, dict[str, Any]]],
    locations: list[dict[str, Any]],
    choice: int | str | None,
    today: date,
) -> str:
    """The export as Markdown.

    `players` are /api/badge-data's player entries, already narrowed to the
    selection; `badges` and `states` are that payload's, `where` and `here`
    included. `locations` are `_badge_locations` entries — `location_id`,
    `name`, `games` and `beaten` — for every tracked location. `choice` is a
    location id, `TRACKED`, or None for anywhere.
    """
    chosen = next((l for l in locations if l["location_id"] == choice), None)
    scope = [chosen] if chosen else locations
    names = _and([p["display_name"] for p in players])

    rows = []
    for badge in badges:
        missing = [p for p in players if not _earned(states, p, badge)]
        if missing and _doable(badge, chosen, choice, locations):
            rows.append((badge, missing))
    rows.sort(key=lambda r: (
        -len(r[1]),
        _difficulty_rank(r[0]),
        r[0]["name"].lower(),
        r[0]["badge_id"],
    ))

    if chosen:
        where, there = f"at {chosen['name']}", "there"
    elif choice == TRACKED and locations:
        where = f"at {_or([l['name'] for l in locations])}"
        there = "at each of those"
    else:
        where, there = "", "at the locations we play"
    if not locations:
        there = "where we play (none is catalogued yet, so that part is missing)"

    out = [
        f"# Activate badge plan — {names}" + (f" {where}" if where else ""),
        "",
        f"Exported from activateTracker on {today.isoformat()}.",
        "",
        BRIEF.format(scope=where or "anywhere", there=there),
        "",
        "## The group",
        "",
        *(_player_line(p) for p in players),
        "",
        f"## Badges still missing ({len(rows)})",
        "",
        "Most of us missing it first, then easiest first.",
    ]
    if not rows:
        out += ["", "None — every badge in scope is already held by all of us."]
    for badge, missing in rows:
        out += ["", *_badge_block(badge, missing, players, states, scope, locations)]

    described: set[tuple[str, str]] = set()
    for loc in scope:
        out += ["", *_gamemodes(loc, players, described)]
    return "\n".join(out).rstrip() + "\n"


# ---------- which badges ----------

def _state(states, player, badge) -> dict[str, Any] | None:
    return states.get(str(player["id"]), {}).get(str(badge["badge_id"]))


def _earned(states, player, badge) -> bool:
    st = _state(states, player, badge)
    return bool(st and st["earned"])


def _doable(badge, chosen, choice, locations) -> bool:
    """Only a definite "no" rules a badge out — `unknown` (the photo room) and a
    roomless badge's silence both stay in, the same as on the page."""
    where = badge.get("where") or {}
    if chosen:
        w = where.get(str(chosen["location_id"]))
        return not w or w["status"] != "no"
    if choice == TRACKED:
        answers = [where[k] for l in locations if (k := str(l["location_id"])) in where]
        return not answers or any(a["status"] != "no" for a in answers)
    return True


def _difficulty_rank(badge) -> int:
    d = badge.get("difficulty")
    return DIFFICULTIES.index(d) if d in DIFFICULTIES else len(DIFFICULTIES)


# ---------- the writing ----------

def _player_line(p) -> str:
    line = f"- **{p['display_name']}** (`{p['handle']}`): {p['earned']} of {p['possible']} badges"
    if p.get("tier"):
        line += f", {p['tier']} trophy"
    if p.get("next_tier"):
        line += f", {p['to_next']} to {p['next_tier']}"
    return line + "."


def _badge_block(badge, missing, players, states, scope, locations) -> list[str]:
    lines = [f"### {badge['name']}", ""]

    holders = [p for p in players if p not in missing]
    line = f"- Missing: {', '.join(p['display_name'] for p in missing)}"
    if holders:
        line += f" (already held by {', '.join(p['display_name'] for p in holders)})"
    lines.append(line)

    if badge.get("description"):
        lines.append(f"- Requirement: {badge['description']}")

    rooms = _rooms(badge)
    if rooms:
        lines.append(f"- Room: {rooms}")
    if badge.get("level"):
        lines.append(f"- Game/level: {badge['level']}")

    grade = []
    if badge.get("difficulty"):
        grade.append(
            f"Difficulty: {badge['difficulty']}"
            + (" (our estimate, not the documents')" if badge.get("difficulty_estimated") else "")
        )
    if badge.get("players"):
        grade.append(f"Players: {badge['players']}")
    if badge.get("stars"):
        grade.append(f"Stars: {badge['stars']}")
    if grade:
        lines.append("- " + " · ".join(grade))

    if badge.get("overlapping"):
        lines.append(f"- Overlaps with: {badge['overlapping']}")

    where = _where(badge, locations)
    if where:
        lines.append(f"- Where: {where}")
    lines += _facts(badge, missing, scope)

    progress = _progress(badge, missing, states)
    if progress:
        lines.append(f"- Progress: {progress}")

    if badge.get("notes"):
        lines.append(f"- Notes: {badge['notes']}")
    if badge.get("hint"):
        lines.append(f"- Hint: {badge['hint']}")
    if badge.get("giveaway"):
        lines.append(f"- Answer: {badge['giveaway']}")
    lines += [f"- Tip: {t}" for t in badge.get("tips") or ()]
    lines += [f"- Watch out: {w}" for w in badge.get("watch_out") or ()]
    return lines


def _rooms(badge) -> str | None:
    """"Hide (Relay) + Mega Grid (Mega Relay)" when every room is needed, "Mega
    Laser or Trench" when any will do. A badge the documents grade but tie to
    no room can be done in any of them; one they don't cover at all says
    nothing."""
    rooms = badge.get("rooms") or ()
    if not rooms:
        covered = badge.get("difficulty") or badge.get("players")
        return "any" if covered else None
    named: dict[str, list[str]] = {}
    for room, game in badge.get("games") or ():
        named.setdefault(room, []).append(game)
    parts = [f"{r} ({', '.join(named[r])})" if named.get(r) else r for r in rooms]
    if badge.get("rooms_mode") == "all":
        return " + ".join(parts) + (" — needs all of them" if len(parts) > 1 else "")
    return " or ".join(parts)


def _where(badge, locations) -> str | None:
    """Every tracked location's answer, whatever the scope — a badge that can't
    be done here is worth knowing about for the next visit elsewhere."""
    where = badge.get("where") or {}
    parts = []
    for loc in locations:
        w = where.get(str(loc["location_id"]))
        if not w:
            continue
        part = f"{loc['name']} {w['status']}"
        if w.get("why"):
            part += f" ({w['why']})"
        parts.append(part)
    return "; ".join(parts) or None


def _facts(badge, missing, scope) -> list[str]:
    """The location's own lines — Riddle 7.0's S games, the level count behind
    Activated — with progress only for those still missing the badge: what a
    holder has beaten says nothing about it."""
    lines = []
    here = badge.get("here") or {}
    for loc in scope:
        for f in here.get(str(loc["location_id"])) or ():
            said = [
                f"{p['display_name']}: {text}"
                for p in missing
                if (text := (f.get("players") or {}).get(str(p["id"])))
            ]
            line = f"- {f['label']} at {loc['name']}: {f['text']}"
            if said:
                line += f" ({'; '.join(said)})"
            lines.append(line)
    return lines


def _progress(badge, missing, states) -> str | None:
    parts = []
    for p in missing:
        st = _state(states, p, badge)
        if not st or not st.get("progress"):
            continue
        if st.get("total_progress"):
            parts.append(f"{p['display_name']} {st['progress']}/{st['total_progress']}")
        else:
            # Activated and its kin count without a target; a bare running
            # count is all there is.
            parts.append(f"{p['display_name']} {st['progress']} so far")
    return ", ".join(parts) or None


def _gamemodes(loc, players, described: set[tuple[str, str]]) -> list[str]:
    """Every co-op gamemode at one location, room by room in the site's order,
    with each player's beaten levels. A gamemode's rules are written out the
    first time it appears, so a second location doesn't repeat them."""
    lines = [
        f"## Gamemodes at {loc['name']}",
        "",
        "Levels each of us has beaten, numbered from 1.",
    ]
    if not loc["games"]:
        return lines + ["", "Not catalogued yet."]

    room = None
    for g in loc["games"]:
        if g["room"] != room:
            room = g["room"]
            lines += ["", f"### {room}", ""]
        ref = master_document.lookup(g["room"], g["name"])
        head = f"- **{g['name']}**, {len(g['levels'])} levels"
        if ref.get("optimal_players"):
            head += f", best with {ref['optimal_players']}"
            if ref.get("optimal_disputed"):
                head += " (no clear consensus)"
        beaten = [f"{p['display_name']} {_beaten(g, loc['beaten'].get(p['id']))}" for p in players]
        lines.append(head + " — " + "; ".join(beaten))
        if ref.get("description") and (g["room"], g["name"]) not in described:
            described.add((g["room"], g["name"]))
            lines.append(f"  - {ref['description']}")
    return lines


def _beaten(game, done: set[tuple[int, int]] | None) -> str:
    if done is None:
        return "no scores here"
    levels = sorted(lvl for lvl in game["levels"] if (game["game_id"], lvl) in done)
    if not levels:
        return "none"
    if len(levels) == len(game["levels"]):
        return "all"
    return _ranges([lvl + 1 for lvl in levels])


def _ranges(nums: list[int]) -> str:
    """[1, 2, 3, 5] -> "1–3, 5"."""
    spans: list[list[int]] = []
    for n in nums:
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in spans)


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _or(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " or " + items[-1]
