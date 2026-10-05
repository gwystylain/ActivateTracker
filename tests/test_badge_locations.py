from app.badge_locations import availability, facts
from app.badge_reference import lookup

TEN = list(range(10))


def _games(*rows):
    """(room, game_id, name) rows, ten levels each — as every co-op game has."""
    return [{"room": r, "game_id": gid, "name": n, "levels": TEN} for r, gid, n in rows]


# Trimmed to what the tests touch, but the rooms and what they run are live:
# Langley's Laser room runs Sneak and Chopper, Coquitlam has no Laser at all.
LANGLEY = _games(
    ("Hide", 101, "Words"), ("Hide", 102, "Sequence"), ("Hide", 103, "Relay"),
    ("Laser", 201, "Sneak"), ("Laser", 202, "Chopper"),
    ("Pipes", 301, "Scramble"), ("Pipes", 302, "Piperooni"),
    ("Mega Grid", 401, "Mega Relay"), ("Mega Grid", 402, "Statues"),
    ("Mega Laser", 501, "Laser Relay"),
    ("Control", 601, "Bop"),
)
COQUITLAM = _games(
    ("Hide", 101, "Words"), ("Hide", 102, "Sequence"), ("Hide", 103, "Relay"),
    ("Portals", 701, "Wormholes"), ("Portals", 702, "Stopwatch"),
    ("Mega Grid", 401, "Mega Relay"),
)


def _badge(name, description=None):
    return {"name": name, "description": description, **lookup(name, description)}


# ---------- availability ----------

def test_a_badge_with_no_room_can_be_done_anywhere():
    assert availability(_badge("Night Owl"), LANGLEY) is None


def test_a_room_the_location_has_running_the_named_gamemode_is_a_yes():
    assert availability(_badge("Fast Feet"), LANGLEY) == {"status": "yes", "why": None}


def test_a_scoring_room_the_location_lacks_is_a_no():
    got = availability(_badge("Fast Feet"), COQUITLAM)
    assert got == {"status": "no", "why": "no Control room"}


def test_the_room_alone_is_not_enough_when_it_runs_something_else():
    """The whole reason `games` exists: Langley has a Laser room, and it runs
    Chopper where Photon Rush used to be."""
    steady = _badge(
        "Steady Stream", "Complete Photon Rush level 6 while pushing the button every second"
    )
    got = availability(steady, LANGLEY)
    assert got["status"] == "no"
    assert got["why"] == "Laser room runs Chopper and Sneak, not Photon Rush"


def test_a_competitive_game_falls_back_to_the_room():
    """Tails isn't catalogued anywhere, so Control is all there is to check."""
    assert availability(_badge("Snake Island"), LANGLEY)["status"] == "yes"


def test_a_room_the_site_never_lists_is_unknown_not_absent():
    """The site lists only scoring rooms, so the photo room is missing from every
    location whether or not the building has one."""
    got = availability(_badge("Photobomb"), LANGLEY)
    assert got["status"] == "unknown"
    assert got["why"].startswith("Photo isn't a room the site keeps scores for")


def test_every_room_of_a_set_is_needed():
    marathon = _badge("The Marathon")
    assert availability(marathon, LANGLEY)["status"] == "yes"
    got = availability(marathon, COQUITLAM)      # no Mega Laser there
    assert got == {"status": "no", "why": "no Mega Laser room"}


def test_one_room_of_a_choice_is_enough():
    either = {"name": "X", "rooms": ("Mega Laser", "Trench"), "rooms_mode": "any",
              "games": (("Mega Laser", "Laser Relay"), ("Trench", "Zap"))}
    assert availability(either, LANGLEY)["status"] == "yes"
    got = availability(either, COQUITLAM)
    assert got == {"status": "no", "why": "no Mega Laser room; no Trench room"}


def test_the_two_untouchable_5_0s_are_in_different_buildings():
    piperooni = _badge("Untouchable 5.0", "Win level 5 of Piperooni without losing a life")
    wormholes = _badge("Untouchable 5.0", "Win level 5 of Wormholes without losing a life")
    assert availability(piperooni, LANGLEY)["status"] == "yes"
    assert availability(piperooni, COQUITLAM)["status"] == "no"
    assert availability(wormholes, COQUITLAM)["status"] == "yes"
    assert availability(wormholes, LANGLEY)["status"] == "no"


# ---------- facts ----------

def test_riddle_7_lists_the_location_s_s_games():
    """The example that asked for this: the list depends on the building."""
    [fact] = facts(_badge("Riddle 7.0"), LANGLEY, {})
    assert fact["label"] == "S games"
    assert fact["text"] == "Scramble, Sequence, Sneak, Statues — 4 in all"

    [fact] = facts(_badge("Riddle 7.0"), COQUITLAM, {})
    assert fact["text"] == "Sequence, Stopwatch — 2 in all"


def test_riddle_7_says_which_level_7s_each_player_still_lacks():
    beaten = {
        1: {(102, 6), (301, 6)},                          # Sequence, Scramble
        2: {(102, 6), (301, 6), (201, 6), (402, 6)},      # all four
    }
    [fact] = facts(_badge("Riddle 7.0"), LANGLEY, beaten)
    assert fact["players"]["1"] == "level 7 not beaten yet in Sneak, Statues"
    assert fact["players"]["2"] == "level 7 already beaten in all 4, one at a time"


def test_riddle_7_ignores_a_game_without_a_level_7():
    short = [{"room": "Hide", "game_id": 9, "name": "Spellinator", "levels": [0, 1, 2]}]
    [fact] = facts(_badge("Riddle 7.0"), short, {})
    assert fact["text"] == "none here"


def test_activated_and_halfway_count_this_location_s_levels():
    beaten = {1: {(101, 0), (101, 1), (102, 0)}}
    [act] = facts(_badge("Activated"), COQUITLAM, beaten)
    assert act["text"] == "60 to beat"
    assert act["players"]["1"] == "3 beaten, 57 to go"

    [half] = facts(_badge("Halfway Mark"), COQUITLAM, beaten)
    assert half["text"] == "30 of 60 levels"
    assert half["players"]["1"] == "3 beaten, 27 to go"


def test_halfway_of_an_odd_count_rounds_up():
    odd = [{"room": "Hide", "game_id": 1, "name": "Words", "levels": [0, 1, 2]}]
    [half] = facts(_badge("Halfway Mark"), odd, {1: {(1, 0)}})
    assert half["text"] == "2 of 3 levels"
    assert half["players"]["1"] == "1 beaten, 1 to go"


def test_a_level_outside_the_catalog_does_not_count_towards_it():
    """A beaten count above the total would read as more than all of them."""
    [act] = facts(_badge("Activated"), COQUITLAM, {1: {(999, 0), (101, 0)}})
    assert act["players"]["1"] == "1 beaten, 59 to go"


def test_the_grand_tour_lists_rooms_in_the_site_s_order():
    [fact] = facts(_badge("The Grand Tour"), COQUITLAM, {})
    assert fact["text"] == "Hide, Portals, Mega Grid — 3 in all"


def test_completionist_counts_only_what_the_catalog_can_see():
    [fact] = facts(_badge("Completionist"), COQUITLAM, {})
    assert fact["text"].startswith("6 cooperative, plus the competitive ones")


def test_most_badges_have_no_location_facts():
    assert facts(_badge("Night Owl"), LANGLEY, {}) == []
    assert facts(_badge("Riddle 7.0"), [], {}) == []
