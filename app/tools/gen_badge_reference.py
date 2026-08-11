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
module docstring for why, and for the two conflicts that resolves.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_ENTRY = re.compile(r"^\* \*\*(.+?):\*\*\s*(.*?)\s*$")
_FIELD = re.compile(r"^\s+\* ##\s*([A-Za-z/ ]+?):\s*(.*?)\s*$")
_MULTI = {"Tip", "Watch Out", "Fun Fact"}
# Google Docs' markdown export backslash-escapes punctuation.
_ESCAPED = re.compile(r"\\([!#()\-\[\]*_.])")


def _clean(s: str) -> str:
    return _ESCAPED.sub(r"\1", s).strip()


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


def build(doc_text: str, ryflix_html: str | None) -> dict[str, dict[str, Any]]:
    ryflix: dict[str, dict[str, Any]] = {}
    if ryflix_html:
        start = ryflix_html.index("const BADGES = [")
        raw = ryflix_html[start + len("const BADGES = ") : ryflix_html.index("}];", start) + 2]
        for b in json.loads(raw):
            ryflix.setdefault(norm(b.get("name")), b)

    out: dict[str, dict[str, Any]] = {}
    for entry in parse_document(doc_text):
        fields = entry["fields"]
        extra = ryflix.get(norm(entry["name"]), {})

        rooms, mode = parse_rooms(fields.get("Room") or fields.get("Rooms"))
        if not rooms:
            rooms, mode = parse_rooms(extra.get("room"))

        record: dict[str, Any] = {
            "name": entry["name"],
            "rooms": rooms,
            "rooms_mode": mode,
            "level": fields.get("Game/Level") or fields.get("Level"),
            "difficulty": extra.get("difficulty") or None,
            "players": extra.get("players") or None,
            "overlapping": extra.get("overlapping") or None,
            "notes": extra.get("notes") or None,
            "tips": tuple(fields.get("Tip", ())),
            "watch_out": tuple(fields.get("Watch Out", ())),
            "fun_facts": tuple(fields.get("Fun Fact", ())),
            "hint": fields.get("Hint"),
            "giveaway": fields.get("Giveaway"),
        }
        out[key_for(entry["name"], entry["description"])] = record
    return out


def _lit(v: Any, indent: str) -> str:
    if isinstance(v, tuple):
        if not v:
            return "()"
        inner = "".join(f"{indent}    {json.dumps(x, ensure_ascii=False)},\n" for x in v)
        return "(\n" + inner + indent + ")"
    return json.dumps(v, ensure_ascii=False) if v is not None else "None"


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
both, checked against the site's own catalog (`location_games`), and it is more
complete about rooms that run the same game — "Mega Laser or Trench" where the
other names only Mega Laser. Two conflicts are unresolved by that check because
neither game runs at a location we track: the document puts Steady Stream's
Photon Rush in Laser and Recollection's Memory in Arena, the other puts both in
Push. The document's Arena/Memory agrees with `master_document.GAMEMODES`, so
the document is taken on both.

`hint` and `giveaway` are the Easter Egg and Riddle answers. The source document
hides them as white-on-white text because each can only be solved once; /badges
shows them with the rest of a badge's detail once it is expanded.

A badge with no entry here — `Mascot`, which the document hasn't caught up with —
gets every field empty and renders with no detail. That is expected, not an error.
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
    "level": None,
    "difficulty": None,
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
