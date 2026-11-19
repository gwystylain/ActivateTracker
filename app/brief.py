"""A short, rude summary of chosen players, written for a Discord bot to post.

`gather` reads what the dashboard already knows — visit rows, earned badges,
records held — into one `PlayerBrief` per player; `render` turns those into
Discord Markdown. The two are apart so the wording can be tested without a
database and the facts can be sent alongside the text, for a bot that would
rather write its own insults.

Everything is dated the way the rest of the app dates it: a session is a
`visits.visit_date` (the play day, `streak.poll_day`), a badge counts on its
`earned_on`, and a record on the day `_record_dates` says it was set. All three
are the same play day for the same night out, which is what lets "last
session" collect points, badges and records together. A badge or record held
since before tracking has no date and so never shows up as recent.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from . import streak as streak_mod

#: No visit in this many days and a player is slacking, whatever their month
#: looked like. A week is long enough not to roast an ordinary gap between
#: weekends, short enough to catch someone drifting off.
SLACK_DAYS = 7

#: Discord refuses a message over 2000 characters.
DISCORD_LIMIT = 2000

@dataclass
class Session:
    """A player's most recent visit day and what it brought in."""

    day: date
    points: int
    badges: list[str] = field(default_factory=list)
    records: list[str] = field(default_factory=list)


@dataclass
class PlayerBrief:
    id: int
    name: str
    days_since: int | None
    last_session: Session | None
    window_points: int
    window_visits: int
    window_badges: int
    window_records: int
    records_held: int
    discount_pct: int

    @property
    def slacking(self) -> bool:
        return (
            self.days_since is None
            or self.days_since >= SLACK_DAYS
            or self.window_points <= 0
        )

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        if self.last_session:
            out["last_session"]["day"] = self.last_session.day.isoformat()
        out["slacking"] = self.slacking
        return out


def gather(
    conn,
    players: list[Any],
    *,
    records: list[dict[str, Any]],
    today: date,
    window_days: int = streak_mod.DISCOUNT_WINDOW_DAYS,
) -> list[PlayerBrief]:
    """One brief per row of `players` (id, handle, display_name, initial_streak,
    initial_streak_set_at), in the order given.

    `records` is `public._build_records`' output: the levels each player holds
    now, each dated to when it was set. Passed in rather than rebuilt here so
    this module doesn't reach into the routes.
    """
    cutoff = today - timedelta(days=window_days)
    held_by: dict[int, list[tuple[str | None, str]]] = defaultdict(list)
    for p in records:
        for row in p["rows"]:
            for lvl in row["levels"]:
                label = f'{row["room"]} {row["game"]} L{lvl["n"]} @ {row["location"]}'
                held_by[p["id"]].append((lvl["date"], label))

    out: list[PlayerBrief] = []
    for p in players:
        pid = p["id"]
        visits = conn.execute(
            "SELECT visit_date, score_delta FROM visits WHERE player_id = ?", (pid,)
        ).fetchall()
        points_on: dict[date, int] = defaultdict(int)
        for v in visits:
            points_on[date.fromisoformat(v["visit_date"])] += v["score_delta"]

        baseline = (
            date.fromisoformat(p["initial_streak_set_at"])
            if p["initial_streak_set_at"]
            else None
        )
        summary = streak_mod.summarize(
            list(points_on),
            initial_streak=p["initial_streak"] or 0,
            initial_streak_set_at=baseline,
            today=today,
        )

        badges = [
            (date.fromisoformat(r["earned_on"]), r["name"])
            for r in conn.execute(
                """
                SELECT b.name AS name, pb.earned_on AS earned_on
                FROM player_badges pb JOIN badges b ON b.badge_id = pb.badge_id
                WHERE pb.player_id = ? AND pb.earned = 1 AND pb.earned_on IS NOT NULL
                ORDER BY b.name
                """,
                (pid,),
            ).fetchall()
        ]
        held = [
            (date.fromisoformat(d) if d else None, label) for d, label in held_by[pid]
        ]

        session = None
        if summary.last_visit_date is not None:
            day = summary.last_visit_date
            session = Session(
                day=day,
                points=points_on[day],
                badges=[name for d, name in badges if d == day],
                records=[label for d, label in held if d == day],
            )

        out.append(
            PlayerBrief(
                id=pid,
                name=p["display_name"] or p["handle"],
                days_since=summary.days_since_last_visit,
                last_session=session,
                window_points=sum(n for d, n in points_on.items() if cutoff <= d <= today),
                window_visits=sum(1 for d in points_on if cutoff <= d <= today),
                window_badges=sum(1 for d, _ in badges if cutoff <= d <= today),
                window_records=sum(1 for d, _ in held if d and cutoff <= d <= today),
                records_held=len(held),
                discount_pct=summary.discount_pct,
            )
        )
    return out


