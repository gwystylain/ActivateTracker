"""Badge tips from the players' Discord, layered over badge_reference.

badge_reference is generated from two community documents and is rebuilt
whenever they change. This module is the third source, and it is ours: the
useful core of the Activate Discord's #badges channel (8,540 messages, April
2024 to October 2026), cut down to methods somebody confirmed by earning the
badge, rulings from Activate staff, and figures people measured. Where advice
changed over time only the newest survives. It is hand-written and committed,
the way gen_badge_reference's ESTIMATES are, and it is applied at lookup time by
`overlay` rather than baked into the generated module, so regenerating
badge_reference never loses it and editing it never needs the generator's
inputs.

**It adds, and corrects only by replacing.** A tuple field (`tips`,
`watch_out`) is appended after the document's own entries, so nothing the
documents say is dropped. A scalar field (`notes`, `giveaway`) replaces the
document's value outright — used only where the chat shows the document is now
wrong: Link level 1 stopped costing lives in October 2026, which broke Thumb
War's recorded method, and Riddle 4.0's recorded answer has its steps in the
wrong order.

Keyed by badge name alone. The one name two badges share, Untouchable 5.0, has
no entry here; anything said about it would hold for both.
"""
from __future__ import annotations

from typing import Any

from .badge_reference import norm

# Said of every badge judged over a whole visit. The rule in force is not the
# one Activate states (staff, March 2025: finish plus 15 minutes) but what
# players keep observing, so it is written as advice that works either way.
_VISIT = (
    "Everything you play in a day pools into your first visit, so go for this "
    "in your first session. Play your \"one last game\", wait about 10 minutes, "
    "and check the badge shows on the iPad before starting another session."
)

_STREAK = (
    "You can leave the room between games, but a loss or any other game in "
    "between breaks the streak. Start with your shakiest game, so a failure "
    "costs the least."
)

_X_FOR_X = (
    "The games must differ, not the rooms. Changing group size mid-streak is "
    "fine; extending your time mid-streak has broken streaks."
)

_DEJA_VU = (
    "Stack it on an X for X streak: finish the streak on Zones, then win the "
    "same Zones level again."
)

_WINS = (
    "Wins are cumulative across all three badges. Fastest: solo Tails or Bop "
    "Clock (staff must set an x3 override), or Grid's competitive Zones and "
    "Shot Clock at about 20 seconds each. Canyon Cross counts only when it says "
    "\"the winner is red\"."
)

