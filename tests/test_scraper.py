import json
from pathlib import Path

import pytest

from datetime import datetime, timezone

from app.scraper import (
    BadgeState,
    FetchError,
    RateLimited,
    ScrapeError,
    ScrapeResult,
    badges_from_trophy_progress,
    combine_badges,
    combine_results,
    fetch,
    fetch_badges,
    fetch_location,
    fetch_player_badges,
    fetch_profile,
    fetch_room_top_scores,
    parse_badges,
    parse_games,
    parse_player_location,
    parse_rooms,
    parse_top_scores,
    retry_after_seconds,
)

# Live captures of the site's own API, all taken within a minute of each other
# and of BADGE_FIXTURE below — which is what lets one source check the other.
SITE = Path(__file__).parent / "fixtures" / "site"


def _site(name):
    return json.loads((SITE / name).read_text(encoding="utf-8"))


def _coquitlam():
    games, level_count = parse_games(_site("location_38_games.json"))
    return parse_player_location(
        _site("player_gmebagholder_location_38.json"),
        handle="gmebagholder",
        location_id=38,
        profile=_site("player_gmebagholder.json"),
        level_count=level_count,
    )


def test_parse_player_location_returns_scrape_result():
    r = _coquitlam()
    assert r.player_name == "GMEbagholder"
    assert r.location_id == 38
    assert r.total_score == 1189912
    assert r.yearly_score == 1189912
    # The per-account numbers come from /api/player/<handle>, not from the
    # location: profile rank, stars and coins.
    assert r.profile_rank == 5
    assert r.stars == 1740
    assert r.coins == 718
    # Two per-location rank-shaped numbers, as the page labels them: "RANK 47"
    # and "PLAYER POSITION 16".
    assert r.location_player_rank == 47
    assert r.standing == 16
    assert r.yearly_rank == 8
    assert r.scores[0] == {"gameId": 1001, "levelId": 0, "highScore": 1621}
    # The page's own "LEVELS BEAT 242/510".
    assert (r.levels_beat, r.level_count) == (242, 510)


def test_the_page_s_rank_is_the_total_score_in_steps_of_25000():
    """The page draws "RANK 47" with a bar reading 14,912/25,000 — both are
    totalScore split at 25,000, which the payload's playerRank agrees with."""
    r = _coquitlam()
    assert r.location_player_rank == r.total_score // 25000
    assert r.total_score % 25000 == 14912


def test_levels_beat_ignores_zero_scores():
    r = parse_player_location(
        {
            "locationId": 9,
            "totalScore": 5,
            "yearlyScore": 5,
            "scores": [
                {"gameId": 1, "levelId": 0, "highScore": 100},
                {"gameId": 1, "levelId": 1, "highScore": 0},
                {"gameId": 2, "levelId": 0, "highScore": 7},
            ],
        },
        handle="x",
        location_id=9,
        level_count=12,
    )
    assert r.levels_beat == 2   # the highScore 0 entry doesn't count
    assert r.level_count == 12


def test_a_payload_for_another_location_is_refused():
    with pytest.raises(ScrapeError, match="location"):
        parse_player_location(
            _site("player_gmebagholder_location_72.json"), handle="x", location_id=38
        )
    with pytest.raises(ScrapeError):
        parse_player_location([], handle="x", location_id=38)


def test_the_level_denominator_counts_competitive_games_but_the_catalog_does_not():
    """The page divides by every listed game's levels, and /games now lists the
    competitive ones with a single level each — so Coquitlam reads /510 where
    its co-op gamemodes hold 490. The snapshot keeps the page's number; the
    catalog keeps only gamemodes that can actually be beaten."""
    games, level_count = parse_games(_site("location_38_games.json"))
    assert level_count == 510
    assert len(games) == 49
    assert sum(len(g.levels) for g in games) == 490

    games72, level_count72 = parse_games(_site("location_72_games.json"))
    assert (level_count72, sum(len(g.levels) for g in games72)) == (530, 510)


