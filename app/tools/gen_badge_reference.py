"""Regenerate app/badge_reference.py from the two community sources.

    python -m app.tools.gen_badge_reference \
        "Activate Master Document.md" badges.html > app/badge_reference.py

Neither source is committed — they are other people's documents, and both are
updated independently of this repo. The *generated* module is committed, the way
app/master_document.py is, so nothing at runtime depends on having them.

Inputs:

- The community *Activate Games Master Document*, exported as markdown. Its
  "List and descriptions" section is the richer source: room, level, tips,
  what to watch out for, and the Easter Egg / Riddle hints and answers.
- `activate.ryflix.ca/badges.html`, whose inline `const BADGES = [...]` adds a
  difficulty rating, an optimal player count, overlapping badges and notes.

Where the two disagree about a room the document wins; see badge_reference's
module docstring for why, and for the conflicts that resolves.

Each badge's gamemodes are matched against app/master_document.py, so rerun
this after editing that too.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.master_document import GAMEMODES

# Difficulty grades neither document records, filled in here so the column has
# no holes. These are *estimates* and are marked as such all the way to the page
# — they never overwrite a sourced grade, and the moment either document grades
# one of these the estimate steps aside (asserted in build()). A badge neither
# document has an entry for at all needs a "name" and "description" here too,
# Activate's own wording, to key the record.
#
# Empty since October 2026, with both of its estimates retired the two ways an
# estimate should go: ryflix graded Photobomb (Easy, as estimated), and Mascot
# left the badge API — the document now files it with the retired badges.
ESTIMATES: dict[str, dict[str, Any]] = {}

_ENTRY = re.compile(r"^\* \*\*(.+?):\*\*\s*(.*?)\s*$")
_FIELD = re.compile(r"^\s+\* ##\s*([A-Za-z/ ]+?):\s*(.*?)\s*$")
_MULTI = {"Tip", "Watch Out", "Fun Fact"}
# Google Docs' markdown export backslash-escapes punctuation.
_ESCAPED = re.compile(r"\\([!#()\-\[\]*_.])")


def _clean(s: str) -> str:
    return _ESCAPED.sub(r"\1", s).strip()


_ANCHOR_TAG = re.compile(r"<a\b[^>]*href=\"([^\"]*)\"[^>]*>(.*?)</a>", re.I | re.S)
_ANY_TAG = re.compile(r"<[^>]+>")


def _plain(text: Any) -> Any:
    """Flatten any HTML a source snuck in down to text.

    Two of the ryflix notes carry an <a> tag. Every string here is rendered with
    textContent — third-party text must never be parsed as markup — so a tag
    left in place shows up on the page as literal angle brackets. A link keeps
    both its label and its URL rather than losing one of them.
    """
    if not isinstance(text, str):
        return text
    text = _ANCHOR_TAG.sub(lambda m: m.group(2).strip() + " (" + m.group(1) + ")", text)
    return re.sub(r"\s+", " ", _ANY_TAG.sub("", text)).strip()


def norm(s: str | None) -> str:
    """Match key: case, spacing and punctuation differ between every source.

    The API writes "Activ8", "10 for 10" and "One by One" where the document
    writes "ACTIV8", "10 For 10" and "One By One".
    """
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def key_for(name: str, description: str) -> str:
    return f"{norm(name)}|{norm(description)}"


def parse_document(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    start = next(
        i for i, l in enumerate(lines) if l.startswith("### List and descriptions")
    )
    end = next(i for i, l in enumerate(lines) if l.startswith("# Rooms"))

    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in lines[start:end]:
        entry = _ENTRY.match(line)
        if entry:
            cur = {
                "name": _clean(entry.group(1)),
                "description": _clean(entry.group(2)),
                "fields": {},
            }
            out.append(cur)
            continue
        field = _FIELD.match(line)
        if field and cur is not None:
            name, value = field.group(1).strip(), _clean(field.group(2))
            if not value:
                continue
            if name in _MULTI:
                cur["fields"].setdefault(name, []).append(value)
            else:
                cur["fields"].setdefault(name, value)
    return out


def parse_rooms(raw: Any) -> tuple[tuple[str, ...], str]:
    """(rooms, mode). "Mega Laser or Trench" is a choice; a list is a set.

    The document writes the choice as `Room:` and the set as `Rooms:`, but the
    separator is what decides — "or" means either room will do, while The
    Marathon's "Hide, Mega Grid, and Mega Laser" needs all three.
    """
    if raw is None:
        return ((), "any")
    if isinstance(raw, list):
        rooms = [str(r).strip() for r in raw]
        return (tuple(r for r in rooms if r), "all" if len(rooms) > 1 else "any")
    text = str(raw).strip()
    if not text:
        return ((), "any")
    if " or " in text:
        parts = [p.strip() for p in text.split(" or ")]
        return (tuple(p for p in parts if p), "any")
    parts = [p.strip() for p in re.split(r",| and ", text)]
    parts = [p for p in parts if p]
    return (tuple(parts), "all" if len(parts) > 1 else "any")


_WORD_EDGE = r"(?<![A-Za-z0-9])"
_WORD_END = r"(?![A-Za-z0-9])"


def find_games(text: str, rooms: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """(room, gamemode) pairs the badge's wording names, among `rooms`.

    Matched against master_document.GAMEMODES rather than parsed out of the
    sentence, because the sentences vary — "Complete Bop level 7", "Win level 1
    of Mega Grid", "Easter Egg Statues", a `Game/Level` of "Scramble 1" — while
    the names don't. Longest name first, and a matched span is spent, so The
    Marathon's "Mega Relay" can't also count as Hide's "Relay"; the bare "Relay"
    earlier in the sentence is the one that counts for Hide.

    One name in two of the badge's rooms (Zap, back when Adrenaline Junkie said
    "Mega Laser or Trench") yields a pair for each room.

    Only cooperative gamemodes are listed there — the competitive ones have no
    levels for the site to catalog — so a badge played in one (Snake Island's
    Tails) names nothing and is placed by its room alone.
    """
    rooms = tuple(rooms)
    names = sorted({g for r in rooms for g in GAMEMODES.get(r, {})}, key=len, reverse=True)
    spent: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for name in names:
        pattern = _WORD_EDGE + re.escape(name) + _WORD_END
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if any(m.start() < end and start < m.end() for start, end in spent):
                continue
            spent.append(m.span())
            hits.append((m.start(), name))
            break
    return tuple(
        (room, name)
        for _, name in sorted(hits)
        for room in rooms
        if name in GAMEMODES.get(room, {})
    )


def _place(
    name: str, text: str, rooms: tuple[str, ...], mode: str, *, trusted: bool
) -> tuple[tuple[str, ...], str, tuple[tuple[str, str], ...]]:
    """(rooms, mode, games) for one badge.

    `trusted` is whether the rooms came from the master document. Rooms only
    ryflix gives are checked against what the wording names: if the claimed
    room runs none of it, and the master document places it in exactly one
    room, that room is taken instead. Steady Stream is the case — ryflix says
    Push, the same mistake it makes for Recollection, but Photon Rush is a Laser
    game.
    """
    games = find_games(text, rooms)
    if games or trusted or not rooms:
        return rooms, mode, games
    elsewhere = find_games(text, GAMEMODES)
    homes = tuple(dict.fromkeys(room for room, _ in elsewhere))
    if len(homes) != 1:
        return rooms, mode, games
    print(
        f"note: {name!r}: ryflix says {' / '.join(rooms)}, but "
        f"{', '.join(g for _, g in elsewhere)} is a {homes[0]} gamemode; "
        f"taking {homes[0]}.",
        file=sys.stderr,
    )
    return homes, "any", elsewhere


def _empty_record(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "rooms": (),
        "rooms_mode": "any",
        "games": (),
        "level": None,
        "difficulty": None,
        "difficulty_estimated": False,
        "difficulty_note": None,
        "players": None,
        "overlapping": None,
        "notes": None,
        "tips": (),
        "watch_out": (),
        "fun_facts": (),
        "hint": None,
        "giveaway": None,
    }


def build(doc_text: str, ryflix_html: str | None) -> dict[str, dict[str, Any]]:
    ryflix: dict[str, dict[str, Any]] = {}
    if ryflix_html:
        start = ryflix_html.index("const BADGES = [")
        raw = ryflix_html[start + len("const BADGES = ") : ryflix_html.index("}];", start) + 2]
        for b in json.loads(raw):
            ryflix.setdefault(norm(b.get("name")), b)

    out: dict[str, dict[str, Any]] = {}
    documented: set[str] = set()
    for entry in parse_document(doc_text):
        fields = entry["fields"]
        extra = ryflix.get(norm(entry["name"]), {})
        documented.add(norm(entry["name"]))

        rooms, mode = parse_rooms(fields.get("Room") or fields.get("Rooms"))
        trusted = bool(rooms)
        if not rooms:
            rooms, mode = parse_rooms(extra.get("room"))
        level = fields.get("Game/Level") or fields.get("Level")
        rooms, mode, games = _place(
            entry["name"],
            " ".join((entry["name"], entry["description"], level or "")),
            rooms, mode, trusted=trusted,
        )

        record = _empty_record(entry["name"])
        record.update({
            "rooms": rooms,
            "rooms_mode": mode,
            "games": games,
            "level": level,
            "difficulty": extra.get("difficulty") or None,
            "players": extra.get("players") or None,
            "overlapping": extra.get("overlapping") or None,
            "notes": extra.get("notes") or None,
            "tips": tuple(fields.get("Tip", ())),
            "watch_out": tuple(fields.get("Watch Out", ())),
            "fun_facts": tuple(fields.get("Fun Fact", ())),
            "hint": fields.get("Hint"),
            "giveaway": fields.get("Giveaway"),
        })
        _flatten(record)
        out[key_for(entry["name"], entry["description"])] = record

    # A badge ryflix lists and the document doesn't still deserves what ryflix
    # knows. Steady Stream is one: Activate still serves it, but the October
    # 2026 document moved it out of its badge list and into its notes on
    # retired mechanics, so without this it would lose its grade and its room.
    for name_key, extra in ryflix.items():
        if name_key in documented or not extra.get("name"):
            continue
        rooms, mode = parse_rooms(extra.get("room"))
        rooms, mode, games = _place(
            extra["name"],
            " ".join((extra["name"], extra.get("description") or "")),
            rooms, mode, trusted=False,
        )
        record = _empty_record(extra["name"])
        record.update({
            "rooms": rooms,
            "rooms_mode": mode,
            "games": games,
            "difficulty": extra.get("difficulty") or None,
            "players": extra.get("players") or None,
            "overlapping": extra.get("overlapping") or None,
            "notes": extra.get("notes") or None,
        })
        _flatten(record)
        out[key_for(extra["name"], extra.get("description") or "")] = record

    _apply_estimates(out)
    return out


def _flatten(record: dict[str, Any]) -> None:
    for field in ("level", "difficulty", "players", "overlapping", "notes",
                  "hint", "giveaway"):
        record[field] = _plain(record[field]) or None
    for field in ("tips", "watch_out", "fun_facts"):
        record[field] = tuple(_plain(x) for x in record[field])


def _apply_estimates(records: dict[str, dict[str, Any]]) -> None:
    """Fill difficulty holes, and only holes.

    An estimate that finds a grade already there is a document that has caught
    up, so it is dropped rather than argued with — loudly, because a stale
    estimate sitting in the table is the thing worth noticing.
    """
    by_name = {key.split("|", 1)[0]: key for key in records}

    for name_key, estimate in ESTIMATES.items():
        key = by_name.get(name_key)
        if key is None:
            # In neither source at all (Mascot was, until it was retired).
            # Build the record so the badge still resolves, with nothing
            # claimed but the estimate.
            key = key_for(name_key, estimate.get("description", ""))
            records[key] = _empty_record(estimate.get("name", name_key))

        record = records[key]
        if record["difficulty"]:
            print(
                f"note: {name_key!r} now has a sourced difficulty "
                f"({record['difficulty']!r}); dropping the estimate. Remove it "
                f"from ESTIMATES.",
                file=sys.stderr,
            )
            continue
        record["difficulty"] = estimate["difficulty"]
        record["difficulty_estimated"] = True
        record["difficulty_note"] = estimate["why"]


def _lit(v: Any, indent: str) -> str:
    # Before anything else: json.dumps would write `false`, which is not Python.
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, tuple):
        if not v:
            return "()"
        inner = "".join(f"{indent}    {_inline(x)},\n" for x in v)
        return "(\n" + inner + indent + ")"
    return json.dumps(v, ensure_ascii=False) if v is not None else "None"


def _inline(v: Any) -> str:
    # A nested tuple (a `games` pair) must stay a tuple: json.dumps would write
    # a list, and a list can't go in the set _build_name_index compares with.
    if isinstance(v, tuple):
        return "(" + ", ".join(_inline(x) for x in v) + ("," if len(v) == 1 else "") + ")"
    return json.dumps(v, ensure_ascii=False)


def render(records: dict[str, dict[str, Any]]) -> str:
    lines = [_HEADER, "BADGES: dict[str, dict[str, Any]] = {"]
    for key in sorted(records):
        rec = records[key]
        lines.append(f"    {json.dumps(key, ensure_ascii=False)}: {{")
        for field, value in rec.items():
            lines.append(f"        {json.dumps(field)}: {_lit(value, '        ')},")
        lines.append("    },")
    lines.append("}")
    lines.append(_FOOTER)
    return "\n".join(lines)


_HEADER = '''"""Reference detail for each badge, from the two community sources.

