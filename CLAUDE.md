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
The plotted value (`top_score`) is the player's *highest* single-location score, not the sum
across locations. `/api/chart-data` only emits a point when that value actually moved between
polls (the first observation always counts), so a gain at a non-leading location leaves the
line flat and drops out. Days where no tracked player changed drop out of the union x-axis
entirely. Each point carries a `locations: {slug: score}` breakdown that forward-fills
per-location values across days where only some locations were polled. The front-end
(`app/static/app.js`) forward-fills the y values to every x label for tooltip continuity but
uses `pointRadius` callbacks to hide dots on dates that aren't real change-days.

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

### Test fixture is a real captured response
`tests/fixtures/gmebagholder_langley.html` is a 378 KB capture of a live playactivate page.
`extract_player_blob` anchors on the unique `"player":{"player":{"playerName"` substring and
walks braces (handling escaped quotes in strings). If the site's hydration shape changes, this
fixture and the anchor regex are the canary.

### Schema is bootstrapped, not migrated
`db.init_schema` runs `CREATE TABLE IF NOT EXISTS ...` on every startup. There is no migration
framework. Adding a column to an existing table means adding it to `SCHEMA` *and* to `db._migrate`
as a `PRAGMA table_info`-guarded `ALTER TABLE` — `CREATE TABLE IF NOT EXISTS` is a no-op on a
deployment whose table already exists, so new columns only reach it through `_migrate`. Backfilled
columns are NULL on old rows, so readers must tolerate None. Existing deployments' SQLite at
`/data/tracker.db` survives container rebuilds via the compose volume.

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
multiple handles
`combine_results` takes the *union* of beaten `(gameId, levelId)` pairs rather than summing, or a
level both profiles cleared would count twice. On the dashboard the headline row shows the best
(lowest) rank and the highest levels-beat across the player's locations, with the per-location
values in the expandable rows.
