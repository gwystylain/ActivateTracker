# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Local dev (Windows; use the venv's Python explicitly because system Python lacks the deps):

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q tests/test_streak.py::test_discount_tiers
ACTIVATETRACKER_DB=data/dev.db .venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Generate config secrets:

```bash
.venv/Scripts/python.exe -m app.tools.hashpw           # bcrypt hash for admin password
python -c "import secrets; print(secrets.token_hex(32))"   # session.secret_key
```

CI (`.github/workflows/build.yml`) runs pytest, then on push to `main` builds and pushes
`ghcr.io/gwystylain/activatetracker:latest`. There is no separate lint step; no Ruff/mypy config.

## Architecture

### Why curl_cffi instead of httpx for outbound polling
playactivate.com is Cloudflare-fronted and TLS-fingerprints clients. httpx with browser-mimicking
headers gets `403 Forbidden`; only TLS-impersonating clients work. The scraper hard-codes
`impersonate="chrome124"`. httpx is still in the deps for FastAPI's TestClient. Don't swap
`AsyncSession` back to httpx without re-verifying live fetch.

### Visit detection from score deltas (no visit API)
playactivate.com exposes scores but not visit timestamps. The poller infers visits by comparing
`total_score` of each new snapshot against the most recent prior snapshot for the same
`(player_id, location_id)`. A strictly-greater value inserts one row in `visits` dated
`streak.activity_day(polled_at)` — the day *before* the poll, because playactivate's scores
refresh ~1 day late: play on May 14 first shows up in the May 15 poll. That helper is the single
home of the rule; the chart's x-axis uses it too, so a point and its visit can't disagree.
Multi-location increases on the same calendar day are deduped to one visit by `streak.summarize`,
which is what Activate's per-day visit counting actually does. `streak.visits_in_window` counts
unique visit days inside the trailing 30 days; `streak.discount_for` maps that count to a discount
(1 visit = 10%, +5% per extra visit, capped at 25% for 4+).

### Multi-handle players
`players.handle` is a comma-separated list of Activate profile slugs (canonicalised to
`"a, b"` by `poller.format_handles`). One row in `player_locations` triggers N HTTP fetches
per refresh (one per handle). `scraper.combine_results` sums totals/yearly/stars/coins and
takes the best (min) of any leaderboard rank, then `persist_snapshot` writes ONE snapshot per
`(player, location)` holding the combined values. Visit detection runs on combined totals.

### Chart-data semantics
Every point carries *both* plottable metrics — `top_score` (the player's highest single-location
score) and `total_score` (the sum across locations) — because the chart's Best location / All
locations toggle switches between them client-side with no refetch. `/api/chart-data` emits a day
when *either* metric moved (the first observation always counts); days where neither moved drop
out, which is what keeps the chart from growing a dot per poll on uneventful days. Each point also
carries a `locations: {slug: score}` breakdown that forward-fills per-location values across days
where only some locations were polled.

A point's `date` is the day of *play*, `streak.activity_day(polled_at)`, not the `polled_at` day
it was grouped from — the same shift the visit rows get. Reading the poll date straight off the
snapshot is what put every dot a day late while "days since last visit" stayed right: the latter
reads `visits.visit_date`, which had the lag backed out all along.

Deciding which dates get a visible dot is the front-end's job (`app/static/app.js`), because it
depends on the selected metric: `changeDates()` walks the emitted points and marks only those where
the *chosen* metric changed. So in Best location mode, a day where only a non-leading location
gained is a flat, dotless stretch — the point exists in the payload but is drawn at radius 0. The
y values are forward-filled to every x label for tooltip continuity regardless. The selected metric
persists in `localStorage` under `atrk.chartMetric`; both the read and the write are wrapped in
try/catch because blocked storage throws on access and would otherwise take the whole chart down.
Switching metrics also has to dismiss any open tooltip (`chart.tooltip.setActiveElements([], ...)`)
— Chart.js otherwise keeps rendering the previous metric's number until the pointer moves.

### CSRF via body-replay middleware
`security.CsrfMiddleware._peek_form` reads the request body once, parses it manually, then
replaces `request._receive` with a closure that hands the same bytes back. Necessary because
`Starlette.Request.form()` consumes the receive channel — naively reading it in middleware
would leave the route handler with empty form data. Only `application/x-www-form-urlencoded`
is checked here; admin posts are all that flavour.

