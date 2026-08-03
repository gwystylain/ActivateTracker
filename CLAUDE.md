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
`(player_id, location_id)`. A strictly-greater value inserts one row in `visits` dated today.
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

### Schema is bootstrapped, not migrated
`db.init_schema` runs `CREATE TABLE IF NOT EXISTS ...` on every startup. There is no migration
framework. Adding a column to an existing table means adding it to `SCHEMA` *and* to `db._migrate`
as a `PRAGMA table_info`-guarded `ALTER TABLE` — `CREATE TABLE IF NOT EXISTS` is a no-op on a
deployment whose table already exists, so new columns only reach it through `_migrate`. Backfilled
columns are NULL on old rows, so readers must tolerate None. Adding a whole new *table* needs only
the `SCHEMA` entry (that's why `location_games` / `location_top_scores` / `location_catalog` have
no `_migrate` clause). Existing deployments' SQLite at `/data/tracker.db` survives container
rebuilds via the compose volume.

### Rank and levels-beat come from the same page as the score
`score_snapshots.player_rank` is the *per-location* leaderboard rank (`playerLocation.playerRank`),
not the cross-location profile rank (`player.rank`, which is scraped as `ScrapeResult.player_rank`
but not persisted). The hydration blob has no "levels beat" field: `scraper.parse_html` derives it
as the count of `playerLocation.scores` entries with a non-zero `highScore`, alongside
`location.levelCount` as the denominator. That derivation is not a guess: the page also *renders*
the answer as `Levels Beat: 106/490` in presentational markup, and the derived pair matches it
exactly (checked live at coquitlam 106/490 and langley 49/510). The JSON blob is the more stable
anchor than the Tailwind-classed `<p>`, so we derive rather than scrape the rendered string — but
that string is the ground truth to re-check against if the number ever looks wrong. Note the
committed langley fixture is an old capture (40/470), so fixture numbers lag live ones. Across
multiple handles `combine_results` merges the `scores` lists by `(gameId, levelId)` keeping the
better `highScore` and counts *that*, rather than summing — a level both profiles cleared would
otherwise count twice. On the dashboard the headline row shows the best (lowest) rank and the
highest levels-beat across the player's locations, with the per-location values in the expandable
rows.

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

`public._beaten_levels` (raw_scores_json → `{(game_id, level_id): high}`, dropping zeros) and
`public._latest_snapshots` (newest snapshot per visible player at a location) are shared by
`_build_records` and `/api/game-data`; the zero-is-no-score rule lives in the former only.