# ---------- wording ----------
#
# The post reads like a group-chat roast, not a report: a heading, one line
# that singles out last place, then a short line per player led by a rank
# emoji and carrying one number — whichever brags or hurts most — and a taunt
# picked from the facts. Shared nights out and a couple of awards close it.
# Every situation has a pool of templates so back-to-back posts differ, and
# every choice goes through `rng`, so a seeded one gives the same text twice.

MEDALS = ("🥇", "🥈", "🥉")
MIDDLE_EMOJI = "😐"
LAST_EMOJI = "🐢"
SLACKER_EMOJI = "💤"
WORST_SLACKER_EMOJI = "🪦"
GHOST_EMOJI = "👻"

#: A gap this small a fraction of the score ahead is a near-tie, and gets
#: called out as one: 718 points out of 800k is a rounding error.
NEAR_TIE = 0.02

#: Badges in one night that earn a solo player an outing line of their own.
BIG_NIGHT = 5

#: Award lines shown at most.
MAX_AWARDS = 2

HEADING = "# 🏆 The Activate Roast"

_LEADER = (
    "Has a key to the building at this point.",
    "Should honestly be paying rent.",
    "Lives there now. Someone water their plants.",
    "{visits} in {window} days. The staff know their order.",
    "Is the goal the high score, or avoiding home?",
)
_LEADER_BARELY = (
    "Won by a rounding error. Hold the parade.",
    "Leads by {gap}. One bad round from nothing.",
    "Technically first. Technically.",
)
_LONE = (
    "First out of one. Proud of you, champ.",
    "Undefeated, mostly by default.",
)
_NEAR_TIE = (
    "Did everything **{ahead}** did and still lost.",
    "So close it's embarrassing. Do better.",
    "One more round of anything and this was theirs. They didn't play it.",
    "Photo finish, and they're not in the photo.",
)
_MIDDLE = (
    "Showed up, scored, went home. Fine.",
    "Aggressively average.",
    "Neither the hero nor the joke. Yet.",
    "Solid. Forgettable, but solid.",
    "Present. That's the nicest thing we can say.",
    "Mid-table, mid-effort, mid everything.",
    "Not last. Hold onto that.",
    "The very definition of \"also played\".",
)
_MIDDLE_EFFICIENT = (
    "Fewer visits than **{leader}**, more per visit. Grudging respect.",
    "Best points per visit going. Imagine if they actually showed up.",
)
_LAST = (
    "{visits} and still got lapped.",
    "Bringing up the rear with what's left of their dignity.",
    "Last place. Is this a cry for help?",
    "The leaderboard has a bottom, and here it is.",
)
_LAST_EFFICIENT = (
    "Best points per visit here and still last. Try showing up.",
    "Scores like a pro, attends like a cat. Last.",
)
_SLACK = (
    "Last seen {last_day}. The lasers have forgotten their name.",
    "Retired without telling anyone.",
    "Every day away, everyone else pulls further ahead.",
    "Has the floor started to miss them? No.",
)
_DISCOUNT_GONE = (
    "Discount: gone.",
    "Discount: dead and buried.",
    "Pays full price now, like a tourist.",
)
_DISCOUNT_FADING = (
    "Discount bleeding out at {pct}%.",
    "Down to {pct}% off and falling.",
)
_GHOST = (
    "No visit on record. Ever. Are they even real?",
    "Has never scored a point. Not one.",
    "Exists only as a name on this list.",
)
_HEAD_LAST = (
    "🎯 Today's target: **{last}**.",
    "🙏 Thoughts and prayers to **{last}**.",
    "📉 **{last}**, this one's for you.",
    "🫵 Looking at you, **{last}**.",
)
_HEAD_MISSING = (
    "🚨 Missing: **{last}**, last seen {last_day}.",
    "🔦 Has anyone checked on **{last}**? It's been {days} days.",
    "🪦 Gone but not forgotten: **{last}**.",
)
_HEAD_GHOST = (
    "👻 **{last}** has never set foot in the place.",
    "👻 Still waiting on **{last}** to play literally once.",
)
_HEAD_ALL_SLACK = (
    "😴 Nobody here has played in a week, and **{last}** is the worst of it.",
    "😴 The whole crew's gone soft. **{last}** most of all.",
)
_OUTING_GROUP = (
    "🎉 {day}: {names} cleared {badges} in one night.",
    "🎉 {day}: {names} went badge hunting together and came home with {badges}.",
    "🤝 {day}: {names} farmed {badges} as a team.",
)
_OUTING_SOLO = (
    "🎉 {day}: {names} cleared {badges} in one night, alone.",
    "🎉 {day}: {names} went full completionist: {badges} in one night.",
)


