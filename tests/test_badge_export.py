import re
from datetime import date

from app import badge_export
from app.badge_reference import BADGES, lookup

TODAY = date(2026, 10, 20)

AARON = {"id": 1, "handle": "aaron", "display_name": "Aaron", "earned": 19,
         "possible": 114, "tier": None, "next_tier": "bronze", "to_next": 6}
EDWARD = {"id": 2, "handle": "gmebagholder", "display_name": "Edward", "earned": 33,
          "possible": 114, "tier": "bronze", "next_tier": "silver", "to_next": 17}

COQUITLAM = {
    "location_id": 38, "name": "Coquitlam",
    "games": [
        {"room": "Grid", "game_id": 1101, "name": "Zones", "levels": list(range(10))},
        {"room": "Hide", "game_id": 1305, "name": "Relay", "levels": list(range(10))},
    ],
    "beaten": {
        1: {(1101, lvl) for lvl in (2, 3, 4)},
        2: {(1101, lvl) for lvl in range(10)} | {(1305, 0), (1305, 1), (1305, 4)},
    },
}
LANGLEY = {
    "location_id": 72, "name": "Langley",
    "games": [
        {"room": "Grid", "game_id": 1101, "name": "Zones", "levels": list(range(10))},
    ],
    "beaten": {2: set()},     # Aaron has never played here
}


def _badge(badge_id, name, description, *, where=None, here=None, **extra):
    """A badge the way /api/badge-data sends it: the community detail looked up
    by name, the way `_describe_badges` does, plus where and here."""
    detail = lookup(name, description)
    return {
        "badge_id": badge_id, "name": name, "description": description, "stars": 10,
        **{k: v for k, v in detail.items() if k != "name"},
        "where": where or {}, "here": here or {}, **extra,
    }


def _state(earned, progress=0, total=0):
    return {"earned": earned, "progress": progress, "total_progress": total,
            "earned_on": None}


def _build(badges, states, *, players=(AARON, EDWARD), locations=(COQUITLAM, LANGLEY),
           choice=38):
    return badge_export.build(
        players=list(players), badges=badges, states=states,
        locations=list(locations), choice=choice, today=TODAY,
    )


def _headings(text):
    """The badge headings: one level under the who's-missing groups when there
    are several players, and the gamemode sections below head their rooms the
    same way, so only the badge section is read."""
    lines = text.split("## Badges still missing", 1)[1].split("## Gamemodes at", 1)[0].splitlines()
    marker = "#### " if any(line.startswith("#### ") for line in lines) else "### "
    return [line[len(marker):] for line in lines if line.startswith(marker)]


MARATHON = _badge(
    1, "The Marathon",
    "Beat Relay level 5, Mega Relay level 5 and Laser Relay level 5 in a row.",
)
TIPTOEING = _badge(2, "Tiptoeing", "Complete Zones level 5 without stepping on any wrong colours")
TEN_FOR_TEN = _badge(3, "10 For 10", "Win ten different level 10 games in a row")


def test_lists_only_what_somebody_selected_still_lacks_most_missing_first():
    """Nobody-has-it first, since one game pays both; equal hauls go easiest
    first, the badge farmer's order. A badge both hold isn't mentioned."""
    held = _badge(4, "Night Owl", "Win a game after 11 PM")
    states = {
        "1": {"1": _state(False), "2": _state(False), "3": _state(False), "4": _state(True)},
        "2": {"1": _state(True), "2": _state(False), "3": _state(False), "4": _state(True)},
    }

    text = _build([MARATHON, TIPTOEING, TEN_FOR_TEN, held], states)

    # Tiptoeing (Medium) and 10 For 10 (Very Hard) are missing for both.
    assert _headings(text)[:3] == ["Tiptoeing", "10 For 10", "The Marathon"]
    assert "Night Owl" not in text
    assert "## Badges still missing (3)" in text
    # Grouped by who lacks them, rather than a "missing" line per badge.
    assert "### Missing for all of us (2)" in text
    assert "### Missing for Aaron only (1)" in text
    assert "- Missing:" not in text


def test_a_badge_everyone_holds_is_named_nowhere():
    """The brief used to give The Marathon as its stacking example, and Claude
    planned a route around it for two players who both held it."""
    states = {p: {"1": _state(True), "2": _state(True), "3": _state(False)} for p in ("1", "2")}
    text = _build([MARATHON, TIPTOEING, TEN_FOR_TEN], states)

    assert _headings(text) == ["10 For 10"]
    assert "Marathon" not in text
    assert "Tiptoeing" not in text