### Session cookie Secure flag is dynamic
`auth_routes.login_submit` keys the cookie's `Secure` attribute off `request.url.scheme`.
nginx-proxy-manager terminates TLS and `--proxy-headers` rewrites scheme to `https`, so the
cookie is Secure in prod but not in plain-HTTP local dev (cookies wouldn't otherwise be sent).

### TrueNAS deployment quirks
Container image bakes in UID 10001; TrueNAS apps run everything as UID 568. The compose file
in production overrides with `user: "568:568"` and the host bind mounts at
`/mnt/HDDs/Applications/ActivateTracker/{config.yaml,data}` are owned 568:568. Bind-mounting
a single `config.yaml` requires the file to exist before `up` or Docker creates a directory
at that path silently.

### Streak baseline reset rule
`update_player` resets `initial_streak_set_at` to today only when the streak value itself
changes. Editing display name / locations leaves the 30-day clock alone. Any new
`initial_streak > 0` writes today's date as the baseline; `0` clears it.

### Test fixtures are real captured responses
`tests/fixtures/gmebagholder_langley.html` is a 378 KB capture of a live playactivate *location*
page; `tests/fixtures/coquitlam_hoops.html` is a 361 KB capture of a *room* page.
`extract_player_blob` anchors on the unique `"player":{"player":{"playerName"` substring and
walks braces (handling escaped quotes in strings); `_extract_json_value` does the same by key name
for the room page's `roomInfo` / `roomGames` / `roomScores`, generalised to `[...]` values. If the
site's hydration shape changes, these fixtures and the anchor regex are the canary.

`tests/fixtures/badges_gmebagholder.json` is a live capture of the badge API's 118-row
response. The langley HTML capture is old enough to predate `trophyProgress` while the
coquitlam one has it — that pair differs in *both* capture date and page type, so it
cannot settle where the field lives. A live poll settled it: both a location page and a
room page return it, so it rides every poll. Anything relying on that should still tolerate
its absence, which is what the langley fixture is now pinning.

### Schema is bootstrapped, not migrated
`db.init_schema` runs `CREATE TABLE IF NOT EXISTS ...` on every startup. There is no migration
framework. Adding a column to an existing table means adding it to `SCHEMA` *and* to `db._migrate`
as a `PRAGMA table_info`-guarded `ALTER TABLE` — `CREATE TABLE IF NOT EXISTS` is a no-op on a
deployment whose table already exists, so new columns only reach it through `_migrate`. Backfilled
columns are NULL on old rows, so readers must tolerate None. Adding a whole new *table* needs only
the `SCHEMA` entry (that's why `location_games` / `location_top_scores` / `location_catalog` have
no `_migrate` clause). Existing deployments' SQLite at `/data/tracker.db` survives container
rebuilds via the compose volume.

### Three rank-shaped numbers; the dashboard shows the one the page names
The page carries three, and they are *different numbers on the same page* — mixing them up is the
easiest mistake here, so all three are parsed and named after what they are:

- `playerLocation.standing` → `ScrapeResult.standing` → `score_snapshots.leaderboard_position`.
  The page states it outright next to the score: `Your Leaderboard Position: #138`. **This is what
  the dashboard's Leaderboard position column shows.** Per location — one live pull read 138 at
  coquitlam and 321 at langley for the same handle.
- `playerLocation.playerRank` → `ScrapeResult.location_player_rank` → `score_snapshots.player_rank`.
  The number in the site's own "We're Impressed" header, inside the `Rank_10-19.png` badge (19 and 8
  for those same two pulls). Still persisted because it has history, but not displayed — the
  dashboard used to show this in a column labelled Rank, which is why the column read #19 while the
  page said #138.
- `player.rank` → `ScrapeResult.profile_rank`. A property of the profile, not of any location:
  every location's page returns the same value (both pages above said 4). Parsed, not persisted.

None of these is computed from the others, and none can be. The headline row shows the best
(lowest) `leaderboard_position` across the player's locations, the same rule as the headline
discount and levels beat — a pick among values the site reported, not a blend. NULL means "not
observed" (a snapshot predating the column) and renders as an em dash; there is no fallback to a
different field. `combine_results` takes the best `standing` across a multi-handle player's
profiles, which likewise picks between two queried numbers.