def test_parse_games_reads_gamemodes_by_room():
    games, _ = parse_games(_site("location_38_games.json"))
    hoops = sorted((g for g in games if g.room_id == 10), key=lambda g: g.game_id)
    assert [(g.game_id, g.name) for g in hoops] == [
        (1001, "Simon Says"),
        (1002, "Trivial"),
        (1003, "Barrage"),
        (1004, "15 Green"),
    ]
    # Every gamemode carries its own level ids, 0-based.
    assert all(g.levels == tuple(range(10)) for g in hoops)
    # roomIndex is the site's display order, not the id order.
    assert [g.order for g in hoops] == [6, 2, 0, 1]
    assert all(g.room_id == g.game_id // 100 for g in games)
    # Hoops' competitive Barrage (1030) shares a name, not an id or a type.
    assert 1030 not in {g.game_id for g in games}


def test_a_location_with_no_games_claims_no_level_count():
    """An unknown location id answers [] with a 200. Zero would be a claim."""
    assert parse_games([]) == ((), None)
    with pytest.raises(ScrapeError):
        parse_games({"games": []})


def test_parse_rooms_keeps_the_site_s_order():
    rooms = parse_rooms(_site("location_38_rooms.json"))
    assert rooms[0] == {"id": 10, "name": "Hoops"}
    assert {r["name"] for r in rooms} >= {"Hoops", "Mega Grid", "Portals", "Scan"}
    with pytest.raises(ScrapeError):
        parse_rooms({"rooms": []})


def test_parse_top_scores_reads_the_location_s_best_per_level():
    top = parse_top_scores(_site("location_38_rooms_10_highscores.json"))
    assert len(top) == 40
    assert top[(1001, 0)] == 2059
    assert {g for g, _ in top} == {1001, 1002, 1003, 1004}   # co-op only
    with pytest.raises(ScrapeError):
        parse_top_scores({"message": "nope"})


def _r(handle, total, yearly=0, **kw):
    defaults = dict(
        handle=handle, location_id=72, player_name=handle,
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


# ---------- badges ----------

# The badge proxy's response, captured alongside SITE.
BADGE_FIXTURE = Path(__file__).parent / "fixtures" / "badges_gmebagholder.json"


def _badge_payload():
    return json.loads(BADGE_FIXTURE.read_text(encoding="utf-8"))


def test_parses_every_badge_with_its_earned_state():
    states = parse_badges(_badge_payload())
    assert len(states) == 114
    assert sum(1 for s in states if s.earned) == 33

    early = next(s for s in states if s.name == "Early Bird")
    assert (early.earned, early.stars) == (True, 5)
    assert early.description == "Win a game before 11 AM"


def test_the_site_s_badge_list_says_earned_and_nothing_more():
    """Same shape as the proxy's, minus progress and stars — which come through
    as None, "doesn't say", so they can never be written over the proxy's."""
    states = parse_badges(_site("player_gmebagholder_badges_38.json"))
    assert len(states) == 101
    assert sum(1 for s in states if s.earned) == 32
    assert all(s.progress is None and s.total_progress is None and s.stars is None
               for s in states)


def test_two_badges_share_a_name_so_the_id_is_the_key():
    """Untouchable 5.0 is Piperooni's and Wormholes', not one badge seen twice.
    Keying on the name would silently drop one of them."""
    states = parse_badges(_badge_payload())
    same_name = [s for s in states if s.name == "Untouchable 5.0"]

    assert len(same_name) == 2
    assert {s.badge_id for s in same_name} == {111, 125}
    assert len({s.description for s in same_name}) == 2
    assert len({s.badge_id for s in states}) == 114


def test_a_badge_with_no_target_is_not_treated_as_complete():
    """Four badges report a running count and a totalProgress of 0. Anything
    dividing by that denominator would blow up or read as finished."""
    states = parse_badges(_badge_payload())
    open_ended = [s for s in states if s.total_progress == 0]

    assert {s.name for s in open_ended} == {
        "Activated", "Halfway Mark", "Completionist", "The Grand Tour",
    }
    assert all(s.progress > 0 for s in open_ended)
    # Halfway Mark is earned by now; the other three, at the same 0 "total",
    # are not — so the denominator says nothing about being finished.
    assert {s.name for s in open_ended if s.earned} == {"Halfway Mark"}


def test_a_duplicate_id_collapses_rather_than_inflating_the_total():
    payload = _badge_payload()
    states = parse_badges([*payload, dict(payload[0], status=True)])
    assert len(states) == 114
    assert next(s for s in states if s.badge_id == payload[0]["id"]).earned


def test_badge_payload_that_is_not_a_list_is_an_error_not_an_empty_set():
    """An empty list would read as "this player has no badges" and wipe them."""
    with pytest.raises(ScrapeError):
        parse_badges({"badges": []})
    with pytest.raises(FetchError):
        parse_badges({"error": "Activate API returned 500"})


def test_the_site_and_the_proxy_agree_on_every_badge_they_share():
    """Two unrelated sources, one answer. The proxy lists the whole catalog;
    the site lists what each location offers. Where both list a badge they
    agree on whether it is earned, names and descriptions included."""
    proxy = {s.badge_id: s for s in parse_badges(_badge_payload())}
    for name in ("player_gmebagholder_badges_38.json", "player_gmebagholder_badges_72.json"):
        for s in parse_badges(_site(name)):
            p = proxy[s.badge_id]
            assert (s.name, s.description, s.earned) == (p.name, p.description, p.earned)


def test_badges_two_locations_share_read_the_same_at_both():
    """Badges transfer between locations, which is why player_badges has no
    location_id. The two lists differ only in what each building can host."""
    at = {
        loc: {s.badge_id: s for s in parse_badges(_site(f"player_gmebagholder_badges_{loc}.json"))}
        for loc in (38, 72)
    }
    shared = set(at[38]) & set(at[72])
    assert len(shared) == 99
    assert all(at[38][b].earned == at[72][b].earned for b in shared)
    # Untouchable 5.0 twice over: Wormholes needs Portals (Coquitlam), and
    # Piperooni needs Pipes (Langley).
    assert 125 in at[38] and 125 not in at[72]
    assert 111 in at[72] and 111 not in at[38]


def test_the_proxy_alone_covers_badges_no_tracked_location_offers():
    """Row By Row needs a Climb room, which neither location has — earned all
    the same, elsewhere. Only the proxy can say so."""
    site = combine_badges([
        parse_badges(_site("player_gmebagholder_badges_38.json")),
        parse_badges(_site("player_gmebagholder_badges_72.json")),
    ])
    assert (len(site), sum(s.earned for s in site)) == (103, 32)

    merged = combine_badges([site, parse_badges(_badge_payload())])
    assert (len(merged), sum(s.earned for s in merged)) == (114, 33)
    row_by_row = next(s for s in merged if s.name == "Row By Row")
    assert row_by_row.earned and row_by_row.badge_id not in {s.badge_id for s in site}


def test_trophy_progress_states_the_count_the_badge_list_enumerates():
    """completedBadges is exact, and platinum's threshold is how many badges
    that location offers — the length of its /badges list."""
    r = _coquitlam()
    assert badges_from_trophy_progress(r.trophy_progress) == (32, 101)

    listed = parse_badges(_site("player_gmebagholder_badges_38.json"))
    assert (sum(1 for s in listed if s.earned), len(listed)) == (32, 101)


def test_a_payload_without_trophy_progress_yields_no_count_rather_than_zero():
    """Zero would be a claim; None is the absence of one."""
    r = parse_player_location(
        {"locationId": 72, "totalScore": 1, "yearlyScore": 1, "scores": []},
        handle="x", location_id=72,
    )
    assert r.trophy_progress is None
    assert badges_from_trophy_progress(r.trophy_progress) == (None, None)
    assert badges_from_trophy_progress({}) == (None, None)
    assert badges_from_trophy_progress({"bronze": "nonsense"}) == (None, None)


def test_without_a_stated_count_it_is_read_off_the_finest_tier_still_in_progress():
    """The older shape, fractions only. progress is 2dp, so the error is
    ±0.005×threshold — reading platinum's 118 could be a badge out, while
    bronze's 25 cannot."""
    tp = {
        "bronze": {"progress": 1.0, "requiredBadges": 25},
        "silver": {"progress": 0.62, "requiredBadges": 50},
        "gold": {"progress": 0.41, "requiredBadges": 75},
        "platinum": {"progress": 0.26, "requiredBadges": 118},
    }
    assert badges_from_trophy_progress(tp) == (31, 118)   # silver: 0.62 * 50


def test_a_stated_count_beats_any_fraction():
    tp = {
        "silver": {"completedBadges": 32, "requiredBadges": 50, "progress": 0.64},
        "platinum": {"completedBadges": 32, "requiredBadges": 101, "progress": 0.31},
    }
    # 0.31 × 101 would round to 31; the payload says 32 outright.
    assert badges_from_trophy_progress(tp) == (32, 101)


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


def test_a_source_that_says_nothing_never_beats_one_that_does():
    site = [BadgeState(2, "B", "b", earned=True)]
    proxy = [BadgeState(2, "B", "b", earned=False, progress=19, total_progress=25, stars=5)]
    for lists in ([site, proxy], [proxy, site]):
        (s,) = combine_badges(lists)
        assert (s.earned, s.progress, s.total_progress, s.stars) == (True, 19, 25, 5)


# ---------- the site's endpoints ----------

class _Resp:
    def __init__(self, status_code, *, headers=None, payload=None, raise_on_json=False):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        self._raise = raise_on_json

    def json(self):
        if self._raise:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class _OneShotSession:
    """Enough of AsyncSession to answer every request with one response."""

    def __init__(self, resp):
        self._resp = resp
        self.urls = []
        self.kwargs = []

    async def get(self, url, **kw):
        self.urls.append(url)
        self.kwargs.append(kw)
        return self._resp


class _RoutedSession:
    """Answers by URL suffix, from the captures."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    async def get(self, url, **kw):
        self.urls.append(url)
        for suffix, name in self.routes.items():
            if url.endswith(suffix):
                return _Resp(200, payload=_site(name))
        return _Resp(404, raise_on_json=True)


async def test_every_site_request_is_impersonated_json_on_playactivate():
    session = _RoutedSession({
        "/api/player/gmebagholder": "player_gmebagholder.json",
        "/api/player/gmebagholder/location/38": "player_gmebagholder_location_38.json",
        "/api/player/gmebagholder/badges/38": "player_gmebagholder_badges_38.json",
        "/api/location/38/rooms": "location_38_rooms.json",
        "/api/location/38/games": "location_38_games.json",
        "/api/location/38/rooms/10/highscores": "location_38_rooms_10_highscores.json",
    })

    profile = await fetch_profile("gmebagholder", session=session, timeout=1.0)
    info = await fetch_location(38, session=session, timeout=1.0)
    r = await fetch(
        "gmebagholder", 38, session=session, timeout=1.0,
        profile=profile, level_count=info.level_count,
    )
    badges = await fetch_player_badges("gmebagholder", 38, session=session, timeout=1.0)
    top = await fetch_room_top_scores(38, 10, session=session, timeout=1.0)

    assert session.urls == [
        "https://playactivate.com/api/player/gmebagholder",
        "https://playactivate.com/api/location/38/rooms",
        "https://playactivate.com/api/location/38/games",
        "https://playactivate.com/api/player/gmebagholder/location/38",
        "https://playactivate.com/api/player/gmebagholder/badges/38",
        "https://playactivate.com/api/location/38/rooms/10/highscores",
    ]
    assert (r.coins, r.levels_beat, r.level_count) == (718, 242, 510)
    assert (info.coop_levels, len(info.rooms), len(info.games_in(10))) == (490, 10, 4)
    assert len(badges) == 101 and len(top) == 40


async def test_a_handle_is_one_path_segment_however_it_was_typed():
    session = _OneShotSession(_Resp(200, payload={}))
    await fetch_profile("a/b c", session=session, timeout=1.0)
    assert session.urls == ["https://playactivate.com/api/player/a%2Fb%20c"]


async def test_an_unknown_handle_is_a_fetch_error_not_an_empty_result():
    """The site answers a handle it doesn't know with its 404 HTML page."""
    session = _OneShotSession(_Resp(404, raise_on_json=True))
    for call in (
        fetch_profile("nosuchhandle", session=session, timeout=1.0),
        fetch("nosuchhandle", 38, session=session, timeout=1.0),
        fetch_player_badges("nosuchhandle", 38, session=session, timeout=1.0),
    ):
        with pytest.raises(FetchError):
            await call


async def test_an_empty_badge_list_from_the_site_is_not_an_answer():
    """An unknown location id answers [] with a 200. Taken at its word, that is
    "holds nothing", and retiring against it would delete every row."""
    session = _OneShotSession(_Resp(200, payload=[]))
    with pytest.raises(ScrapeError, match="empty"):
        await fetch_player_badges("gmebagholder", 9999, session=session, timeout=1.0)


async def test_an_html_page_served_with_a_200_is_a_scrape_error():
    """A Cloudflare challenge, say. Not JSON is not data."""
    session = _OneShotSession(_Resp(200, raise_on_json=True))
    with pytest.raises(ScrapeError, match="not JSON"):
        await fetch_profile("gmebagholder", session=session, timeout=1.0)


# ---------- rate limiting ----------

async def test_a_429_is_a_rate_limit_not_a_dead_handle():
    """The badge proxy answers a 429 in milliseconds without touching its
    upstream, and the same handle works once the window rolls. Treating it like
    a 404 is what left a player's badges ten days stale."""
    session = _OneShotSession(_Resp(429, headers={"Retry-After": "45"}))

    with pytest.raises(RateLimited) as exc:
        await fetch_badges("ikeaboy", session=session, timeout=1.0)

    assert exc.value.retry_after == 45.0
    assert isinstance(exc.value, FetchError)   # still a fetch failure


async def test_a_429_without_a_retry_after_leaves_the_wait_to_the_caller():
    session = _OneShotSession(_Resp(429))

    with pytest.raises(RateLimited) as exc:
        await fetch_badges("ikeaboy", session=session, timeout=1.0)

    assert exc.value.retry_after is None


async def test_other_http_failures_are_not_rate_limits():
    """A 500 means stop asking; only a 429 means ask again later."""
    session = _OneShotSession(_Resp(500))

    with pytest.raises(FetchError) as exc:
        await fetch_badges("ikeaboy", session=session, timeout=1.0)

    assert not isinstance(exc.value, RateLimited)


def test_retry_after_reads_both_forms_the_rfc_allows():
    now = datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc)
    assert retry_after_seconds("30") == 30.0
    assert retry_after_seconds("Fri, 21 Aug 2026 11:01:00 GMT", now=now) == 60.0


def test_a_retry_after_already_past_means_go_now_not_a_negative_sleep():
    now = datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc)
    assert retry_after_seconds("Fri, 21 Aug 2026 10:59:00 GMT", now=now) == 0.0
    assert retry_after_seconds("-5") == 0.0


