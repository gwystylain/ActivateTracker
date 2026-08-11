import json
from pathlib import Path

from app.badge_reference import BADGES, lookup, norm

BADGE_FIXTURE = Path(__file__).parent / "fixtures" / "badges_gmebagholder.json"

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


def test_a_badge_no_document_covers_gets_the_full_empty_shape():
    """Mascot is Activate's, not the community's, yet. Every key still present,
    so the front end never special-cases it."""
    empty = lookup("Mascot", "whatever it is")

    assert empty["rooms"] == () and empty["rooms_mode"] == "any"
    assert empty["tips"] == () and empty["watch_out"] == () and empty["fun_facts"] == ()
    for field in ("level", "difficulty", "players", "overlapping", "notes",
                  "hint", "giveaway"):
        assert empty[field] is None, field


def test_a_choice_of_rooms_is_not_a_requirement_for_all_of_them():
    """"Mega Laser or Trench" means either will do; The Marathon's three rooms
    are all needed. Confusing the two sends someone to a room they can't use."""
    either = lookup("Adrenaline Junkie")
    assert either["rooms"] == ("Mega Laser", "Trench")
    assert either["rooms_mode"] == "any"

    marathon = lookup("The Marathon")
    assert set(marathon["rooms"]) == {"Hide", "Mega Grid", "Mega Laser"}
    assert marathon["rooms_mode"] == "all"


def test_easter_eggs_and_riddles_carry_their_hint_and_answer():
    """Spoilers are held here; hiding them until asked for is the page's job."""
    flip = lookup("Easter Egg Flip")
    assert flip["hint"] and flip["giveaway"]
    assert lookup("Riddle 2.0")["giveaway"].startswith("On the score checker iPad")


def test_every_live_badge_but_one_picks_up_reference_detail():
    """A regression fence around the name join: if the document is re-exported
    and the match rate collapses, the page quietly loses its detail."""
    live = json.loads(BADGE_FIXTURE.read_text(encoding="utf-8"))
    described = [
        b for b in live
        if any(lookup(b["name"], b["description"])[f]
               for f in ("rooms", "difficulty", "tips", "notes", "hint"))
    ]

    assert len(live) - len(described) == 1
    assert [b["name"] for b in live if b not in described] == ["Mascot"]


def test_every_record_is_well_formed():
    for key, rec in BADGES.items():
        assert "|" in key, key
        assert rec["rooms_mode"] in ("any", "all"), key
        for room in rec["rooms"]:
            assert room in KNOWN_ROOMS, f"{key}: unknown room {room!r}"
        # A single room can't be a set of rooms, and "all" of one is just "any".
        if len(rec["rooms"]) < 2:
            assert rec["rooms_mode"] == "any", key

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