The hydration blob has no "levels beat" field: `scraper.parse_html` derives it
as the count of `playerLocation.scores` entries with a non-zero `highScore`, alongside
`location.levelCount` as the denominator. That derivation is not a guess: the page also *renders*
the answer as `Levels Beat: 106/490` in presentational markup, and the derived pair matches it
exactly (checked live at coquitlam 106/490 and langley 49/510). The JSON blob is the more stable
anchor than the Tailwind-classed `<p>`, so we derive rather than scrape the rendered string — but
that string is the ground truth to re-check against if the number ever looks wrong. Note the
committed langley fixture is an old capture (40/470), so fixture numbers lag live ones. Across
multiple handles `combine_results` merges the `scores` lists by `(gameId, levelId)` keeping the
better `highScore` and counts *that*, rather than summing — a level both profiles cleared would
otherwise count twice. On the dashboard the headline row shows the highest levels-beat across the
player's locations, with the per-location values (and per-location ranks) in the expandable rows.

### /games: per-level breakdown and Point Farmer
Terminology differs from the site's. What the UI calls a **Game** is the site's *room* (Hoops);
what it calls a **gamemode** is the site's *game* (`roomGames[].id`, e.g. 1003 "Barrage"); a
**level** is `levelId`, 0-based in the data and displayed +1. `gameId // 100 == roomId`.

**Per-player level data costs no extra requests.** `playerLocation.scores` on the location page
already covers *every room at the location*, and `persist_snapshot` already writes it verbatim to
`score_snapshots.raw_scores_json`. `/api/game-data` reads the newest snapshot per
`(player, location)` and emits only levels with `highScore > 0`; everything absent renders as
"No score". Historic snapshots are ignored — Activate banks only the best-ever run, so the current
row is the whole truth.

What *does* need fetching is the catalog (which gamemodes and levels exist) and each location's
top scores, which live only on room pages — one request per room. `app/catalog.py` owns this:
`needs_refresh` re-walks a location when it has never been catalogued, when a player's score there
rose in this poll (the deliberate throttle: no score change, no room requests), or when the stored
`catalog_levels` no longer matches `location.levelCount`. `poller.poll_all` dedupes to **one walk
per location per poll**, not one per player, and a catalog failure never fails the score poll.

That throttle has one blind spot by design: `roomScores` is the whole venue's top score, set by
people we don't track, so a location where none of *our* players moved never gets its top scores
re-read. `poll_all(..., force_catalog=True)` overrides it, exposed as the admin page's
**Refresh + rebuild catalog** button (`POST /admin/refresh-catalog`) alongside the ordinary
throttled **Refresh all now**.

### The room-page URL segment is the room name, not the marketing slug
`scraper.room_slug` lowercases and percent-encodes `location.rooms[].name`: `Hoops` → `hoops`,
`Mega Grid` → `mega%20grid`. The marketing slugs under `/rooms` are a different id space and the
scores route rejects them — `mega-grid` **302s to the location page**, which parses fine and
carries no `roomInfo`. `parse_room_html` raises `ScrapeError` on a missing `roomInfo` precisely so
that soft failure can't quietly record a room as having no gamemodes.

### The catalog validates itself against levelCount
Summing `len(levels)` over a location's rooms equals `location.levelCount` exactly (verified live:
coquitlam 490, langley 510). `catalog.refresh_location` recomputes that sum from the table after
each walk — so a partial refresh is recorded honestly — and logs a warning on a mismatch.
A short count also makes `needs_refresh` retry on the next poll.

The per-room gamemode lists were identical at both locations probed, but `location_games` is keyed
per location anyway so one location's layout can never corrupt another's.

### Master document data is static reference, keyed by room
The site publishes neither a description for a gamemode nor how many players it plays best with,
so `app/master_document.py` carries both, transcribed from the community *Activate Games Master
Document*. **Keyed by room name first**, because names repeat across rooms and need not be the
same game — Mega Laser's Defuse wants 8 rounds where Trench's wants "enough targets". `lookup`
falls back to a name-only index built at import, merged **one field at a time**: a field two rooms
disagree about is dropped rather than answered with the wrong room's value. Today that is all of
`Defuse` (different rules) and only the *count* fields of `Zap` (3 at Mega Laser, unrecorded at
Trench) — Zap's rules are identical, so those still resolve.

