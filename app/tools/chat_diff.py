"""Print the messages a newer Discord chat export has that an older one lacks.

    python -m app.tools.chat_diff OLD.txt NEW.txt > new_messages.txt

badge_tips.py is written from the #badges channel, and each fresh export
repeats everything already read. This prints only what is new, so the reading
starts where the last one stopped.

Messages are compared whole (timestamp, author and text) with their reaction
blocks left out, since a reaction added to an old message is not news. An old
message that was edited therefore shows up again, which is wanted.
"""
from __future__ import annotations

import re
import sys

_HEADER = re.compile(r"^\[\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2} [AP]M\] ")
_RULE = re.compile(r"^={20,}$")


def messages(text: str) -> list[str]:
    """Split an export into messages, dropping its banners and reactions."""
    found: list[list[str]] = []
    current: list[str] | None = None
    in_reactions = False
    for line in text.splitlines():
        if _HEADER.match(line):
            current = [line]
            found.append(current)
            in_reactions = False
        elif _RULE.match(line):
            current = None  # the header and footer banners belong to no message
        elif current is not None:
            if line.strip() == "{Reactions}":
                in_reactions = True
            if not in_reactions:
                current.append(line)
    return ["\n".join(m).rstrip() for m in found]


def added(old: str, new: str) -> list[str]:
    seen = set(messages(old))
    return [m for m in messages(new) if m not in seen]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8-sig") as f:
        old = f.read()
    with open(argv[2], encoding="utf-8-sig") as f:
        new = f.read()
    out = added(old, new)
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n\n".join(out))
    print(f"{len(out)} new message(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
