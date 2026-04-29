# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Local dev (Windows; use the venv's Python explicitly because system Python lacks the deps):

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q tests/test_streak.py::test_visit_at_day_30_keeps_streak
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
which is what Activate's per-day visit counting actually does. `streak.compute_streak` walks
sorted unique visit dates applying a 30-day reset.

### Multi-handle players
`players.handle` is a comma-separated list of Activate profile slugs (canonicalised to
`"a, b"` by `poller.format_handles`). One row in `player_locations` triggers N HTTP fetches
per refresh (one per handle). `scraper.combine_results` sums totals/yearly/stars/coins and
takes the best (min) of any leaderboard rank, then `persist_snapshot` writes ONE snapshot per
`(player, location)` holding the combined values. Visit detection runs on combined totals.

### Chart-data semantics
`/api/chart-data` only emits a point when a player's `total_score` actually moved between
polls (the first observation always counts). Days where no tracked player changed drop out
of the union x-axis entirely. Each point carries a `locations: {slug: score}` breakdown that
forward-fills per-location values across days where only some locations were polled. The
front-end (`app/static/app.js`) forward-fills the y values to every x label for tooltip
continuity but uses `pointRadius` callbacks to hide dots on dates that aren't real change-days.

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
framework. Adding columns to existing tables would require manual `ALTER TABLE` in `init_schema`
that's idempotent (e.g. checking `PRAGMA table_info`). Existing deployments' SQLite at
`/data/tracker.db` survives container rebuilds via the compose volume.
