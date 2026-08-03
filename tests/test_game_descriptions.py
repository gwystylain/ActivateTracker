from app.game_descriptions import DESCRIPTIONS, describe


def test_describe_finds_a_gamemode_by_room_and_name():
    assert describe("Hoops", "Barrage").startswith("Sink the required number of baskets")


def test_cooperative_rules_win_over_the_competitive_game_of_the_same_name():
    """Hoops runs a competitive Barrage too; the site's leaderboard is the co-op one."""
    assert "45 seconds" not in describe("Hoops", "Barrage")


def test_the_room_decides_when_two_rooms_share_a_gamemode_name():
    assert describe("Mega Laser", "Defuse").endswith("survive 8 rounds to win.")
    assert describe("Trench", "Defuse").endswith("defuse enough targets to win.")


def test_an_unambiguous_name_resolves_without_a_matching_room():
    assert describe("Basketball", "15 Green") == describe("Hoops", "15 Green")


def test_an_ambiguous_name_without_its_room_gets_nothing():
    """Better no tooltip than the other room's rules."""
    assert describe("Somewhere Else", "Defuse") is None


def test_gamemodes_the_document_does_not_cover_get_nothing():
    assert describe("Hoops", "Nonexistent") is None
    assert describe("Nonexistent", "Nonexistent") is None
    assert describe(None, None) is None


def test_every_description_is_a_non_empty_single_paragraph():
    for room, games in DESCRIPTIONS.items():
        for name, desc in games.items():
            where = f"{room}/{name}"
            assert desc.strip() == desc, where
            assert "\n" not in desc, where
            # Grip's Loop ends on a bracketed worked example, kept verbatim.
            assert desc.endswith((".", "!", ")")), where
            # Sub-bullets in the source document are tips, not rules; none of
            # them should have been swept into a description.
            assert "Tip:" not in desc and "Watch Out:" not in desc, where
