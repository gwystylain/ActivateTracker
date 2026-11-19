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

#: Names listed per item before "+N more".
LIST_MAX = 3


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

def _names(briefs: list[PlayerBrief]) -> str:
    bold = [f"**{b.name}**" for b in briefs]
    if len(bold) <= 1:
        return "".join(bold)
    return ", ".join(bold[:-1]) + " and " + bold[-1]


def _v(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _list(items: list[str]) -> str:
    shown = ", ".join(items[:LIST_MAX])
    return shown + (f", +{len(items) - LIST_MAX} more" if len(items) > LIST_MAX else "")


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _ago(days: int) -> str:
    return {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")


_HEADLINES = (
    lambda lead, lag, nl, ng: f"🚀 {lead} {_v(nl, 'is', 'are')} rocketing ahead of {lag}.",
    lambda lead, lag, nl, ng: f"📈 {lead} {_v(nl, 'is', 'are')} leaving {lag} in the dust.",
    lambda lead, lag, nl, ng: (
        f"🏃 {lead} {_v(nl, 'keeps', 'keep')} showing up. {lag} {_v(ng, 'does', 'do')} not."
    ),
    lambda lead, lag, nl, ng: f"🏆 {lead} {_v(nl, 'is', 'are')} lapping {lag}. It isn't close.",
)

_GHOSTED = (
    "hasn't set foot in Activate for {n} days",
    "has been missing for {n} days",
    "is on day {n} of an unannounced retirement",
    "has gone {n} days without scoring a single point",
    "was last seen {n} days ago and the lasers have stopped asking",
)


def _leader_line(b: PlayerBrief, window_days: int) -> str:
    s = b.last_session
    assert s is not None  # a leader has window points, so a visit
    got = [f"**+{s.points:,}** points"]
    if s.badges:
        got.append(f"{_count(len(s.badges), 'badge')} ({_list(s.badges)})")
    if s.records:
        got.append(f"{_count(len(s.records), 'record')} ({_list(s.records)})")
    month = [_count(b.window_visits, "visit"), f"+{b.window_points:,}"]
    if b.window_badges:
        month.append(_count(b.window_badges, "badge"))
    if b.window_records:
        month.append(_count(b.window_records, "record"))
    return (
        f"**{b.name}** — last session {_day(s.day)} ({_ago(b.days_since)}): "
        f"{', '.join(got)}. Last {window_days} days: {', '.join(month)} · "
        f"{b.discount_pct}% off."
    )


def _slacker_line(b: PlayerBrief, top: PlayerBrief | None, rng: random.Random) -> str:
    if b.days_since is None:
        line = f"**{b.name}** has no visit on record. Ever. Are they even real?"
    else:
        line = f"**{b.name}** " + rng.choice(_GHOSTED).format(n=b.days_since)
        if b.last_session:
            line += f" (last session {_day(b.last_session.day)})"
        line += "."
        line += (
            " Discount: gone."
            if b.discount_pct == 0
            else f" Discount clinging on at {b.discount_pct}%."
        )
    if top is not None:
        line += f" Meanwhile **{top.name}** banked {top.window_points:,} points."
    return line


def render(
    briefs: list[PlayerBrief],
    *,
    window_days: int = streak_mod.DISCOUNT_WINDOW_DAYS,
    rng: random.Random | None = None,
) -> str:
    """Discord Markdown: a headline when there's somebody to compare, then a
    line per player — leaders by points, then the slackers, longest gone first.
    """
    rng = rng or random.Random()
    leaders = sorted(
        (b for b in briefs if not b.slacking), key=lambda b: -b.window_points
    )
    slackers = sorted(
        (b for b in briefs if b.slacking),
        key=lambda b: -(b.days_since if b.days_since is not None else 10**6),
    )
    top = leaders[0] if leaders else None

    lines: list[str] = []
    if leaders and slackers:
        lines.append(
            rng.choice(_HEADLINES)(
                _names(leaders), _names(slackers), len(leaders), len(slackers)
            )
        )
    elif len(leaders) > 1:
        gap = leaders[0].window_points - leaders[-1].window_points
        lines.append(
            f"🏁 Everyone showed up. **{leaders[0].name}** leads the last "
            f"{window_days} days; **{leaders[-1].name}** trails by {gap:,} points."
        )
    elif slackers and len(slackers) > 1:
        lines.append(f"🪦 Nobody here has played in {SLACK_DAYS} days. Disgraceful.")

    if lines:
        lines.append("")
    lines += [_leader_line(b, window_days) for b in leaders]
    lines += [_slacker_line(b, top, rng) for b in slackers]

    text = "\n".join(lines)
    if len(text) > DISCORD_LIMIT:
        text = text[: DISCORD_LIMIT - 1].rstrip() + "…"
    return text
