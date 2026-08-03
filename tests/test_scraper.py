from pathlib import Path

import pytest

from app.scraper import (
    ScrapeError,
    ScrapeResult,
    combine_results,
    extract_player_blob,
    parse_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gmebagholder_langley.html"


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
    assert r.player_rank == 3
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
        player_rank=None, stars=None, coins=None, location_player_rank=None,
        yearly_rank=None, standing=None, total_score=total, yearly_score=yearly,
        scores=[],
    )
    defaults.update(kw)
    return ScrapeResult(**defaults)


def test_combine_results_sums_totals_and_takes_best_ranks():
    a = _r("stebb", total=1000, yearly=400, stars=10, coins=5,
           location_player_rank=20, yearly_rank=300, standing=100, player_rank=4)
    b = _r("stevo", total=2000, yearly=800, stars=15, coins=8,
           location_player_rank=8,  yearly_rank=200, standing=50,  player_rank=3)
    c = combine_results([a, b])

    assert c.total_score == 3000
    assert c.yearly_score == 1200
    assert c.stars == 25
    assert c.coins == 13
    # Ranks: lower is better → take min
    assert c.location_player_rank == 8
    assert c.yearly_rank == 200
    assert c.standing == 50
    assert c.player_rank == 3
    # Combined handle is the comma-joined input
    assert c.handle == "stebb,stevo"
    # Scores list is dropped on combine (not used for display)
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


def test_combine_results_handles_partial_nones():
    a = _r("a", total=10, stars=None, location_player_rank=5)
    b = _r("b", total=20, stars=4,    location_player_rank=None)
    c = combine_results([a, b])
    assert c.stars == 4                  # only one had a value
    assert c.location_player_rank == 5   # only one had a value


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
