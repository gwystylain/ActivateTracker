import json
from pathlib import Path

import pytest

from app.scraper import (
    BadgeState,
    FetchError,
    ScrapeError,
    ScrapeResult,
    badges_from_trophy_progress,
    combine_badges,
    combine_results,
    extract_player_blob,
    parse_badges,
    parse_html,
    parse_room_html,
    room_slug,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gmebagholder_langley.html"
ROOM_FIXTURE = Path(__file__).parent / "fixtures" / "coquitlam_hoops.html"


def test_extracts_player_blob_from_real_html():
    html = FIXTURE.read_text(encoding="utf-8")
    blob = extract_player_blob(html)

    assert blob["locationId"] == "72"
    assert blob["locationName"] == "langley"

    pl = blob["playerLocation"]
    assert pl["playerName"] == "GMEbagholder"
    assert pl["totalScore"] == 172617
    assert pl["yearlyScore"] == 80349
    assert isinstance(pl["scores"], list)
    assert len(pl["scores"]) > 0
    # Spot-check the first known game/level pair from the response.
    first = pl["scores"][0]
    assert first == {"gameId": 1002, "levelId": 2, "highScore": 4018}


def test_parse_html_returns_scrape_result():
    html = FIXTURE.read_text(encoding="utf-8")
    r = parse_html(html, handle="gmebagholder", location_id=72, slug="langley")
    assert r.player_name == "GMEbagholder"
    assert r.total_score == 172617
    assert r.yearly_score == 80349
    assert r.location_id == 72
    assert r.location_slug == "langley"
    # Two distinct ranks off the same page: the profile's own (`player.rank`)
    # and this location's leaderboard position (`playerLocation.playerRank`).
    assert r.profile_rank == 3
    assert r.stars == 355
    assert r.coins == 145
    assert r.location_player_rank == 6
    assert r.yearly_rank == 2932
    assert r.standing == 287
    assert len(r.scores) > 30  # the real fixture has many
    # Levels beat is derived: one scores entry with a non-zero highScore per
    # level. The fixture's 40 entries are all non-zero.
    assert r.levels_beat == 40
    assert r.level_count == 470  # location.levelCount


def test_extract_raises_on_missing_blob():
    with pytest.raises(ScrapeError):
        extract_player_blob("<html><body>no data</body></html>")


def _r(handle, total, yearly=0, **kw):
    defaults = dict(
        handle=handle, location_id=72, location_slug="langley", player_name=handle,
        profile_rank=None, stars=None, coins=None, location_player_rank=None,
        yearly_rank=None, standing=None, total_score=total, yearly_score=yearly,
        scores=[],
    )
    defaults.update(kw)
    return ScrapeResult(**defaults)


def test_combine_results_sums_totals_and_takes_best_ranks():
    a = _r("stebb", total=1000, yearly=400, stars=10, coins=5,
           location_player_rank=20, yearly_rank=300, standing=100, profile_rank=4)
    b = _r("stevo", total=2000, yearly=800, stars=15, coins=8,
           location_player_rank=8,  yearly_rank=200, standing=50,  profile_rank=3)
    c = combine_results([a, b])

    assert c.total_score == 3000
    assert c.yearly_score == 1200
    assert c.stars == 25
    assert c.coins == 13
    # Ranks: lower is better → take min
    assert c.location_player_rank == 8
    assert c.yearly_rank == 200
    assert c.standing == 50
    # Two handles are two real profiles, each with its own queried rank — the
    # better one wins rather than anything being derived from the pair.
    assert c.profile_rank == 3
    # Combined handle is the comma-joined input
    assert c.handle == "stebb,stevo"
    # Neither input carried per-level scores, so there's nothing to merge.
    assert c.scores == []


def test_combine_results_passthrough_for_single():
    a = _r("solo", total=500)
    assert combine_results([a]) is a


def test_levels_beat_ignores_zero_scores():
    fake = (
        '"player":{"player":{"playerName":"x","rank":1},'
        '"location":{"levelCount":12},'
        '"playerLocation":{"locationId":9,"playerName":"x","totalScore":5,'
        '"yearlyScore":5,"scores":['
        '{"gameId":1,"levelId":0,"highScore":100},'
        '{"gameId":1,"levelId":1,"highScore":0},'
        '{"gameId":2,"levelId":0,"highScore":7}]},'
        '"locationId":"9","locationName":"x"}'
    )
    r = parse_html(fake, handle="x", location_id=9, slug="x")
    assert r.levels_beat == 2   # the highScore 0 entry doesn't count
    assert r.level_count == 12


def test_combine_results_unions_levels_beat_across_handles():
    # Both profiles beat game 1 level 0 — it counts once, not twice.
    a = _r("stebb", total=10, levels_beat=2, level_count=470, scores=[
        {"gameId": 1, "levelId": 0, "highScore": 50},
        {"gameId": 1, "levelId": 1, "highScore": 60},
    ])
    b = _r("stevo", total=20, levels_beat=2, level_count=470, scores=[
        {"gameId": 1, "levelId": 0, "highScore": 90},
        {"gameId": 2, "levelId": 0, "highScore": 30},
    ])
    c = combine_results([a, b])
    assert c.levels_beat == 3
    assert c.level_count == 470


def test_combine_results_merges_scores_taking_the_better_run():
    """The /games page reads per-level scores off the merged result, so a level
    either profile cleared must survive the combine at its better score."""
    a = _r("stebb", total=10, scores=[
        {"gameId": 1003, "levelId": 0, "highScore": 50},
        {"gameId": 1003, "levelId": 1, "highScore": 60},
    ])
    b = _r("stevo", total=20, scores=[
        {"gameId": 1003, "levelId": 0, "highScore": 90},   # better than stebb's
        {"gameId": 1004, "levelId": 0, "highScore": 30},
    ])
    c = combine_results([a, b])

    assert c.scores == [
        {"gameId": 1003, "levelId": 0, "highScore": 90},
        {"gameId": 1003, "levelId": 1, "highScore": 60},
        {"gameId": 1004, "levelId": 0, "highScore": 30},
    ]
    assert c.levels_beat == 3


def test_combine_results_keeps_zero_scores_out_of_levels_beat():
    a = _r("stebb", total=10, scores=[{"gameId": 1, "levelId": 0, "highScore": 0}])
    b = _r("stevo", total=20, scores=[{"gameId": 1, "levelId": 1, "highScore": 7}])
    c = combine_results([a, b])
    assert len(c.scores) == 2      # the zero entry is still carried
    assert c.levels_beat == 1      # but it isn't a level beaten


def test_combine_results_handles_partial_nones():
    a = _r("a", total=10, stars=None, location_player_rank=5)
    b = _r("b", total=20, stars=4,    location_player_rank=None)
    c = combine_results([a, b])
    assert c.stars == 4                  # only one had a value
    assert c.location_player_rank == 5   # only one had a value


def test_room_slug_is_the_lowercased_room_name():
    """The scores route keys on the room name, not the marketing slug — the
    marketing `mega-grid` 302s back to the location page."""
    assert room_slug("Hoops") == "hoops"
    assert room_slug("Mega Grid") == "mega%20grid"
    assert room_slug("  Mega Laser ") == "mega%20laser"


def test_parse_room_html_reads_gamemodes_and_top_scores():
    html = ROOM_FIXTURE.read_text(encoding="utf-8")
    r = parse_room_html(html, room_name="Hoops")

    assert r.room_id == 10
    assert r.room_name == "Hoops"
    assert [(g.game_id, g.name) for g in r.games] == [
        (1001, "Simon Says"),
        (1002, "Trivial"),
        (1003, "Barrage"),
        (1004, "15 Green"),
    ]
    # Every gamemode carries its own level ids, 0-based.
    assert all(g.levels == tuple(range(10)) for g in r.games)
    assert r.level_total == 40
    # roomIndex is the site's display order, not the id order.
    assert [g.order for g in r.games] == [3, 2, 0, 1]
    # roomScores is the location's top score per level.
    assert r.top_scores[(1001, 0)] == 2041
    assert r.top_scores[(1003, 5)] == 6795


def test_parse_room_html_raises_when_redirected_to_the_location_page():
    """An unknown room segment 302s to the location page instead of 404ing.
    That page parses fine but has no roomInfo — without this the catalog would
    silently record the room as having no gamemodes."""
    location_page = FIXTURE.read_text(encoding="utf-8")
    with pytest.raises(ScrapeError, match="roomInfo"):
        parse_room_html(location_page, room_name="Mega Grid")


def test_parse_html_carries_the_location_room_list():
    html = ROOM_FIXTURE.read_text(encoding="utf-8")
    r = parse_html(html, handle="gmebagholder", location_id=38, slug="coquitlam")
    # Present on every page for the location, which is what lets the catalog
    # walk the rooms without a separate request.
    assert r.rooms[0] == {"id": 10, "name": "Hoops"}
    assert {room["name"] for room in r.rooms} >= {"Hoops", "Mega Grid", "Scan"}
    assert r.level_count == 490


def test_brace_balance_handles_strings_and_escapes():
    # Anchor must match this constructed snippet.
    fake = (
        '...prelude..."player":{"player":{"playerName":"weird \\"quote\\""},'
        '"playerLocation":{"locationId":99,"playerName":"x","scores":[],'
        '"totalScore":0,"yearlyScore":0},'
        '"locationId":"99","locationName":"x"}...trailer'
    )
    blob = extract_player_blob(fake)
    assert blob["locationId"] == "99"
    assert blob["player"]["playerName"] == 'weird "quote"'


# ---------- badges ----------

BADGE_FIXTURE = Path(__file__).parent / "fixtures" / "badges_gmebagholder.json"


def _badge_payload():
    return json.loads(BADGE_FIXTURE.read_text(encoding="utf-8"))


def test_parses_every_badge_with_its_earned_state():
    states = parse_badges(_badge_payload())
    assert len(states) == 118
    assert sum(1 for s in states if s.earned) == 11

    early = next(s for s in states if s.name == "Early Bird")
    assert (early.earned, early.stars) == (True, 5)
    assert early.description == "Win a game before 11 AM"


def test_two_badges_share_a_name_so_the_id_is_the_key():
    """Untouchable 5.0 is Piperooni's and Wormholes', not one badge seen twice.
    Keying on the name would silently drop one of the 118."""
    states = parse_badges(_badge_payload())
    same_name = [s for s in states if s.name == "Untouchable 5.0"]

    assert len(same_name) == 2
    assert {s.badge_id for s in same_name} == {111, 125}
    assert len({s.description for s in same_name}) == 2
    assert len({s.badge_id for s in states}) == 118


def test_a_badge_with_no_target_is_not_treated_as_complete():
    """Four badges report a running count and a totalProgress of 0. Anything
    dividing by that denominator would blow up or read as finished."""
    states = parse_badges(_badge_payload())
    open_ended = [s for s in states if s.total_progress == 0]

    assert {s.name for s in open_ended} == {
        "Activated", "Halfway Mark", "Completionist", "The Grand Tour",
    }
    for s in open_ended:
        assert s.progress > 0 and not s.earned


def test_a_duplicate_id_collapses_rather_than_inflating_the_total():
    payload = _badge_payload()
    states = parse_badges([*payload, dict(payload[0], status=True)])
    assert len(states) == 118
    assert next(s for s in states if s.badge_id == payload[0]["id"]).earned


def test_badge_payload_that_is_not_a_list_is_an_error_not_an_empty_set():
    """An empty list would read as "this player has no badges" and wipe them."""
    with pytest.raises(ScrapeError):
        parse_badges({"badges": []})
    with pytest.raises(FetchError):
        parse_badges({"error": "Activate API returned 500"})


def test_trophy_progress_recovers_the_same_count_the_badge_api_reports():
    """Two unrelated sources, one answer: the score page states four fractions
    of a threshold, the badge API lists the badges. Both say 11 of 118."""
    html = ROOM_FIXTURE.read_text(encoding="utf-8")
    r = parse_html(html, handle="gmebagholder", location_id=38, slug="coquitlam")

    assert badges_from_trophy_progress(r.trophy_progress) == (11, 118)
    states = parse_badges(_badge_payload())
    assert (sum(1 for s in states if s.earned), len(states)) == (11, 118)


def test_a_page_without_trophy_progress_yields_no_count_rather_than_zero():
    """The langley capture predates the field. Zero would be a claim; None
    is the absence of one."""
    html = FIXTURE.read_text(encoding="utf-8")
    r = parse_html(html, handle="gmebagholder", location_id=72, slug="langley")

    assert r.trophy_progress is None
    assert badges_from_trophy_progress(r.trophy_progress) == (None, None)
    assert badges_from_trophy_progress({}) == (None, None)
    assert badges_from_trophy_progress({"bronze": "nonsense"}) == (None, None)


def test_the_count_is_read_off_the_finest_tier_still_in_progress():
    """progress is 2dp, so the error is ±0.005×threshold — reading platinum's
    118 could be a badge out, while bronze's 25 cannot."""
    tp = {
        "bronze": {"progress": 1.0, "requiredBadges": 25},
        "silver": {"progress": 0.62, "requiredBadges": 50},
        "gold": {"progress": 0.41, "requiredBadges": 75},
        "platinum": {"progress": 0.26, "requiredBadges": 118},
    }
    assert badges_from_trophy_progress(tp) == (31, 118)   # silver: 0.62 * 50


def test_every_tier_complete_means_every_badge():
    tp = {
        "bronze": {"progress": 1.0, "requiredBadges": 25},
        "platinum": {"progress": 1.0, "requiredBadges": 118},
    }
    assert badges_from_trophy_progress(tp) == (118, 118)


def test_combining_handles_unions_earned_and_keeps_the_best_progress():
    """Two of one person's accounts hold overlapping badge sets. Summing would
    invent a total neither account has; the union is what the person can show."""
    a = [
        BadgeState(1, "A", "a", earned=True, progress=1, total_progress=1, stars=5),
        BadgeState(2, "B", "b", earned=False, progress=4, total_progress=25, stars=5),
    ]
    b = [
        BadgeState(1, "A", "a", earned=False, progress=0, total_progress=1, stars=5),
        BadgeState(2, "B", "b", earned=False, progress=19, total_progress=25, stars=5),
    ]
    merged = {s.badge_id: s for s in combine_badges([a, b])}

    assert merged[1].earned is True
    assert (merged[2].earned, merged[2].progress) == (False, 19)