def test_the_brief_names_no_badge_and_says_an_unlisted_one_is_not_wanted():
    """Tips and overlaps still name other badges, held or not, so the brief
    says what a name outside the list means."""
    for name in {r["name"] for r in BADGES.values()}:
        assert not re.search(rf"\b{re.escape(name)}\b", badge_export.BRIEF), name

    text = _build([TIPTOEING], {"1": {}, "2": {}}, choice=38)
    assert ('A badge named anywhere else but not listed there — in a tip, a note or an'
            ' "Overlaps with" line — is one all of us already hold or one that can\'t be'
            ' done here, so don\'t plan for it.') in text
    anywhere = _build([TIPTOEING], {"1": {}, "2": {}}, choice=None)
    assert "is one all of us already hold, so don't plan for it." in anywhere


def test_the_brief_asks_for_each_badge_to_be_described_where_the_plan_lists_it():
    """A route that says only "earns Tiptoeing" sends the reader back to the
    export at the venue to find out what Tiptoeing wants."""
    text = _build([TIPTOEING], {"1": {}, "2": {}})
    assert ("Wherever you list badges in a block or table, put a concise description"
            " of what each one asks for beside its name") in text


def test_a_badge_with_no_state_row_counts_as_missing():
    text = _build([TIPTOEING], {"1": {}, "2": {"2": _state(True)}})
    assert "### Missing for Aaron only (1)" in text


def test_one_player_needs_no_groups():
    text = _build([TIPTOEING], {"1": {}}, players=(AARON,))
    assert "Missing for" not in text
    assert "\n### Tiptoeing\n" in text


def test_the_requirement_is_activate_s_wording_and_the_rooms_say_which_gamemode():
    states = {"1": {"1": _state(False)}, "2": {"1": _state(False)}}
    text = _build([MARATHON], states)

    assert ("- Requirement: Beat Relay level 5, Mega Relay level 5 and Laser Relay"
            " level 5 in a row.") in text
    assert ("- Room: Hide (Relay) + Mega Grid (Mega Relay) + Mega Laser (Laser Relay)"
            " — needs all of them") in text
    assert "- Overlaps with: 5 for 5" in text


def test_lines_that_say_nothing_are_left_out():
    """No room means any room (the brief says so), "Depends" is no player
    count, and star values buy nothing since badges stopped paying in stars."""
    five = _badge(5, "5 For 5", "Win five different level 5 games in a row")
    assert five["players"] == "Depends"
    text = _build([five], {"1": {"5": _state(False)}, "2": {"5": _state(False)}})

    assert "- Room:" not in text
    assert "- Difficulty: Easy\n" in text
    assert "Stars" not in text


def test_a_chosen_location_drops_a_definite_no_and_keeps_what_it_can_t_rule_out():
    no = _badge(6, "Row By Row", "Complete Technique level 7 by only using one row at a time.",
                where={"38": {"status": "no", "why": "no Climb room"}})
    unknown = _badge(7, "Photobomb", "Take a photo in the photo room",
                     where={"38": {"status": "unknown", "why": "can't see it"}})
    roomless = _badge(8, "Night Owl", "Win a game after 11 PM")
    states = {"1": {}, "2": {}}

    text = _build([no, unknown, roomless], states, choice=38)

    assert sorted(_headings(text)) == ["Night Owl", "Photobomb"]
    assert text.startswith("# Activate badge plan — Aaron and Edward at Coquitlam\n")
    # The scope settled "yes"; only the answer it couldn't settle is written.
    assert text.count("- Where:") == 1
    assert "- Where: can't tell if Coquitlam can host it — can't see it" in text


def test_a_where_line_appears_only_where_some_location_says_no():
    everywhere = _badge(9, "Night Owl", "Win a game after 11 PM", where={
        "38": {"status": "yes", "why": None}, "72": {"status": "yes", "why": None},
    })
    text = _build([everywhere], {"1": {}, "2": {}}, choice=badge_export.TRACKED)
    assert "- Where:" not in text


def test_any_tracked_location_needs_one_that_isn_t_a_no():
    """Unlike the page's option of the same name, a badge somebody holds isn't
    kept where no tracked location can host it: a route can't visit it."""
    nowhere = _badge(6, "Row By Row", "x", where={
        "38": {"status": "no", "why": "no Climb room"},
        "72": {"status": "no", "why": "no Climb room"},
    })
    langley_only = _badge(9, "Untouchable 5.0", "y", where={
        "38": {"status": "no", "why": "no Pipes room"},
        "72": {"status": "yes", "why": None},
    })
    states = {"1": {"6": _state(False)}, "2": {"6": _state(True)}}

    text = _build([nowhere, langley_only], states, choice=badge_export.TRACKED)

    assert _headings(text)[0] == "Untouchable 5.0"
    assert "Row By Row" not in text
    assert "lack at Coquitlam or Langley" in text
    assert "- Where: Coquitlam no (no Pipes room); Langley yes" in text


