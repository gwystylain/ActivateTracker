from app.master_document import GAMEMODES, lookup


def test_lookup_finds_a_gamemode_by_room_and_name():
    rec = lookup("Hoops", "Barrage")
    assert rec["description"].startswith("Sink the required number of baskets")
    assert rec["optimal_players"] == 2


def test_cooperative_rules_win_over_the_competitive_game_of_the_same_name():
    """Hoops runs a competitive Barrage too; the site's leaderboard is the co-op one."""
    assert "45 seconds" not in lookup("Hoops", "Barrage")["description"]


def test_the_room_decides_when_two_rooms_share_a_gamemode_name():
    assert lookup("Mega Laser", "Defuse")["description"].endswith("survive 8 rounds to win.")
    assert lookup("Trench", "Defuse")["description"].endswith("defuse enough targets to win.")


def test_an_unambiguous_name_resolves_without_a_matching_room():
    assert lookup("Basketball", "15 Green") == lookup("Hoops", "15 Green")


def test_an_ambiguous_field_without_its_room_is_dropped_on_its_own():
    """Zap's rules match across rooms; only its player count disagrees."""
    stray = lookup("Somewhere Else", "Zap")
    assert stray["description"] == lookup("Trench", "Zap")["description"]
    assert stray["optimal_players"] is None       # 3 at Mega Laser, unrecorded at Trench
    assert lookup("Somewhere Else", "Defuse")["description"] is None


def test_gamemodes_the_document_does_not_cover_get_nothing():
    empty = {"description": None, "optimal_players": None, "optimal_disputed": False}
    assert lookup("Hoops", "Nonexistent") == empty
    assert lookup("Nonexistent", "Nonexistent") == empty
    assert lookup(None, None) == empty


def test_an_unrecorded_player_count_is_none_not_a_guess():
    """13 of the 76 gamemodes have a blank or '?' in the document's column."""
    assert lookup("Grip", "Firewall")["optimal_players"] is None      # the '?' cell
    assert lookup("Arena", "Digby")["optimal_players"] is None        # a blank cell
    assert lookup("Grip", "Firewall")["description"] is not None      # but it has rules


def test_a_disputed_count_keeps_its_number_and_says_so():
    """`**` in the document means a number is written down but not agreed on."""
    rec = lookup("Climb", "Technique")
    assert (rec["optimal_players"], rec["optimal_disputed"]) == (5, True)
    assert lookup("Hoops", "Trivial")["optimal_disputed"] is False


def test_every_record_is_well_formed():
    for room, games in GAMEMODES.items():
        for name, rec in games.items():
            where = f"{room}/{name}"
            desc = rec["description"]
            assert desc.strip() == desc and "\n" not in desc, where
            # Grip's Loop ends on a bracketed worked example, kept verbatim.
            assert desc.endswith((".", "!", ")")), where
            # Sub-bullets in the source document are tips, not rules; none of
            # them should have been swept into a description.
            assert "Tip:" not in desc and "Watch Out:" not in desc, where

            optimal = rec.get("optimal_players")
            assert optimal is None or optimal in (2, 3, 4, 5), where
            # A "no consensus" marker with no number behind it is meaningless.
            assert not (rec.get("optimal_disputed") and optimal is None), where
