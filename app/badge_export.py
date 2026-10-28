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

**Nothing is written that the reader already knows or can't use**, because a
paste into Claude is paid for by the token. Badges are grouped by who lacks
them, so no badge repeats its own "missing" line. A Where line appears only
when it says something: at a chosen location the scope already settled it,
unless the answer there is "can't tell"; across several locations it is left
out where every one of them is a yes. A tip or watch-out that several listed
badges share (the X for X and visit-long families carry the same text) is
written once, above the badges. Gamemode rules are written only for gamemodes a
listed badge names, since the beaten levels are what the others contribute. A
hint goes when its answer is there, "any room" and "Depends" say nothing, and
star values buy nothing since badges stopped paying in stars.

Pure, over the /api/badge-data payload and `public._badge_locations`' per-location
catalog, so it is tested without a database.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from itertools import groupby
from typing import Any

from . import master_document

# The page's "Any of my locations" — the one location choice that isn't an id.
TRACKED = "tracked"

# The document's own scale, in its own order. Unrecorded sorts after all of it.
DIFFICULTIES = ("Easy", "Medium", "Hard", "Very Hard")

# The brief names no badge. Claude reads a named example as one we want, and it
# planned a route around The Marathon for two players who both held it, because
# the stacking example here used to be that badge.
BRIEF = """\
I play Activate — rooms of short cooperative games, each gamemode with numbered \
levels — with the people under "The group". Below is every badge we still lack \
{scope}, then every gamemode {there} with the levels each of us has already \
beaten. Please plan our next visit so we collect as many of these badges as we \
can in as few games as we can.

What I'd like back:

1. An ordered route: for each game, the room, gamemode and level, which badges \
it earns or advances, and for whom.
2. Stack badges wherever one game can count for several — say, a level cleared \
the particular way one badge asks for, which is also one of the several \
different wins another badge wants.
3. Where badges need games "in a row", say where each streak starts, what is in \
it, and what would break it.
4. Badges that run for a whole visit — say what to do from the first game on.
5. Who should play each game when it matters: a badge's player count, or which \
of us still needs it.
6. What to leave for another visit and why: too hard for now, needs more \
players, or can't be done here.

Wherever you list badges in a block or table, put a concise description of \
what each one asks for beside its name, so the plan can be followed without \
this list beside it.

How to read the data:

- Only the badges under "Badges still missing" are wanted. A badge named \
anywhere else but not listed there — in a tip, a note or an "Overlaps with" \
line — is one all of us already hold{absent}, so don't plan for it.
- As far as we know, a badge goes to everyone in the game that meets its \
condition, so one win can earn it for several of us. Badges are grouped by who \
still lacks them; anyone else already holds that badge but can still play to \
make up the numbers.
- "Requirement" is Activate's own wording and is the authority on what a badge \
needs. Room, difficulty, player count, tips and answers come from community \
documents and the players' Discord, which can be wrong or out of date; \
"Players" is their best player count, not a hard rule.
- No "Room" line means any room will do. {where_rule}
- Tips that apply to several badges are written once, under "Notes for several \
badges".
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
    # Most of us missing first, and the same set of us together, so the rows
    # fall into groups; easiest first within a group.
    rows.sort(key=lambda r: (
        -len(r[1]),
        [players.index(p) for p in r[1]],
        _difficulty_rank(r[0]),
        r[0]["name"].lower(),
        r[0]["badge_id"],
    ))

    if chosen:
        where, there = f"at {chosen['name']}", "there"
        where_rule = "Everything listed can be done here unless a \"Where\" line says otherwise."
        absent = " or one that can't be done here"
    elif choice == TRACKED and locations:
        where = f"at {_or([l['name'] for l in locations])}"
        there = "at each of those"
        where_rule = "No \"Where\" line means every one of those locations can host it."
        absent = " or one none of those locations can host"
    else:
        where, there = "", "at the locations we play"
        where_rule = "No \"Where\" line means every location we play can host it."
        absent = ""
    if not locations:
        there = "where we play (none is catalogued yet, so that part is missing)"

    shared = _shared_notes(rows)
    grouped = len(players) > 1
    out = [
        f"# Activate badge plan — {names}" + (f" {where}" if where else ""),
        "",
        f"Exported from activateTracker on {today.isoformat()}.",
        "",
        BRIEF.format(
            scope=where or "anywhere", there=there, where_rule=where_rule, absent=absent
        ),
        "",
        "## The group",
        "",
        *(_player_line(p) for p in players),
    ]
    if shared:
        out += ["", "## Notes for several badges", "", *_shared_lines(shared)]
    out += [
        "",
        f"## Badges still missing ({len(rows)})",
        "",
        "Grouped by who still lacks them, then easiest first." if grouped else "Easiest first.",
    ]
    if not rows:
        out += ["", "None — every badge in scope is already held by all of us."]
    for _, group in groupby(rows, key=lambda r: [p["id"] for p in r[1]]):
        group = list(group)
        if grouped:
            out += ["", f"### {_group_heading(group[0][1], players)} ({len(group)})"]
        for badge, missing in group:
            out += ["", *_badge_block(
                badge, missing, states, scope, locations, chosen, shared,
                heading="####" if grouped else "###",
            )]

    named = {pair for badge, _ in rows for pair in badge.get("games") or ()}
    described: set[tuple[str, str]] = set()
    for loc in scope:
        out += ["", *_gamemodes(loc, players, named, described)]
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


def _group_heading(missing, players) -> str:
    if len(missing) == len(players):
        return "Missing for all of us"
    names = _and([p["display_name"] for p in missing])
    return f"Missing for {names}" + (" only" if len(missing) == 1 else "")


# The two repeatable fields, in the order a badge block writes them.
_NOTE_FIELDS = (("tips", "Tip"), ("watch_out", "Watch out"))


def _shared_notes(rows) -> dict[tuple[str, str], list[str]]:
    """{(field, text): badge names} for every tip or watch-out that two or more
    listed badges carry word for word — badge_tips gives whole families (X for
    X, Deja Vu, the visit-long badges) one shared text, and writing it under
    each of them is the same sentence paid for again and again."""
    counts: Counter[tuple[str, str]] = Counter()
    names: dict[tuple[str, str], list[str]] = {}
    for badge, _ in rows:
        for field, _label in _NOTE_FIELDS:
            for text in dict.fromkeys(badge.get(field) or ()):
                counts[(field, text)] += 1
                names.setdefault((field, text), []).append(badge["name"])
    return {key: names[key] for key, n in counts.items() if n > 1}


def _shared_lines(shared) -> list[str]:
    labels = dict(_NOTE_FIELDS)
    return [
        f"- **{labels[field]}** for {', '.join(sorted(names, key=_natural))}: {text}"
        for (field, text), names in shared.items()
    ]


def _natural(name: str) -> list[Any]:
    """"5 For 5" before "10 For 10", "Deja Vu" before "Deja Vu 2.0"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _badge_block(badge, missing, states, scope, locations, chosen, shared, *, heading) -> list[str]:
    lines = [f"{heading} {badge['name']}", ""]

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
    if badge.get("players") and badge["players"] != "Depends":
        grade.append(f"Players: {badge['players']}")
    if grade:
        lines.append("- " + " · ".join(grade))

    if badge.get("overlapping"):
        lines.append(f"- Overlaps with: {badge['overlapping']}")

    where = _where(badge, locations, chosen)
    if where:
        lines.append(f"- Where: {where}")
    lines += _facts(badge, missing, scope)

    progress = _progress(badge, missing, states)
    if progress:
        lines.append(f"- Progress: {progress}")

    if badge.get("notes"):
        lines.append(f"- Notes: {badge['notes']}")
    # The hint exists to nudge someone solving it themselves; next to the
    # answer it only costs words.
    if badge.get("hint") and not badge.get("giveaway"):
        lines.append(f"- Hint: {badge['hint']}")
    if badge.get("giveaway"):
        lines.append(f"- Answer: {badge['giveaway']}")
    for field, label in _NOTE_FIELDS:
        lines += [
            f"- {label}: {text}"
            for text in badge.get(field) or ()
            if (field, text) not in shared
        ]
    return lines


