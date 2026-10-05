import json
import re
from pathlib import Path

from app import badge_reference
from app.badge_reference import BADGES, lookup, norm
from app.master_document import GAMEMODES
from app.tools import gen_badge_reference as gen

BADGE_FIXTURE = Path(__file__).parent / "fixtures" / "badges_gmebagholder.json"

# In the fixture, which predates their retirement, and in no source now: the
# API stopped serving Mascot in October 2026 and the document moved it to its
# notes on retired badges. Go For Gold, Recollection and Sniper Shot 1.0 left
# the API too, but the document still describes them, so they still resolve.
RETIRED_AND_UNDESCRIBED = {"Mascot"}

# Rooms the community documents name. Superset of any one location: the
# documents cover every room Activate has built anywhere.
KNOWN_ROOMS = {
    "Arena", "Climb", "Control", "Grid", "Grip", "Hide", "Hoops", "Laser",
    "Mega Grid", "Mega Laser", "Photo", "Pipes", "Portals", "Press", "Push",
    "Scan", "Strike", "Trench",
}


def test_the_same_name_in_two_rooms_resolves_by_description():
    """The whole reason this is keyed on more than the name: Untouchable 5.0 is
    two badges, and knowing which one you mean decides which building to go to."""
    piperooni = lookup("Untouchable 5.0", "Win level 5 of Piperooni without losing a life")
    wormholes = lookup("Untouchable 5.0", "Win level 5 of Wormholes without losing a life")

    assert piperooni["rooms"] == ("Pipes",)
    assert wormholes["rooms"] == ("Portals",)


def test_the_name_alone_drops_what_the_two_disagree_on_and_keeps_the_rest():
    """Same rule as master_document.lookup: answer with the wrong badge's room
    and the reader drives to the wrong location."""
    stray = lookup("Untouchable 5.0")

    assert stray["rooms"] == ()                 # the two disagree — no answer
    assert stray["difficulty"] == "Hard"        # they agree — still answered


def test_an_unambiguous_name_resolves_without_its_description():
    assert lookup("Easter Egg Flip")["rooms"] == ("Strike",)
    assert lookup("Easter Egg Flip", "mismatched wording")["rooms"] == ("Strike",)


def test_names_are_matched_across_the_sources_own_spellings():
    """The API writes "Activ8" and "10 for 10"; the document writes "ACTIV8"
    and "10 For 10". 117 of the 118 live badges only match once normalised."""
    assert norm("ACTIV8") == norm("Activ8")
    assert lookup("Activ8")["difficulty"] == lookup("ACTIV8")["difficulty"]
    assert lookup("10 for 10")["difficulty"] is not None


def test_a_badge_nothing_covers_gets_the_full_empty_shape():
    """Activate adds badges the community documents haven't reached yet, so a
    miss has to be survivable: every key still present, and the front end never
    special-cases it."""
    empty = lookup("Some Badge Nobody Has Written Up", "whatever it is")

    assert empty["rooms"] == () and empty["rooms_mode"] == "any"
    assert empty["tips"] == () and empty["watch_out"] == () and empty["fun_facts"] == ()
    assert empty["difficulty_estimated"] is False
    for field in ("level", "difficulty", "players", "overlapping", "notes",
                  "hint", "giveaway", "difficulty_note"):
        assert empty[field] is None, field


def test_a_choice_of_rooms_is_not_a_requirement_for_all_of_them():
    """"Mega Laser or Trench" means either will do; The Marathon's three rooms
    are all needed. Confusing the two sends someone to a room they can't use.

    The document wrote three badges as that choice until October 2026, when it
    narrowed all three to Mega Laser — so the parser is pinned directly."""
    assert gen.parse_rooms("Mega Laser or Trench") == (("Mega Laser", "Trench"), "any")
    assert lookup("Adrenaline Junkie")["rooms"] == ("Mega Laser",)

    marathon = lookup("The Marathon")
    assert set(marathon["rooms"]) == {"Hide", "Mega Grid", "Mega Laser"}
    assert marathon["rooms_mode"] == "all"


def test_easter_eggs_and_riddles_carry_their_hint_and_answer():
    """Spoilers are held here; hiding them until asked for is the page's job."""
    flip = lookup("Easter Egg Flip")
    assert flip["hint"] and flip["giveaway"]
    assert lookup("Riddle 2.0")["giveaway"].startswith("On the score checker iPad")