TIPS: dict[str, dict[str, Any]] = {
    # ---------- visit badges ----------
    "Best of Both Worlds": {
        "tips": (
            "It counts games played, not won. Beta games don't count. Solo Bop "
            "Clock (competitive) and Grid 1 (co-op) are quick games to even the "
            "count.",
        ),
        "watch_out": (_VISIT,),
    },
    "Scatterbrained": {
        "tips": (
            "Never press Next Level or Replay. Any different game counts, even "
            "in the same room, and losing is fine.",
        ),
        "watch_out": (_VISIT,),
    },
    "Out For Blood": {
        "tips": ("Play every competitive game in the same visit to pick up Completionist too.",),
        "watch_out": (_VISIT,),
    },
    "OmnipoTENce": {
        "tips": ("It often comes free when you grind one level 10 for a whole session.",),
        "watch_out": (_VISIT,),
    },
    "Keener": {
        "tips": (
            "It is every game you start, whoever else is playing. If someone "
            "scans in before you, have them sign out to reset the screen, then "
            "scan first.",
        ),
        "watch_out": (_VISIT,),
    },
    "Win/Loss": {"watch_out": (_VISIT,)},

    # ---------- any room ----------
    "ACTIV8": {
        "notes": "Recommended games: Tile Relay, Spot, Technique, Sequence",
        "tips": (
            "Finish on a time bonus of 638 with no lives lost, 688 with one "
            "lost, 738 with two. The counter lags, so press 1-2 points early.",
            "Tile Relay 8 with 2 players is called the easiest: the player who "
            "starts mid-wall goes first, about 8890 comes easily, and you just "
            "delay the final step.",
            "Spot 8: find every screen early, stand ready on the last one and "
            "tap at 638-641.",
            "Technique 8: start in the corners; 3 players is easiest. The board "
            "shows waves 1-2 combined, so the last wave must bank 888 minus that "
            "minus the lives bonus.",
            "Avoid Zones (unpredictable end splash) and Trivial (no running "
            "total). The old Spellinator method died when Spelinator replaced "
            "it in late 2025.",
        ),
    },
    "Thumb War": {
        "notes": (
            "You must lose at least one life, since 1000 + 250 for five lives "
            "already passes 1234. In Gems 1, find the buttons, hit 4 wrong ones "
            "and finish on a time bonus of 184. Link and Match level 1 no longer "
            "cost lives, so the old Link method overshoots."
        ),
        "tips": ("Rings 1, Undercover and Spellinator also work.",),
    },
    "The Gauntlet": {
        "tips": (
            "Done in Zones, Mega Zones, Simon Says, Odd One Out, Scramble, Spot, "
            "Inside Out, Order Up and Collect.",
        ),
    },
    "The Gauntlet Plus": {
        "tips": (
            "Rooms may repeat; only games can't. Put games you can't lose a life "
            "in (Barrage, Mines, Terminal, Zap, Defuse) at the hard end.",
            "A proven 2-player run: Asteroids 1, Defuse 2, Zap 3, Relay 4, Maze "
            "5, Mines 6, Gauntlet 7, Barrage 8, Zones 9, Mega Zones 10.",
            "Zones loses lives to orange and Mega Zones to purple, so some groups "
            "move them lower. Collect 10 is close to free where Control exists.",
        ),
    },
    "The Grand Tour": {
        "tips": (
            "Any level counts, and level 2s are quicker than 1s (no tutorial). "
            "One of each room type is enough. Safe picks: Terminal, Barrage, "
            "Mines, Zap, Labyrinth 1, Link 1, Hide Relay, Zones.",
        ),
        "watch_out": (
            "It counts rooms the system lists even if you can't play them yet "
            "(a room being swapped or \"coming soon\"). If it shows more rooms "
            "than you have, open a ticket.",
        ),
    },
    "Completionist": {
        "tips": (
            "Beta games don't count. If you're stuck one short, ask staff which "
            "game is hidden: a hidden game still counts against you, a disabled "
            "one doesn't.",
        ),
    },
    "Activated": {
        "watch_out": (
            "In practice, levels you beat at another location count if this "
            "location has them too, though staff say it should be one location. "
            "Leave one level to beat here so the check runs here.",
        ),
    },
    "Halfway Mark": {
        "watch_out": (
            "In practice, levels you beat at another location count if this "
            "location has them too. It is awarded mid-session.",
        ),
    },
    "Social Butterfly": {
        "tips": (
            "All 25 must be at one location; you count as one of them, and "
            "losses count. One game with a stranger can finish it.",
        ),
    },
    "Chasing Rainbows": {
        "tips": ("It needs five different rank colours, not just five different ranks.",),
    },
    "Monthly High Scorer": {
        "tips": (
            "On the month's last day, find a level nobody has a monthly high "
            "score on yet and beat it. Everyone in the game gets the badge.",
        ),
    },
    "Early Bird": {
        "watch_out": (
            "Only a co-op win counts (corporate staff). A location that opens "
            "at 11 AM can't award it.",
        ),
    },
    "Night Owl": {
        "tips": ("Book the last slot and win one of your final games after 11 PM.",),
        "watch_out": ("Only a co-op win counts (corporate staff).",),
    },
    "Starting a Rivalry": {"tips": (_WINS,)},
    "Heating Up": {"tips": (_WINS,)},
    "The Ultimate Winner": {
        "tips": (_WINS, "Two players can tie competitive Numbers so both get a win."),
    },
    "My Own Competition": {
        "tips": ("Only improving a score you already have counts; first clears don't.",),
    },
    "Practice Makes Perfect": {
        "tips": ("Only improving a score you already have counts; first clears don't.",),
    },
    "10 For 10": {
        "tips": (
            _STREAK,
            _X_FOR_X,
            "Gimmes: Zones, Mega Zones, Statues, Collect, and Gridlock or "
            "Strategy once memorised. Jigsaw 10 is easier than 8 or 9.",
            "Simon Says 10: one player memorises the first half and shoots, "
            "another the second half.",
        ),
    },
    "Deja Vu Master": {"tips": (_DEJA_VU, "Collect 10 is friendly for this.")},

    # ---------- Hoops ----------
    "Equal Partners": {
        "tips": (
            "Baskets in an unlit hoop don't affect it (staff), so get Showing "
            "Off in the same game: with 2 players that is 60 + 60 + 10 shots.",
            "A solo Barrage 8 currently awards it automatically.",
        ),
    },
    "Showing Off": {
        "tips": ("Get Equal Partners in the same game: unlit-hoop baskets don't affect it.",),
    },
    "Direction Inspection 1.0": {
        "tips": (
            "Each wave's leftmost basket starts green, so pre-shoot it. A miss "
            "costs four cycles of waiting, and the last wave allows almost none.",
        ),
        "watch_out": ("Lose a life and you redo that basket before moving on.",),
    },
    "15S in 15S": {"tips": ("Two players can do it; three make it easy.",)},

    # ---------- Hide ----------
    "2 Fast 2 See": {
        "tips": (
            "Take corners rather than walls, and start within reach of two "
            "buttons. Players with no number coming up should find their next "
            "one early.",
            "The eye flashing is fine; only the eye going red fails it.",
        ),
        "watch_out": ("Staff think the eye first fires at about 8 seconds rather than 9.",),
    },
    "Total Recall 2.0": {
        "tips": (
            "Look at each number once, then press it once. With 5 players split "
            "the numbers 4/4/4/3/3; you can skip memorising your last one.",
        ),
    },
    "One and Done 2.0": {
        "tips": (
            "Spelinator deals fresh letters each wave, so spend one vowel per "
            "word in waves 1-2, especially U, O and Y. FJORD is a good second "
            "word if offered.",
        ),
        "watch_out": ("Letters typed in a rejected word count as used (support ruling).",),
    },
    "Untouchable Master": {
        "watch_out": ("It is Hide's Relay 10, not Mega Relay or Laser Relay.",),
    },

    # ---------- Grid ----------
    "Tiptoeing": {
        "tips": (
            "After each wave stand still on the target tile; the next wave never "
            "spawns a wrong colour there. Stand in the middle of the 2x2, since "
            "heels trip neighbouring tiles.",
        ),
        "watch_out": (
            "A stuck or ghost tile is the usual reason a clean run fails. Ask "
            "staff to recalibrate the room, then try again.",
        ),
    },
    "No Stone Unturned": {
        "tips": (
            "Unlit tiles count too. Photograph the layout, give each player a "
            "section, and cover it in waves 1-2.",
        ),
        "watch_out": ("If a clean run fails, suspect a dead tile.",),
    },

    # ---------- Mega Grid ----------
    "Safety Second": {
        "tips": (
            "As a duo, start in opposite corners away from where the red starts "
            "and run against it in a 1-2-skip rhythm. Aim to land on the red's "
            "tail; small jogging hops beat big jumps.",
        ),
    },
    "Hands Then Feet": {
        "tips": ("Soloable: plan where to start for wave 3. Wave 4 lights all 20 buttons.",),
    },
    "Size Matters": {
        "tips": (
            "Order is by size across all colours. Each player takes a colour, "
            "finds its big piece first (the small one is the remainder) and "
            "calls the sizes, such as \"3, 4, 7\"; one caller then runs \"3 go, "
            "4 go\". Correct pieces usually appear twice, mirrored.",
        ),
    },

    # ---------- Mega Laser and Laser ----------
    "One By One": {
        "tips": (
            "Going left to right just keeps everyone on the same target. "
            "Hitting a red mid-target is fine, and extra guns are allowed.",
        ),
        "watch_out": ("Letting a wave detonate while a target is part-filled voids the run.",),
    },
    "Steady Stream": {"watch_out": ("Only older Laser rooms run Photon Rush.",)},

    # ---------- Strike ----------
    "Sniper 1.0": {
        "tips": ("Playing for score works against it; two players in the room let each take their time.",),
    },
    "Total Recall": {
        "tips": ("Hit every panel once, memorise them, then throw at one picture at a time.",),
    },

    # ---------- Press and Push ----------
    "Direction Inspection 2.0": {
        "tips": (
            "Find all four gems before pressing any, then go left to right by "
            "where they sit on the walls, not the targets shown above. In one "
            "column, top first. Post-it notes help.",
        ),
    },
    "Direction Inspection 3.0": {
        "tips": (
            "Three players is enough: one hunts button 1, one button 2, one "
            "button 3, and anyone who spots a button calls its number. After "
            "half the room, it's a free-for-all.",
        ),
    },
    "Firestorm": {
        "tips": (
            "Count targets against bombs (sets like 4-4-4-3-3), and expect a bomb "
            "on any row with 4 or more targets.",
        ),
        "watch_out": (
            "Not every Mines 8 layout can be done (staff). If the solution shown "
            "after a loss puts a bomb on a target, that layout was impossible.",
        ),
    },
    "Perfect Pairs": {"tips": ("It is 10 seconds from the timer starting at 90, so done by 80.",)},
    "Recollection 2.0": {
        "watch_out": (
            "One mistake re-reveals the whole room, so in practice it needs a "
            "flawless run.",
        ),
    },
    "Total Recall 3.0": {
        "watch_out": (
            "It has been awarded despite lost lives; it seems to count reveals, "
            "like Recollection 2.0.",
        ),
    },
    "Directional Victory": {
        "watch_out": (
            "Slightly early presses register, so you can take button 3 a moment "
            "before button 2 and fail. A button sometimes vanishes from the room "
            "too.",
        ),
    },

    # ---------- Climb ----------
    "Row by Row": {
        "tips": ("A second player can trail the crosser and clean up rows to speed it up.",),
    },
    "Get Low": {
        "tips": ("The reds follow the same pattern up and down. Solo is easier than a pair.",),
    },

    # ---------- Control ----------
    "Old School": {
        "tips": ("One player is enough, using the d-pad only (no B, A).",),
        "watch_out": (
            "The sound also plays on other levels and in Bop, but only "
            "Labyrinth 3 awards the badge.",
        ),
    },
    "Snake Island": {
        "tips": (
            "Three small snakes and two big ones works (two and three too). Big "
            "snakes stay in the safe area until less time is left than they have "
            "lives, then step outside the boundary to run the clock out.",
        ),
    },
    "Fast Feet": {"tips": ("Running non-stop through a solo Bop 7 earns it without counting.",)},

    # ---------- Arena ----------
    "Recollection": {
        "watch_out": ("Not awarding as of October 2026; due back two weeks after the 28 October release.",),
    },
    "Sniper Shot 1.0": {
        "watch_out": ("Not awarding as of October 2026; due back two weeks after the 28 October release.",),
    },

    # ---------- riddles ----------
    "Riddle 1.0": {
        "tips": (
            "It is one game in one room per day, so repeat the check at each "
            "room's sign-in screen. The trophy is sometimes just outside the "
            "How to Play window. Do it before your time starts.",
        ),
    },
    "Riddle 3.0": {
        "tips": (
            "Number the trophies 1 (lowest pitch) to 4 (highest) and play "
            "3212333 222 344 3212333 322321, all 26 notes. The pitches reshuffle "
            "at each sign-in.",
        ),
        "watch_out": (
            "Score checkers in the lobby are often muted; use one inside the "
            "game area, or ask staff to turn the sound up.",
        ),
    },
    "Riddle 4.0": {
        "giveaway": (
            "Sign in at a score checker first. Then scan your wristband at a "
            "game's sign-in screen while a helper taps Sign Out within about a "
            "second. You don't need to start the game."
        ),
        "tips": (
            "Have your helper watch your hand rather than wait for the sound. If "
            "you pressed Start, you were too slow. The photo booth counts as a "
            "game.",
        ),
    },
    "Riddle 5.0": {
        "watch_out": (
            "You shouldn't lose a life doing it; if you do, it is misbehaving. "
            "It tends to work on the first try and not after a timeout, and "
            "staff saw it work on level 2 when level 1 didn't.",
        ),
    },
    "Riddle 6.0": {
        "tips": (
            "Don't lose a life and you get Untouchable too; replay the level and "
            "you get Deja Vu.",
        ),
    },
    "Riddle 7.0": {
        "tips": (
            "Play the big-group games (Sequence, Spellinator) first with extra "
            "players, then split. Swat 7 is easy and Strategy 7 is easy once "
            "memorised; Seeker 7 is hard with few players.",
        ),
        "watch_out": ("Scan Rush and Swarm join the list from 28 October 2026.",),
    },
    "Call Jenny": {
        "tips": (
            "Terminal 2 always starts with a 0 since October 2025, so the chance "
            "comes every game. Each throw should beep; one miss means replaying.",
        ),
    },

    # ---------- Easter eggs ----------
    "Easter Egg Barrage": {
        "tips": (
            "With 5 players it is about 150 shots into one hoop, so go solo or "
            "as a pair. As a pair, both shoot at hoops 2 and 4 the moment the "
            "last basket drops.",
        ),
    },
    "Easter Egg Mega Grid": {
        "tips": (
            "Players report the same button everywhere: standing on the green "
            "in Mega Grid 1, the short wall to your left, the farthest of its "
            "three buttons. The egg picture is often missing; the button glows "
            "light blue.",
            "It lights for a split second as the door opens or Replay is "
            "pressed, so hold the button down before it lights. Fail inside 10 "
            "seconds for a free replay.",
        ),
    },
    "Easter Egg Numbers": {
        "tips": (
            "The egg button lights for 2-3 seconds. The room resets but the "
            "clock doesn't, so you need a quick group to count back up.",
        ),
    },
    "Easter Egg Statues": {
        "tips": (
            "It shows in a countdown phase, never a red one, on a random button "
            "glowing light blue. Playing level 3 first is a myth. With 2 "
            "players, run the walls pressing every button you pass.",
        ),
    },
    "Easter Egg Stopwatch": {
        "tips": ("The draining screen lasts about 3 seconds. With 3 or more players, one can watch the back wall.",),
    },
    "Easter Egg Trivial": {
        "tips": (
            "About 1 game in 12 asks the question, so 40 or more tries is "
            "normal. Use Trivial 2 as a cool-down between other attempts.",
            "Score in the flashing basket while it is still flashing. Which "
            "basket flashes is not fixed.",
        ),
    },
    "Easter Egg Words": {
        "tips": (
            "Since September 2026 EASTER can come as any word, not only the "
            "first. The G turning green instead of costing a life means the egg "
            "is coming.",
        ),
        "watch_out": ("Words 7 doesn't trigger it.",),
    },
}

for _n in range(2, 10):
    TIPS[f"{_n} For {_n}"] = {"tips": (_STREAK, _X_FOR_X)}
for _name in ("Deja Vu", *(f"Deja Vu {_n}.0" for _n in range(2, 10))):
    TIPS[_name] = {"tips": (_DEJA_VU,)}

_BY_NAME = {norm(name): entry for name, entry in TIPS.items()}


def overlay(name: str | None, detail: dict[str, Any]) -> dict[str, Any]:
    """`detail` (a badge_reference.lookup result) with the Discord's tips added.

    Returns a new dict; `detail` itself is left alone.
    """
    extra = _BY_NAME.get(norm(name))
    if not extra:
        return detail
    out = dict(detail)
    for field, value in extra.items():
        out[field] = tuple(out.get(field) or ()) + value if isinstance(value, tuple) else value
    return out
