from pathlib import Path

import pytest

from app.scraper import ScrapeError, extract_player_blob, parse_html

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


def test_extract_raises_on_missing_blob():
    with pytest.raises(ScrapeError):
        extract_player_blob("<html><body>no data</body></html>")


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