def test_every_live_badge_picks_up_reference_detail():
    """A regression fence around the name join: if the document is re-exported
    and the match rate collapses, the page quietly loses its detail."""
    live = json.loads(BADGE_FIXTURE.read_text(encoding="utf-8"))
    bare = [
        b["name"] for b in live
        if not any(lookup(b["name"], b["description"])[f]
                   for f in ("rooms", "difficulty", "tips", "notes", "hint"))
    ]

    assert set(bare) == RETIRED_AND_UNDESCRIBED


def test_every_record_is_well_formed():
    for key, rec in BADGES.items():
        assert "|" in key, key
        assert rec["rooms_mode"] in ("any", "all"), key
        for room in rec["rooms"]:
            assert room in KNOWN_ROOMS, f"{key}: unknown room {room!r}"
        # A single room can't be a set of rooms, and "all" of one is just "any".
        if len(rec["rooms"]) < 2:
            assert rec["rooms_mode"] == "any", key
        # Every gamemode is one the master document runs in that badge's room —
        # the page checks a location's catalog for exactly these pairs.
        assert isinstance(rec["games"], tuple), key
        for room, game in rec["games"]:
            assert room in rec["rooms"], f"{key}: {game!r} in {room!r}"
            assert game in GAMEMODES[room], f"{key}: {game!r} not run in {room!r}"

        for field in ("tips", "watch_out", "fun_facts"):
            assert isinstance(rec[field], tuple), key
            for line in rec[field]:
                assert line == line.strip() and line, key

        # The generator splits the document on its own field labels; a label
        # showing up inside a value means one of them was swept into another.
        for field in ("notes", "hint", "giveaway"):
            value = rec[field]
            if value:
                assert not value.startswith(("Tip:", "Room:", "Hint:", "Giveaway:")), key


# ---------- estimated difficulties ----------

def _records(**names):
    """Minimal generator records, keyed the way build() keys them."""
    out = {}
    for name, difficulty in names.items():
        rec = gen._empty_record(name)
        rec["difficulty"] = difficulty
        out[gen.key_for(name, "")] = rec
    return out


def test_an_estimate_fills_a_hole_and_says_so(monkeypatch):
    """A hole in the column is worse than a soft answer, but the soft answer
    must not pass for a sourced one — so it carries a flag and its reasoning."""
    monkeypatch.setattr(gen, "ESTIMATES", {
        "photobomb": {"difficulty": "Easy", "why": "Estimated: just take the photo."},
    })
    records = _records(Photobomb=None)
    gen._apply_estimates(records)

    rec = next(iter(records.values()))
    assert (rec["difficulty"], rec["difficulty_estimated"]) == ("Easy", True)
    assert rec["difficulty_note"] == "Estimated: just take the photo."


def test_an_estimate_steps_aside_once_a_source_grades_the_badge(monkeypatch, capsys):
    """What happened to Photobomb in October 2026: ryflix graded it, and the
    estimate retired itself loudly rather than arguing."""
    monkeypatch.setattr(gen, "ESTIMATES", {
        "photobomb": {"difficulty": "Medium", "why": "Estimated."},
    })
    records = _records(Photobomb="Easy")
    gen._apply_estimates(records)

    rec = next(iter(records.values()))
    assert (rec["difficulty"], rec["difficulty_estimated"]) == ("Easy", False)
    assert rec["difficulty_note"] is None
    assert "dropping the estimate" in capsys.readouterr().err


def test_no_estimate_is_in_force():
    """Both former estimates were retired — Photobomb graded by ryflix, Mascot
    dropped by Activate — so every grade on the page is a sourced one."""
    graded = lookup("Easter Egg Flip")
    assert graded["difficulty"] == "Hard"
    assert graded["difficulty_estimated"] is False
    assert graded["difficulty_note"] is None

    assert lookup("Photobomb")["difficulty"] == "Easy"
    assert [r["name"] for r in BADGES.values() if r["difficulty_estimated"]] == []


def test_every_live_badge_now_has_a_difficulty():
    live = json.loads(BADGE_FIXTURE.read_text(encoding="utf-8"))
    ungraded = [
        b["name"] for b in live
        if not lookup(b["name"], b["description"])["difficulty"]
    ]
    assert set(ungraded) == RETIRED_AND_UNDESCRIBED