Only **cooperative** gamemodes are listed, because those are what `roomGames` exposes — the
competitive games have no levels and no leaderboard. Where a room runs a competitive game under
the same name (Hoops' Barrage, Hide's Numbers), the co-op rules are the ones written down.
The document covers rooms Activate has built anywhere, so it is a superset of any one location;
conversely a gamemode it hasn't caught up with gets nulls and simply renders with no tooltip and
a dash for its player count. Both directions are expected and neither is an error.

`optimal_players` is the document's "Optimal # of players" column, and it is **not populated for
all 76** — 12 cells are blank and Grip's Firewall holds a `?`, so 13 come through as None and the
page shows a dash. The document's `**` marker ("no clear consensus") survives as
`optimal_disputed`, rendered as a starred, muted number: still shown, because a soft answer beats
none, but visibly softer than the other 53. The filter treats a disputed 5 as a 5 — it is the same
answer, just less settled.

`public._described` hangs the data on each game in `/api/game-data` rather than storing it with the
catalog — it is keyed by name, not something the site told us, so it has no business in a table
that mirrors upstream. Front-end: `games.js:modeName` marks up any *described* name with
`.mode-name` (the dotted underline is therefore the promise that there is something to read), and
one shared `position: fixed` bubble on `<body>` is refilled and moved. Fixed and body-level because
the cards live inside `.table-wrap { overflow-x: auto }`, which would clip an in-cell popover, and
because every row is destroyed on each render — hence delegated listeners and a `hideTip()` at the
top of `render`/`renderLevels`. The name is a tab stop **only** in Point Farmer and Never scored;
in the level breakdown the row is already `role="button"` and gets the `aria-describedby` itself,
since nesting a second tab stop inside it would misdescribe the structure. The gamemode `<select>`
uses a plain `title` instead: an open dropdown's options are drawn by the OS and nothing in the
page can position against them.

The **Optimal players** column appears in all three cards, and its tick-box filter lives in
`visibleGames()` so it narrows all three at once — the same reach as the Game and Gamemode selects
(the older "Only gamemodes with a no score" box is applied inside `renderLevels` and so affects
only the level breakdown). Ticking nothing constrains nothing, the way a facet filter normally
behaves. The choices are hard-coded rather than derived from the payload, so the boxes don't
shuffle when a location happens to catalogue no 4-player gamemode, and **Not recorded** is one of
them: without it, ticking any box would hide the 13 unrecorded gamemodes with no way back. In the
level breakdown the expanded level rows leave the cell empty — the count belongs to the gamemode,
not to each of its levels.

### Badges: counted by the score page, enumerated by a third party
The score pages carry no badge list — the site shows badges only on the in-store
score-checker iPad, and emails them per session. Two different sources fill that in,
and the app keeps them apart on purpose.

**The count** comes free from the page we already fetch. `playerLocation.trophyProgress`
states four tier fractions (`{tier: {progress, requiredBadges}}`) rather than a number;
`scraper.badges_from_trophy_progress` turns them into `(earned, possible)` and
`persist_snapshot` writes them to `score_snapshots.badges_earned` / `badges_possible`.
`progress` is 2dp, so a tier recovers the count only to ±0.005×threshold — hence reading
the *smallest* threshold still under 1.0, exact up to 75 badges and ±1 only past gold.
`platinum.requiredBadges` is the number of badges attainable **at that location** (badges
needing a room it lacks are excluded), which is why the trophy helper drops any tier
threshold above `possible`: a 40-badge location can never reach silver's 50, so offering
"24 to silver" would be an errand with no end. The committed langley fixture predates the
field entirely and parses to `(None, None)` — readers must tolerate that, and None is the
absence of a claim, not zero.

**Which badges** comes from `api.ryflix.ca/api/badges/activate-sync/<handle>`, a
community-run proxy in front of an official Activate badge API (a bad handle returns
`{"error": "Activate API returned 500"}`, naming its upstream). Public, unauthenticated,
keyed on the handle alone. It returns every badge applicable to the player — earned or
not, with name, description, star value and **partial progress** — so there is no separate
catalog fetch and `badges` is a mirror of upstream, refreshed on every poll.