Generated by `python -m app.tools.gen_badge_reference` — edit that, not this.

Activate publishes a badge's name, description and star value through the badge
API and nothing else: no room, no difficulty, no idea how to actually do it. Two
community documents fill that in, and this module is their merge.

**Keyed by name *and* description**, because the name alone is ambiguous:
"Untouchable 5.0" is two badges, and they are in different rooms — Piperooni in
Pipes, Wormholes in Portals. `lookup` falls back to a name-only index built at
import, merged one field at a time so a field two badges disagree about is
dropped rather than answered with the other badge's value. Same rule, and the
same reason, as `master_document.lookup`.

Where the two sources disagree about a room, the master document wins. It was
right about both Untouchable 5.0 rooms where the other source gave Portals for
both, checked against the site's own catalog (`location_games`). Recollection is
the conflict that check can't settle, since Memory runs at no location we track:
the document puts it in Arena, the other in Push, and the document's Arena
agrees with `master_document.GAMEMODES`.

A badge only the other source lists keeps that source's room unless the room
runs none of what the badge names, in which case the gamemode's own room is
taken. That is Steady Stream since the October 2026 document moved it into its
notes on retired mechanics: the other source says Push, the same mistake it
makes for Recollection, and Photon Rush is a Laser game.

`games` is the (room, gamemode) pairs a badge's wording names, matched against
`master_document.GAMEMODES`, so that /badges can tell a room a location has from
a room that still runs the right game: Langley has a Laser room, but it runs
Sneak and Chopper, not Photon Rush. The competitive games are not in that list —
they have no levels, so the site never catalogs them — and a badge played in one
(Snake Island's Tails) names nothing and is placed by its room alone.

`hint` and `giveaway` are the Easter Egg and Riddle answers. The source document
hides them as white-on-white text because each can only be solved once; /badges
shows them with the rest of a badge's detail once it is expanded.

A badge graded by neither document can carry this repo's own estimate:
`difficulty_estimated` says so and `difficulty_note` says why. The page shows one
muted and starred rather than hiding it — a soft answer beats a hole in the
column, but it should not pass for the document's. An estimate is only ever a
gap-filler; it never overwrites a sourced grade. None is in force today.

A badge with no entry at all gets every field empty and renders with no detail.
That is expected, not an error.
"""
from __future__ import annotations

import re
from typing import Any

'''

_FOOTER = '''

_EMPTY: dict[str, Any] = {
    "name": None,
    "rooms": (),
    "rooms_mode": "any",
    # (room, gamemode) pairs the badge names; empty where it names none, and
    # then `rooms` alone says where it is played.
    "games": (),
    "level": None,
    "difficulty": None,
    # True where the grade is this repo's estimate rather than either document's
    # — shown, but visibly softer, the way master_document's disputed player
    # counts are. `difficulty_note` says why.
    "difficulty_estimated": False,
    "difficulty_note": None,
    "players": None,
    "overlapping": None,
    "notes": None,
    "tips": (),
    "watch_out": (),
    "fun_facts": (),
    "hint": None,
    "giveaway": None,
}

# Fields that carry no information when empty, so "both agree" is easy to state.
_FIELDS = tuple(_EMPTY)


def norm(s: str | None) -> str:
    """Match key. Every source cases and punctuates these names differently:
    the API says "Activ8" and "10 for 10" where the document says "ACTIV8" and
    "10 For 10"."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _build_name_index() -> dict[str, dict[str, Any]]:
    """Name-only fallback, with disagreements dropped field by field.

    Only "Untouchable 5.0" collides today, and only on `rooms` — everything else
    about the two is identical, so a caller who knows just the name still gets
    the tips and the difficulty and simply no room.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for key, rec in BADGES.items():
        by_name.setdefault(key.split("|", 1)[0], []).append(rec)

    index: dict[str, dict[str, Any]] = {}
    for name_key, records in by_name.items():
        if len(records) == 1:
            index[name_key] = records[0]
            continue
        merged: dict[str, Any] = {}
        for field in _FIELDS:
            values = {r.get(field) for r in records}
            merged[field] = values.pop() if len(values) == 1 else _EMPTY[field]
        index[name_key] = merged
    return index


_BY_NAME = _build_name_index()


def lookup(name: str | None, description: str | None = None) -> dict[str, Any]:
    """One badge's reference detail, with every field always present.

    Fields the documents don't cover come back None or empty, so a caller can
    hand the result straight to the front end without special-casing.
    """
    exact = BADGES.get(f"{norm(name)}|{norm(description)}")
    if exact is not None:
        return {**_EMPTY, **exact}
    return {**_EMPTY, **_BY_NAME.get(norm(name), {})}
'''


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    doc_text = Path(argv[0]).read_text(encoding="utf-8")
    ryflix_html = Path(argv[1]).read_text(encoding="utf-8") if len(argv) > 1 else None
    sys.stdout.reconfigure(encoding="utf-8")
    print(render(build(doc_text, ryflix_html)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