class _Picker:
    """`rng.choice`, but avoiding templates already used in this post."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.used: set[str] = set()

    def __call__(self, pool: tuple[str, ...], **facts: Any) -> str:
        fresh = [t for t in pool if t not in self.used] or list(pool)
        template = self.rng.choice(fresh)
        self.used.add(template)
        return template.format(**facts)


def _names(names: list[str]) -> str:
    bold = [f"**{n}**" for n in names]
    if len(bold) <= 1:
        return "".join(bold)
    return ", ".join(bold[:-1]) + " and " + bold[-1]


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def short(n: int) -> str:
    """800k, 1.2M, 4.5k, 718 — a number to read at a glance."""
    sign, n = ("-" if n < 0 else ""), abs(int(n))
    if n < 1000:
        return f"{sign}{n:,}"
    if n < 10_000:
        return sign + f"{n / 1000:.1f}".removesuffix(".0") + "k"
    if round(n / 1000) < 1000:
        return f"{sign}{round(n / 1000)}k"
    return sign + f"{n / 1_000_000:.1f}".removesuffix(".0") + "M"


def _per_visit(b: PlayerBrief) -> float:
    return b.window_points / b.window_visits if b.window_visits else 0.0


@dataclass
class _Line:
    emoji: str
    name: str
    number: str
    taunt: str
    keep_taunt: bool  # leader and last place keep theirs longest when trimming

    def text(self, with_taunt: bool) -> str:
        out = f"{self.emoji} **{self.name}** · {self.number}."
        return f"{out} {self.taunt}" if with_taunt and self.taunt else out


def _active_lines(
    active: list[PlayerBrief], last_place: PlayerBrief | None, alone: bool,
    window_days: int, pick: _Picker,
) -> list[_Line]:
    leader = active[0] if active else None
    lines = []
    for i, b in enumerate(active):
        ahead = active[i - 1] if i else None
        gap = ahead.window_points - b.window_points if ahead else 0
        near = ahead is not None and gap <= NEAR_TIE * max(ahead.window_points, 1)
        efficient = (
            leader is not None and b is not leader
            and b.window_visits < leader.window_visits
            and _per_visit(b) > _per_visit(leader)
        )
        facts = dict(
            visits=_count(b.window_visits, "visit"), window=window_days,
            ahead=ahead.name if ahead else "", leader=leader.name if leader else "",
            gap=short(gap),
        )

        if b is last_place:
            emoji = LAST_EMOJI
        elif i < len(MEDALS):
            emoji = MEDALS[i]
        else:
            emoji = MIDDLE_EMOJI

        if i == 0:
            number = short(b.window_points)
            nxt = active[1] if len(active) > 1 else None
            barely = nxt is not None and (
                b.window_points - nxt.window_points <= NEAR_TIE * max(b.window_points, 1)
            )
            facts["gap"] = short(b.window_points - nxt.window_points) if nxt else ""
            pool = _LONE if alone else _LEADER_BARELY if barely else _LEADER
        elif near:
            number = f"tied with **{ahead.name}**" if gap == 0 else f"{short(gap)} points behind"
            pool = _NEAR_TIE
        else:
            number = short(b.window_points)
            if b is last_place:
                pool = _LAST_EFFICIENT if efficient else _LAST
            else:
                pool = _MIDDLE_EFFICIENT if efficient else _MIDDLE
        lines.append(
            _Line(emoji, b.name, number, pick(pool, **facts),
                  keep_taunt=i == 0 or b is last_place)
        )
    return lines


def _slacker_lines(
    slackers: list[PlayerBrief], last_place: PlayerBrief | None, window_days: int,
    pick: _Picker,
) -> list[_Line]:
    lines = []
    for b in slackers:
        if b.days_since is None:
            lines.append(_Line(GHOST_EMOJI, b.name, "never seen", pick(_GHOST), b is last_place))
            continue
        emoji = WORST_SLACKER_EMOJI if b is last_place else SLACKER_EMOJI
        number = (
            f"{b.days_since} days gone"
            if b.days_since >= SLACK_DAYS
            else f"no points in {window_days} days"
        )
        last_day = _day(b.last_session.day) if b.last_session else "who knows when"
        taunt = pick(_SLACK, last_day=last_day) + " " + (
            pick(_DISCOUNT_GONE) if b.discount_pct == 0
            else pick(_DISCOUNT_FADING, pct=b.discount_pct)
        )
        lines.append(_Line(emoji, b.name, number, taunt, b is last_place))
    return lines


def _headline(
    briefs: list[PlayerBrief], active: list[PlayerBrief],
    last_place: PlayerBrief | None, pick: _Picker,
) -> str | None:
    if last_place is None:
        return None
    facts = dict(
        last=last_place.name,
        days=last_place.days_since,
        last_day=_day(last_place.last_session.day) if last_place.last_session else "never",
    )
    if last_place.days_since is None:
        return pick(_HEAD_GHOST, **facts)
    if not active and len(briefs) > 1:
        return pick(_HEAD_ALL_SLACK, **facts)
    if last_place.slacking:
        return pick(_HEAD_MISSING, **facts)
    return pick(_HEAD_LAST, **facts)


def _outings(
    ranked: list[PlayerBrief], window_days: int, pick: _Picker
) -> list[str]:
    """One line per night out: players whose last session fell on the same day
    with the same badges went together, so they're named once, together."""
    groups: dict[tuple[date, tuple[str, ...]], list[PlayerBrief]] = defaultdict(list)
    for b in ranked:
        s = b.last_session
        if s and s.badges and b.days_since is not None and b.days_since <= window_days:
            groups[(s.day, tuple(sorted(s.badges)))].append(b)

    out = []
    for (day, badges), who in sorted(groups.items(), key=lambda kv: kv[0][0], reverse=True):
        if len(who) < 2 and len(badges) < BIG_NIGHT:
            continue
        pool = _OUTING_GROUP if len(who) > 1 else _OUTING_SOLO
        out.append(pick(pool, day=_day(day), names=_names([b.name for b in who]),
                        badges=_count(len(badges), "badge")))
    return out