def _rooms(badge) -> str | None:
    """"Hide (Relay) + Mega Grid (Mega Relay)" when every room is needed, "Mega
    Laser or Trench" when any will do, and nothing for a badge tied to no room
    — the brief says that means any."""
    rooms = badge.get("rooms") or ()
    if not rooms:
        return None
    named: dict[str, list[str]] = {}
    for room, game in badge.get("games") or ():
        named.setdefault(room, []).append(game)
    parts = [f"{r} ({', '.join(named[r])})" if named.get(r) else r for r in rooms]
    if badge.get("rooms_mode") == "all":
        return " + ".join(parts) + (" — needs all of them" if len(parts) > 1 else "")
    return " or ".join(parts)


def _where(badge, locations, chosen) -> str | None:
    """Only an answer the reader doesn't already have.

    At a chosen location every "no" was filtered out, so the one thing left to
    say is "can't tell" (the photo room, at a location with no stored list).
    Otherwise every tracked location's answer, but only when they aren't all
    yes — a badge that can't be done somewhere is worth knowing about for the
    next visit elsewhere, and one doable everywhere needs no line at all.
    """
    where = badge.get("where") or {}
    answers = [
        (loc, w) for loc in ([chosen] if chosen else locations)
        if (w := where.get(str(loc["location_id"])))
    ]
    if all(w["status"] == "yes" for _, w in answers):
        return None
    if chosen:
        return f"can't tell if {chosen['name']} can host it — {answers[0][1]['why']}"
    parts = []
    for loc, w in answers:
        part = f"{loc['name']} {w['status']}"
        if w.get("why"):
            part += f" ({w['why']})"
        parts.append(part)
    return "; ".join(parts)


def _facts(badge, missing, scope) -> list[str]:
    """The location's own lines — Riddle 7.0's S games, the level count behind
    Activated — with progress only for those still missing the badge: what a
    holder has beaten says nothing about it. With one location in scope its
    name is already in the title."""
    lines = []
    here = badge.get("here") or {}
    for loc in scope:
        for f in here.get(str(loc["location_id"])) or ():
            said = [
                f"{p['display_name']}: {text}"
                for p in missing
                if (text := (f.get("players") or {}).get(str(p["id"])))
            ]
            place = "" if len(scope) == 1 else f" at {loc['name']}"
            line = f"- {f['label']}{place}: {f['text']}"
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


def _gamemodes(
    loc, players, named: set[tuple[str, str]], described: set[tuple[str, str]]
) -> list[str]:
    """Every co-op gamemode at one location, room by room in the site's order,
    with each player's beaten levels. Rules are written only for a gamemode a
    listed badge names (`named`), and only the first time it appears, so a
    second location doesn't repeat them."""
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
        pair = (g["room"], g["name"])
        if ref.get("description") and pair in named and pair not in described:
            described.add(pair)
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