def test_an_unreadable_retry_after_is_no_answer_rather_than_a_made_up_one():
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("") is None
    assert retry_after_seconds("soon") is None



# ---------- the badge proxy's shape ----------

async def test_the_badge_url_is_the_base_and_the_handle():
    """It moved from api.ryflix.ca/api/badges/activate-sync/<handle> to
    activate-scores.ca/api/activate/badges/<handle> - one path segment shorter,
    and the old host now answers 503."""
    session = _OneShotSession(_Resp(200, payload=[]))
    await fetch_badges("gmebagholder", session=session, timeout=1.0)

    assert session.urls == [
        "https://www.activate-scores.ca/api/activate/badges/gmebagholder"
    ]
    # Without this the API answers 403 {"message": "Forbidden: invalid origin"}
    # however good the TLS fingerprint is - it is a header check, not curl_cffi's
    # department.
    assert session.kwargs[0]["headers"] == {
        "Origin": "https://www.activate-scores.ca"
    }


async def test_the_origin_follows_a_repointed_base():
    """Derived, never configured separately: a stale origin left behind by a
    repointed base would 403 every request."""
    session = _OneShotSession(_Resp(200, payload=[]))
    await fetch_badges(
        "gmebagholder", session=session, base="https://mirror.example/api/b", timeout=1.0
    )

    assert session.urls == ["https://mirror.example/api/b/gmebagholder"]
    assert session.kwargs[0]["headers"] == {"Origin": "https://mirror.example"}


async def test_a_handle_the_api_rejects_is_a_fetch_error_not_an_empty_badge_set():
    """A bad handle now comes back as HTTP 500 with {"message": ...} rather than
    a 200 carrying {"error": ...}. Either way it must not read as "no badges"."""
    session = _OneShotSession(_Resp(500, payload={"message": "(Activate API) Internal server error"}))

    with pytest.raises(FetchError):
        await fetch_badges("nosuchhandle", session=session, timeout=1.0)

    with pytest.raises(FetchError):
        parse_badges({"message": "(Activate API) Internal server error"})