def _awards(briefs: list[PlayerBrief]) -> list[str]:
    """Up to MAX_AWARDS closing lines, preferring ones that go to somebody new."""
    if len(briefs) < 2:
        return []
    candidates: list[tuple[str, frozenset[str]]] = []

    gone = [b.name for b in briefs if b.discount_pct == 0]
    if gone and len(gone) < len(briefs):
        candidates.append((f"💸 Discount gone: {_names(gone)}", frozenset(gone)))

    for emoji, label, attr in (
        ("🔥", "Most visits", "window_visits"),
        ("🏅", "Most badges", "window_badges"),
        ("🎯", "Most records", "window_records"),
    ):
        best = max(getattr(b, attr) for b in briefs)
        winners = [b.name for b in briefs if getattr(b, attr) == best]
        if best > 0 and len(winners) < len(briefs):
            candidates.append(
                (f"{emoji} {label}: {_names(winners)} ({best})", frozenset(winners))
            )

    chosen: list[str] = []
    seen: set[str] = set()
    for line, who in candidates:  # first pass: spread the awards around
        if len(chosen) < MAX_AWARDS and not who & seen:
            chosen.append(line)
            seen |= who
    for line, _ in candidates:  # then fill any slot left
        if len(chosen) < MAX_AWARDS and line not in chosen:
            chosen.append(line)
    return [line for line, _ in candidates if line in chosen]