Because it is somebody's personal server it is `config.badges.api_base`, and `poll_all`
takes `badge_cfg` **opt-in**: without it the poll is scores and catalog only, so no caller
reaches a third party by omission. A badge failure increments `badges_errors` and never
fails the score poll, the same treatment the catalog gets. If the proxy disappears the
dashboard's count survives on `trophyProgress` and only the per-badge detail goes stale.

Four traps, all of them load-bearing:

- **`badge_id` is the key, never `name`.** The API returns 118 badges under 117 names:
  `Untouchable 5.0` is id 111 (Piperooni) *and* id 125 (Wormholes). The community master
  document has the same collision.
- **The community badge trackers use their own id space.** Joining the sync response to
  the ryflix page's embedded `BADGES` array by id mismatches 108 of 118 rows, which is why
  their own page matches by normalised name. Nothing of theirs may be joined by id.
- **`total_progress` is 0 for `Activated`, `Halfway Mark`, `Completionist` and
  `The Grand Tour`**, where `progress` is a bare running count. Never divide by it; the
  front end renders those as a plain number and Closest-to-earning skips them, since a
  fraction needs a denominator.
- **`badges.stars` is the badge's own value** (100 of 2000 earned for one live profile),
  unrelated to `score_snapshots.stars` (924 for the same profile). Different numbers.

`player_badges` has no `location_id`: badges transfer between locations where scores and
rank do not, so the dashboard shows one count on the player row and leaves the
per-location rows blank. `earned_on` is stamped only on an observed false→true transition
and dated `streak.activity_day` — the same one-day backdate the visit rows get, so a badge
and the visit from the same session can't disagree. On a player's *first* poll there is no
transition to observe, so everything already earned is left NULL rather than backdated to
today, which would be a fabricated date. Multi-handle players go through
`scraper.combine_badges`: earned is OR'd and progress maxed, never summed — two of one
person's accounts hold overlapping sets, so a sum is a total neither account has.

`/badges` shows the enumerated count and the page's own tally side by side and says so
when they disagree, rather than picking a winner. Note "badge" already meant the
`Rank_10-19.png` header image in `scraper.py`/`db.py` and `.rec-badge` is the records
crown in `app.css`; the achievement sense is the newcomer.

### Badge reference data is keyed by name *and* description
Activate publishes a badge's name, description and star value and nothing else — no
room, no difficulty, no way to do it. `app/badge_reference.py` carries that, merged from
the community *Activate Games Master Document* (room, level, tips, watch-outs, and the
Easter Egg / Riddle hints and answers) and the badge tracker at activate.ryflix.ca
(difficulty, optimal players, overlapping badges, notes). Regenerate with
`python -m app.tools.gen_badge_reference "<doc>.md" <badges>.html > app/badge_reference.py`;
neither input is committed, the generated module is — same arrangement as
`master_document.py`, which it deliberately mirrors down to the `lookup` contract of
"every field always present".

**Keyed on the composite `norm(name)|norm(description)`**, because the name alone is
ambiguous and the ambiguity is load-bearing: `Untouchable 5.0` is two badges in two
rooms — Piperooni in Pipes, Wormholes in Portals — which at the tracked pair of
locations means one is Langley-only and the other Coquitlam-only. `lookup` falls back to
a name-only index merged one field at a time, so a field the two disagree about is
dropped rather than answered with the other badge's value. Normalising is not optional:
13 names differ in case or spacing between sources (`Activ8`/`ACTIV8`,
`10 for 10`/`10 For 10`), and 117 of 118 live badges only match once normalised.
`Mascot` matches nothing and renders with no detail — expected, not an error.

Where the sources disagree about a room the document wins: it was right about both
`Untouchable 5.0` rooms (checked against `location_games`, which the other source got
wrong for both) and it is more complete about rooms running the same game
(`Mega Laser or Trench` vs just Mega Laser). Two conflicts stay unresolved because
neither game runs at a tracked location — Steady Stream's Photon Rush and Recollection's
Memory — and the document is taken on both.