def test_anywhere_keeps_everything_and_lists_every_location_s_gamemodes():
    nowhere = _badge(6, "Row By Row", "x", where={
        "38": {"status": "no", "why": "no Climb room"},
    })
    text = _build([nowhere], {"1": {}, "2": {}}, choice=None)

    assert "Row By Row" in _headings(text)
    assert "## Gamemodes at Coquitlam" in text
    assert "## Gamemodes at Langley" in text


def test_progress_and_location_facts_are_for_those_still_missing_it():
    """What a holder has beaten says nothing about a badge they already have."""
    activated = _badge(10, "Activated", "Beat all game levels", here={
        "38": [{"label": "Levels", "text": "490 to beat", "players": {
            "1": "169 beaten, 321 to go", "2": "242 beaten, 248 to go",
        }}],
        "72": [{"label": "Levels", "text": "510 to beat", "players": {}}],
    })
    states = {"1": {"10": _state(False, 259, 0)}, "2": {"10": _state(True, 300, 0)}}

    text = _build([activated], states, choice=38)

    # One location in scope, already named in the title.
    assert "- Levels: 490 to beat (Aaron: 169 beaten, 321 to go)" in text
    assert "510 to beat" not in text           # Langley isn't where we're going
    assert "- Progress: Aaron 259 so far" in text


def test_progress_with_a_target_reads_as_a_fraction():
    tally = _badge(11, "Friendly Enemies", "Play 25 competitive mode games")
    states = {"1": {"11": _state(False, 4, 25)}, "2": {"11": _state(False, 0, 25)}}
    assert "- Progress: Aaron 4/25\n" in _build([tally], states)


def test_the_answer_rides_along_for_an_easter_egg():
    egg = _badge(12, "Old School", "Earn 30 bonus lives in Labyrinth level 3.")
    text = _build([egg], {"1": {}, "2": {}})
    assert "- Answer: On Labyrinth 3, before the game starts" in text
    assert "- Hint:" not in text                     # spent once the answer is there


def test_a_hint_without_an_answer_is_kept():
    riddle = _badge(13, "Some Riddle", "riddle text", hint="Think about it")
    assert "- Hint: Think about it" in _build([riddle], {"1": {}, "2": {}})


def test_a_tip_several_badges_share_is_written_once_above_them():
    shared, own = "Start with your shakiest game.", "Zones is a gimme."
    five = _badge(5, "5 For 5", "five", tips=(shared,))
    ten = _badge(14, "10 For 10", "ten", tips=(shared, own))
    text = _build([ten, five], {"1": {}, "2": {}})

    assert text.count(shared) == 1
    assert f"- **Tip** for 5 For 5, 10 For 10: {shared}" in text
    assert text.index("## Notes for several badges") < text.index("## Badges still missing")
    assert f"- Tip: {own}" in text


def test_gamemodes_show_each_player_s_beaten_levels_numbered_from_one():
    text = _build([], {"1": {}, "2": {}}, choice=38)

    assert "- **Zones**, 10 levels, best with 5 (no clear consensus) — Aaron 3–5; Edward all" in text
    assert "- **Relay**, 10 levels, best with 5 — Aaron none; Edward 1–2, 5" in text
    assert "None — every badge in scope is already held by all of us." in text


def test_rules_are_written_once_and_only_for_gamemodes_a_badge_names():
    """Tiptoeing names Zones, so its rules are worth reading; nothing listed
    names Relay, whose line still carries the beaten levels."""
    text = _build([TIPTOEING], {"1": {}, "2": {}}, choice=None)

    rules = "  - Listen to the colour called out by the announcer."
    assert sum(line.startswith(rules) for line in text.splitlines()) == 1
    assert "Everyone starts by holding a coloured button" not in text
    assert "- **Relay**, 10 levels" in text
    # Aaron has no snapshot at Langley at all: that is not "none beaten".
    langley = text.split("## Gamemodes at Langley", 1)[1]
    assert "Aaron no scores here; Edward none" in langley


def test_ranges():
    assert badge_export._ranges([1, 2, 3, 5, 7, 8]) == "1–3, 5, 7–8"
    assert badge_export._ranges([4]) == "4"