def test_a_grade_is_one_of_the_documents_four():
    """The scale is closed. A fifth value would sort off the end of the
    difficulty column and drop out of its filter."""
    scale = {"Easy", "Medium", "Hard", "Very Hard"}
    for key, rec in BADGES.items():
        assert rec["difficulty"] in scale, key
        # A note explains an estimate; on a sourced grade it would be claiming
        # something about a value this repo didn't choose.
        assert bool(rec["difficulty_note"]) == rec["difficulty_estimated"], key


def test_no_field_carries_markup_through_to_the_page():
    """Two ryflix notes ship an <a> tag. Everything is rendered with
    textContent — third-party text must never be parsed as markup — so a tag
    left in the data would show up as literal angle brackets, and stripping it
    outright would lose the link."""
    tag = re.compile(r"<[a-zA-Z/]")
    for key, rec in BADGES.items():
        for field in ("level", "difficulty", "players", "overlapping", "notes",
                      "hint", "giveaway", "difficulty_note"):
            value = rec[field]
            assert not (value and tag.search(value)), f"{key}: markup in {field}"
        for field in ("tips", "watch_out", "fun_facts"):
            for line in rec[field]:
                assert not tag.search(line), f"{key}: markup in {field}"

    notes = lookup("Call Jenny")["notes"]
    assert "Reference (https://www.youtube.com/watch?v=tHL2XeuA6Yg)" in notes


# ---------- gamemodes ----------

def test_a_badge_names_its_gamemode_whatever_the_sentence_looks_like():
    assert lookup("Fast Feet")["games"] == (("Control", "Bop"),)                # "Complete Bop level 7"
    assert lookup("Untouchable")["games"] == (("Mega Grid", "Mega Grid"),)      # "Win level 1 of Mega Grid"
    assert lookup("Easter Egg Statues")["games"] == (("Mega Grid", "Statues"),)  # only in the name
    assert lookup("Riddle 5.0")["games"] == (("Pipes", "Scramble"),)            # only in Game/Level


def test_the_longest_name_wins_and_spends_its_words():
    """"Mega Relay" must not also count as Hide's "Relay" — the bare one that
    opens the sentence is Hide's."""
    assert lookup("The Marathon")["games"] == (
        ("Hide", "Relay"), ("Mega Grid", "Mega Relay"), ("Mega Laser", "Laser Relay"),
    )


def test_one_name_in_both_of_a_choice_of_rooms_counts_in_each():
    text = "Lose 4 lives before scoring a point, then beat Zap level 8"
    assert gen.find_games(text, ("Mega Laser", "Trench")) == (
        ("Mega Laser", "Zap"), ("Trench", "Zap"),
    )


def test_a_competitive_game_names_nothing_and_leaves_the_room_to_decide():
    """Tails has no levels, so the site never catalogs it and neither does the
    master document. Snake Island is placed by Control alone."""
    snake = lookup("Snake Island")
    assert snake["rooms"] == ("Control",)
    assert snake["games"] == ()


def test_a_ryflix_only_badge_is_kept_and_moved_to_its_gamemode_s_room():
    """The October 2026 document moved Steady Stream into its notes, leaving
    ryflix the only source — which says Push, where Photon Rush has never run."""
    steady = lookup("Steady Stream", "Complete Photon Rush level 6 while pushing the button every second")
    assert steady["rooms"] == ("Laser",)
    assert steady["games"] == (("Laser", "Photon Rush"),)
    assert steady["difficulty"] == "Hard"


def test_a_room_the_document_gives_is_never_second_guessed():
    rooms, mode, games = gen._place(
        "X", "Complete Photon Rush level 6", ("Push",), "any", trusted=True,
    )
    assert (rooms, games) == (("Push",), ())


def test_games_survive_rendering_as_tuples():
    """json.dumps writes a list, and a list can't sit in the set the name
    index compares fields with."""
    rec = gen._empty_record("X")
    rec["games"] = (("Hide", "Relay"),)
    source = gen.render({"x|y": rec})
    assert '("Hide", "Relay"),' in source
    assert all(isinstance(pair, tuple) for r in badge_reference.BADGES.values() for pair in r["games"])