Two badges are graded by neither source — `Photobomb` and `Mascot`, both of which the
ryflix catalog omits — so their `difficulty` is this repo's estimate, declared by
`difficulty_estimated` with the reasoning in `difficulty_note`. Both are graded Easy:
neither asks for any play skill, and of the 20 graded 5-star badges 18 are Easy and none
is Hard or above. `gen_badge_reference.ESTIMATES` only ever *fills a hole* — an estimate
that finds a sourced grade already there is dropped with a warning to stderr, so a
document catching up retires it rather than being argued with. The page shows an
estimated grade muted and starred, the same treatment `/games` gives a player count the
document records without consensus: a soft answer beats a hole in the column, but it must
not read as something a document said. The filter and the sort treat an estimated Easy as
an Easy — same answer, less settled.

`hint` and `giveaway` are the Easter Egg and Riddle answers. The source document hides
them as white-on-white text because each can only be solved once; this page shows them
outright alongside the other detail, so expanding a badge is enough to spend that. That
is a deliberate call — the page exists to surface everything known about a badge — but
it is the reason the detail is behind a click at all rather than sitting in the grid.

**The "Where" line is a weak negative and must stay worded as one.** It joins a badge's
rooms against `location_games.room_name`, which is only each location's *scoring* rooms:
the photo room is in no location's list, so `Photobomb` looks unobtainable everywhere.
Badges also transfer between locations, so `Row By Row` (Climb, at neither tracked
location) is earned all the same. Both cases are live in the current data, which is why
the page says "no scoring room for it at your tracked locations" rather than "you can't
get this", never makes the negative claim about a badge somebody has already earned, and
never hides one behind the checkbox on that basis.

### roomScores is per-location and sparse
`roomScores` is the best score anyone *at that location* has posted, not a global best: coquitlam
and langley disagree on every shared Hoops entry. It is also sparse — langley returns 38 rows
where coquitlam returns 40 — and a missing entry means nobody at that location has ever scored
that level. The `/games` table shows that as a dash. Point Farmer can't, since a level nobody has
touched would then rank *below* one people have already played, which is backwards; `games.js`
substitutes the median top score for the same level index across the location (scores track the
level number closely, level 1 ≈ 2k up to level 10 ≈ 10k).

The **Never scored** card lists those gaps directly, one row per gamemode with the missing level
numbers. A stored `top_score` of 0 counts as never scored alongside a missing row — the site can
emit either. It reads only `top_scores`, so the player checkboxes don't apply (it's the venue's
board, not ours); the game/gamemode selects do. The throttle above is why its note warns that a
first score set since the last room walk may not show yet.

### Records held: the same comparison, computed in two places
A level is *held* when a player's high score is `>=` the location's `top_score` for it. Equality is
the ordinary case — the top score *is* somebody's own number — and every player who matches it
counts, so a tie lists twice. Greater-than is the catalog throttle again: a player who beat the
board reads high until the next room walk.

That rule is implemented twice and both must move together. `public._build_records` is the
authority for the **dashboard's Records held card**: server-rendered like the rest of `/`, it
walks *every* tracked location (skipping ones with no catalog, where there are no top scores to
hold) and emits per-player rows carrying the location name — which is why that card has a Location
column and the `/games` cards don't. `games.js:holdsTop` is the same predicate client-side, and
only feeds the crowned cells in the level breakdown plus the crown count on each collapsed
gamemode row.

The shimmer on a crowned number is transparent text over a clipped gradient, so it sits behind
both `prefers-reduced-motion: no-preference` and an `@supports (background-clip: text)` guard —
without the guard a browser lacking the property would render invisible numbers rather than
unstyled ones.

Each record chip on the dashboard carries the day it was set in its `title`. `_record_dates`
derives it by walking that player-location's snapshots oldest-first and taking the first one whose
score for the level reached its current value — sound only because Activate banks the best-ever
run, so a level's stored high never falls. The date is `streak.activity_day(polled_at)`, the same
one-day backdate the visit rows and chart points get, so a record and the visit that set it agree.
A level already at its current value in the player's *earliest* snapshot has no observed
transition and gets no date — it was set at some unknown point before tracking, and the chip says
so rather than naming the first poll. That is the `earned_on` rule for badges, applied to scores.
The walk stops as soon as every held level is accounted for, which on an unchanged board is the
first row read.

`public._beaten_levels` (raw_scores_json → `{(game_id, level_id): high}`, dropping zeros) and
`public._latest_snapshots` (newest snapshot per visible player at a location) are shared by
`_build_records` and `/api/game-data`; the zero-is-no-score rule lives in the former only.