def render(
    briefs: list[PlayerBrief],
    *,
    window_days: int = streak_mod.DISCOUNT_WINDOW_DAYS,
    today: date | None = None,
    rng: random.Random | None = None,
) -> str:
    """Discord Markdown: heading, a headline aimed at last place, a line per
    player (active ones by points, then slackers, the longest gone last), the
    shared nights out and a couple of awards — trimmed to fit one message."""
    pick = _Picker(rng or random.Random())
    active = sorted(
        (b for b in briefs if not b.slacking), key=lambda b: (-b.window_points, b.name)
    )
    slackers = sorted(
        (b for b in briefs if b.slacking),
        key=lambda b: (b.days_since if b.days_since is not None else 10**9, b.name),
    )
    if slackers:
        last_place = slackers[-1]
    elif len(active) > 1:
        last_place = active[-1]
    else:
        last_place = None

    headline = _headline(briefs, active, last_place, pick)
    lines = _active_lines(active, last_place, len(briefs) == 1, window_days, pick)
    lines += _slacker_lines(slackers, last_place, window_days, pick)
    outings = _outings(active + slackers, window_days, pick)
    awards = _awards(briefs)

    sub = f"-# last {window_days} days" + (f" · as of {_day(today)}" if today else "")

    def assemble(level: int) -> str:
        # 0 everything · 1 no awards · 2 only leader's and last place's taunts
        # · 3 no taunts · 4 no outings either
        sections = [[HEADING, sub]]
        if headline:
            sections.append([headline])
        sections.append([
            ln.text(level < 2 or (level == 2 and ln.keep_taunt)) for ln in lines
        ])
        if outings and level < 4:
            sections.append(outings)
        if awards and level < 1:
            sections.append(awards)
        return "\n\n".join("\n".join(s) for s in sections if s)

    for level in range(5):
        text = assemble(level)
        if len(text) <= DISCORD_LIMIT:
            return text

    # Still too long: drop whole player lines from the middle, never mid-line,
    # keeping the top and the bottom — which is where the jokes are.
    rows = text.split("\n")
    while len("\n".join(rows)) > DISCORD_LIMIT - 20 and len(rows) > 4:
        del rows[len(rows) // 2]
    text = "\n".join(rows)
    if len(text) > DISCORD_LIMIT:
        text = text[: DISCORD_LIMIT - 1].rstrip() + "…"
    return text
