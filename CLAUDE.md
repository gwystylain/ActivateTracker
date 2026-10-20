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
`"a, b"` by `poller.format_handles`). One row in `player_locations` costs two requests per
handle per refresh (that location's scores and its badge list), and each handle's profile is
read once per poll however many locations it has. `scraper.combine_results` sums
totals/yearly/stars/coins and takes the best (min) of any leaderboard rank, then
`persist_snapshot` writes ONE snapshot per `(player, location)` holding the combined values.
Visit detection runs on combined totals.

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

### The site's own API, not its page
Since October 2026 `/scores/<handle>/<id>/scores` is a Nuxt app that renders nothing of the
player server-side (the old `/scores/<handle>/<id>/<slug>/scores` URLs 404). The browser fills
it from JSON endpoints on the same host, and `scraper.py` reads exactly those:

| Endpoint | Gives | Asked |
|---|---|---|
| `/api/player/<h>` | name, profile `rank`, `stars`, `coins` | once per handle per poll |
| `/api/player/<h>/location/<id>` | `scores`, `standing`, `playerRank`, totals, `trophyProgress` | per handle per location |
| `/api/player/<h>/badges/<id>` | every badge `<id>` offers, `status` earned or not | per handle per location |
| `/api/location/<id>/rooms` | `[{id, name}]`, the site's order | once per location per poll |
| `/api/location/<id>/games` | every game: `roomId`, `roomIndex`, `levels`, `type` | once per location per poll |
| `/api/location/<id>/rooms/<room>/highscores` | the location's best per level | per room, on a catalog walk |

All are Cloudflare-fronted like the page, so `IMPERSONATE` still applies, and none needs a
cookie. A handle the site doesn't know gets its 404 *HTML* page from every player endpoint,
which `_get_json` turns into a `FetchError`; a 200 that isn't JSON (a challenge page) is a
`ScrapeError`. An unknown *location* id is quieter — `/rooms`, `/games` and `/badges` all
answer `[]` with a 200 — so `parse_games` reports no level count rather than zero, and
`fetch_player_badges` refuses an empty list outright, since retiring against it would delete
every badge the player has. A player who has never played a location gets a 200 with zeros.
The location slug in `player_locations` is no longer in any URL; it survives only as the
dashboard's name for the location.

### Test fixtures are real captured responses
`tests/fixtures/site/` holds live captures of those endpoints for one handle at Coquitlam (38)
and Langley (72), named after their paths (`player_gmebagholder_location_38.json` is
`/api/player/gmebagholder/location/38`). `tests/fixtures/badges_gmebagholder.json` is the badge
proxy's answer for the same handle, captured within the same minute. That pairing is the point:
`test_the_site_and_the_proxy_agree_on_every_badge_they_share` and the trophyProgress-versus-list
test check one source against the other, which only means anything while both describe the same
moment. Refresh them together, never one alone.

They replaced the HTML captures of the old server-rendered page and its brace-walking
extractor, both of which went with that page. If the payloads change shape, these fixtures and
the parsers in `scraper.py` are the canary. The captures also move on as the player plays —
Halfway Mark was unearned in the previous proxy capture and is earned in this one — so a test
that pins a count is stating the count *in the capture*, not a property of the badge.

### Schema is bootstrapped, not migrated
`db.init_schema` runs `CREATE TABLE IF NOT EXISTS ...` on every startup. There is no migration
framework. Adding a column to an existing table means adding it to `SCHEMA` *and* to `db._migrate`
as a `PRAGMA table_info`-guarded `ALTER TABLE` — `CREATE TABLE IF NOT EXISTS` is a no-op on a
deployment whose table already exists, so new columns only reach it through `_migrate`. Backfilled
columns are NULL on old rows, so readers must tolerate None. Adding a whole new *table* needs only
the `SCHEMA` entry (that's why `location_games` / `location_top_scores` / `location_catalog` /
`location_badges` have no `_migrate` clause). Existing deployments' SQLite at `/data/tracker.db` survives container
rebuilds via the compose volume.

### Three rank-shaped numbers; the dashboard shows the one the page names
The data carries three, and they are *different numbers* — mixing them up is the easiest
mistake here, so all three are parsed and named after what they are:

- `standing` (from `/api/player/<h>/location/<id>`) → `ScrapeResult.standing` →
  `score_snapshots.leaderboard_position`. The page states it outright as `PLAYER POSITION`
  (the old page said `Your Leaderboard Position: #138`). **This is what the dashboard's
  Leaderboard position column shows.** Per location — one live pull read 16 at coquitlam and
  508 at langley for the same handle.
- `playerRank` (same endpoint) → `ScrapeResult.location_player_rank` →
  `score_snapshots.player_rank`. The page's `RANK 47`, drawn with a bar reading
  `14,912/25,000` — the page works both out from `totalScore` in steps of 25,000, and the
  payload's value agrees. Still persisted because it has history, but not displayed — the
  dashboard used to show this in a column labelled Rank, which is why the column read #19 while
  the page said #138.
- `rank` from `/api/player/<h>` → `ScrapeResult.profile_rank`. A property of the profile, not
  of any location: the endpoint takes no location at all. Parsed, not persisted.

None of these is computed from the others by us. The headline row shows the best
(lowest) `leaderboard_position` across the player's locations, the same rule as the headline
discount and levels beat — a pick among values the site reported, not a blend. NULL means "not
observed" (a snapshot predating the column) and renders as an em dash; there is no fallback to a
different field. `combine_results` takes the best `standing` across a multi-handle player's
profiles, which likewise picks between two queried numbers.

No endpoint has a "levels beat" field. `scraper.parse_player_location` counts the `scores`
entries with a non-zero `highScore`, and `scraper.parse_games` sums `levels` over the
location's `/games` for the denominator — which is what the page's own script does, and the
pair matches its `LEVELS BEAT 242/510`. **That denominator includes the competitive games**:
`/games` lists them as `type: 4`, each with a single level 0 that no player's `scores` ever
carries, so it reads 510 at Coquitlam where the 49 co-op gamemodes hold 490, and 530 at
Langley against 510. The snapshot's `level_count` keeps the page's figure, because the dashboard
shows the number the page names; the catalog keeps only co-op levels (see below), and the two
are never compared. Snapshots from before October 2026 carry the old co-op-only figure. Across
multiple handles `combine_results` merges the `scores` lists by `(gameId, levelId)` keeping the
better `highScore` and counts *that*, rather than summing — a level both profiles cleared would
otherwise count twice. On the dashboard the headline row shows the highest levels-beat across the
player's locations, with the per-location values (and per-location ranks) in the expandable rows.

Coins, like stars and profile rank, are the account's: every location's snapshot from one poll
carries the same figure. The dashboard's Coins column therefore shows the newest snapshot's value
on the headline row only and leaves the per-location rows blank, the same treatment badges get.

### /games: per-level breakdown and Point Farmer
Terminology differs from the site's. What the UI calls a **Game** is the site's *room* (Hoops);
what it calls a **gamemode** is the site's *game* (`games[].id`, e.g. 1003 "Barrage"); a
**level** is `levelId`, 0-based in the data and displayed +1. `gameId // 100 == roomId`, and
`/games` states `roomId` outright as well.

**Per-player level data costs no extra requests.** `scores` in
`/api/player/<h>/location/<id>` already covers *every room at the location*, and
`persist_snapshot` writes it verbatim to `score_snapshots.raw_scores_json`. `/api/game-data`
reads the newest snapshot per `(player, location)` and emits only levels with `highScore > 0`;
everything absent renders as "No score". Historic snapshots are ignored — Activate banks only the
best-ever run, so the current row is the whole truth.

The catalog (which gamemodes and levels exist) arrives whole in the location's `/games`, which
every poll reads anyway for the level count. What a catalog walk adds is each room's
`/highscores` — one request per room. `app/catalog.py` owns this: `needs_refresh` re-walks a
location when it has never been catalogued, when a player's score there rose in this poll (the
deliberate throttle: no score change, no room requests), or when the stored `catalog_levels` no
longer matches the co-op total `/games` lists. A room is stored — gamemodes and top scores
together — only when its `/highscores` answered, so one failing room keeps its previous rows; a
room with no co-op gamemodes is cleared without a request. `poller.poll_all` dedupes to **one walk
per location per poll**, not one per player, and a catalog failure never fails the score poll.

That throttle has one blind spot by design: `/highscores` is the whole venue's top score, set by
people we don't track, so a location where none of *our* players moved never gets its top scores
re-read. `poll_all(..., force_catalog=True)` overrides it, exposed as the admin page's
**Refresh + rebuild catalog** button (`POST /admin/refresh-catalog`) alongside the ordinary
throttled **Refresh all now**.


### The catalog validates itself against /games
`catalog.refresh_location` recounts levels from `location_games` after each walk — so a partial
refresh is recorded honestly — and compares that with the co-op level total in the `/games` it
was handed (`LocationInfo.coop_levels`: 490 at coquitlam, 510 at langley), logging a warning on a
mismatch. `location_catalog.level_count` stores that expected co-op total, which is what the
`/games` page's "catalog is incomplete" note compares against — never the page's
competitive-inclusive denominator, which would flag every location as short. A short count also
makes `needs_refresh` retry on the next poll.

Only `type: 0` games are catalogued. `type: 4` is competitive; anything else is new and
`parse_games` logs it as unrecognised rather than guessing which side it belongs on. Top scores
are stored only for catalogued gamemodes, since nothing would ever clear one for a game the
catalog doesn't list.

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

Only **cooperative** gamemodes are listed, because those are the only ones the catalog keeps —
the competitive games have no levels and no leaderboard. Where a room runs a competitive game under
the same name (Hoops' Barrage, Hide's Numbers), the co-op rules are the ones written down.
The document covers rooms Activate has built anywhere, so it is a superset of any one location;
conversely a gamemode it hasn't caught up with gets nulls and simply renders with no tooltip and
a dash for its player count. Both directions are expected and neither is an error.

`optimal_players` is the document's "Optimal # of players" column, and it is **not populated for
all 76** — 5 cells are blank, Grip's Firewall holds a `?`, and Laser Maze and Photon Rush have no
row at all since the October 2026 edition moved them to its "Removed Games" section, so 8 come
through as None and the page shows a dash. The document's `**` marker ("no clear consensus")
survives as `optimal_disputed`, rendered as a starred, muted number: still shown, because a soft
answer beats none, but visibly softer than the other 58. The filter treats a disputed 5 as a 5 — it is the same
answer, just less settled.

`public._described` hangs the data on each game in `/api/game-data` rather than storing it with the
catalog — it is keyed by name, not something the site told us, so it has no business in a table
that mirrors upstream. Front-end: `games.js:modeName` marks up any *described* name with
`.mode-name` (the dotted underline is therefore the promise that there is something to read), and
one shared `position: fixed` bubble on `<body>` is refilled and moved. Expanding a gamemode in the
level breakdown leads with a `Description` row before the levels, because the bubble needs a
pointer and expanding is the only gesture a touch screen has — see the hover rule below. Fixed and body-level because
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

### Badges: the site says which, the proxy says how far
Two sources, and the app keeps them apart on purpose.

**The site** answers which badges a player holds. `/api/player/<h>/badges/<id>` lists every
badge that location offers — `{id, name, description, status}` and nothing more — and the poll
asks it for each `(handle, location)` alongside the scores. Badges two locations share read the
same at both (99 of 99 at coquitlam and langley), which is why `player_badges` has no
`location_id`; the lists differ only in what each building can host (Wormholes' Untouchable 5.0
needs Portals, Piperooni's needs Pipes). Its ids are the proxy's ids, and on every badge both
list they agree on name, description and earned. It is the authority on earned, and it needs no
opt-in: it is the same host and the same budget as the scores.

**The count** comes free with the scores. `trophyProgress` gives each tier as
`{completedBadges, requiredBadges, progress, earned}`; `completedBadges` is the exact count (the
same on every tier), and `scraper.badges_from_trophy_progress` reads it into
`score_snapshots.badges_earned` / `badges_possible`. Older payloads had only `progress`, a 2dp
fraction of each threshold, and that is still the fallback — reading the *smallest* threshold
still under 1.0 is exact up to 75 badges and ±1 past gold. `platinum.requiredBadges` is the
number of badges attainable **at that location**, and matches that location's `/badges` list
length exactly (101 at both), which is why the trophy helper drops any tier threshold above
`possible`: a 40-badge location can never reach silver's 50, so offering "24 to silver" would be
an errand with no end. A payload without the field parses to `(None, None)` — None is the
absence of a claim, not zero.

**The proxy** answers how far. `www.activate-scores.ca/api/activate/badges/<handle>` is a
community-run proxy in front of an official Activate badge API (a bad handle returns HTTP 500
with `{"message": "(Activate API) Internal server error"}`, naming its upstream). Public,
unauthenticated, keyed on the handle alone, and the only source of three things the site's list
lacks: **partial progress** towards each unearned badge, each badge's **star value**, and the
badges **no tracked location offers** — Row By Row needs a Climb room, so only the proxy can say
it was earned elsewhere. It moved here from `api.ryflix.ca/api/badges/activate-sync/<handle>`,
which now answers 503; the response shape is byte-identical.

**It 403s a request with no `Origin`.** `{"message": "Forbidden: invalid origin"}`, returned
to plain curl *and* to curl_cffi under `IMPERSONATE` — this is a server-side header check,
not a TLS fingerprint, so it is not the Cloudflare problem in different clothes and
impersonation does not address it. Either an `Origin` or a `Referer` naming the site
satisfies it; `scraper._origin_headers` derives the former from `badges.api_base` rather
than taking it as separate config, so a repointed base cannot leave behind a stale origin
that 403s every request.

Because it is somebody's personal server it is `config.badges.api_base`, and `poll_all`
takes `badge_cfg` **opt-in**: without it the poll never leaves playactivate.com, so no caller
reaches a third party by omission. A proxy failure increments `badges_errors` and never fails
the score poll; earned badges still update from the site.

**The site's silence is not zero.** `BadgeState.progress`, `total_progress` and `stars` are None
from the site, and `persist_badges` writes them through `COALESCE`, so a night when only the site
answered keeps the proxy's last numbers rather than erasing them. `player_badges.progress_at`
says when the proxy last spoke for a row; `updated_at` is when its earned state was last read.

### The badge proxy rate-limits, and the losers used to be the same every night
The predecessor at api.ryflix.ca was far stricter than playactivate.com, which serves 28
score pages back to back without complaint: five handles landed, the sixth and everything
after came back `HTTP 429` in ~6ms — answered by a limiter, not by the upstream — and it was
still refusing 16 seconds after the first request. The current host is two orders of
magnitude roomier and says so, returning `X-RateLimit-Limit: 120` with `Remaining` and a
`Reset` that sat ~30s out when measured, so `badges.spacing_seconds` is down to a courteous
1–3s. The leg keeps its own spacing rather than sharing `poll.jitter_seconds` because it is
the one that leaves playactivate.com, and the machinery below stays for the same reason —
the limit is somebody else's to change.

`scraper.RateLimited` exists to keep that apart from an ordinary `FetchError`: a 429 says
nothing about the request and the same handle works later, where a 404 or 500 means stop
asking. Only the former is retried, waiting the response's own `Retry-After` when it sends
one (`scraper.retry_after_seconds` handles both RFC forms) and a doubling backoff when it
doesn't. A wait over `max_wait_seconds` gives up instead: a poll holds `_poll_lock`, so an
hour asleep here is an hour the scheduler and the admin's Refresh button spend blocked, to
save one day of staleness on one player's badge progress.

Dropping a 429 was not merely lossy, it was *unfair*: the leg ran in score-poll order, which
is stable, so the same players lost every night — two sat ten days stale and one had never
had a single successful fetch, while the dashboard showed counts that were true when written.
`poller.badge_order` now runs stalest-first by `progress_at` (never answered leads), so whoever
lost last night is first in line tonight and a short window rotates over the roster instead of
pinning it. It must be `progress_at`, not `updated_at`: the site rewrites every row's
`updated_at` nightly, which would call everyone fresh and hand the order back to player id.
Note that hidden players are polled too and consume the same budget.

**A partial answer is never persisted.** `combine_badges` can only OR together what it was
handed and `persist_badges` writes `earned` from that, so writing one handle of a two-handle
player — or one of a player's locations — would clear every badge only the missing answer
holds. `poller._write_badges` therefore takes each source whole or not at all: the site's when
every `(handle, location)` answered, the proxy's when every handle did. Either alone is enough
to write; with neither, the write is skipped (`badges_skipped`) and last poll's rows stand —
older but true, and `/badges` states the date it is showing.

**Upstream retires badges, so `poller._retire_badges` deletes.** Go For Gold, Recollection
and Sniper Shot 1.0 went in the move, taking the catalog from 118 to 115, and Mascot followed
in October 2026 (114). Upserting alone
would have left every existing player counted out of 118 while a newly added one was counted
out of 115 — a denominator that depends on when you joined — since `/badges` reads `possible`
as that player's row count. A player's rows are pruned to the ids their own payload carried,
then a `badges` row is dropped once no player is left holding it: the foreign key makes that
order mandatory, and it is what lets a player whose poll was skipped keep their row until
theirs lands. This leans entirely on the partial-answer rule above — a list short by a failed
handle would otherwise read as "upstream dropped these" — and on one more: **only an answer
that defines the whole catalog may retire.** The proxy's does. The site's covers just the
tracked locations' badges, so it retires only when the proxy is switched off (then nothing else
will ever speak for the rest, and the denominator is honestly theirs); on a night the proxy is
merely down, retiring on the site's list would delete Row By Row and every other badge only the
proxy reports. `badge_reference.py` still carries
entries for the first three, because the document still describes them; a lookup that never
happens costs nothing.

Four traps, all of them load-bearing:

- **`badge_id` is the key, never `name`.** The API returns 114 badges under 113 names
  (118/117 before four were retired): `Untouchable 5.0` is id 111 (Piperooni) *and*
  id 125 (Wormholes). The community master document has the same collision.
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
per-location rows blank. The dashboard's figure is the enumerated union — with the proxy on,
the whole catalog (33 of 114 for one live profile) — while `trophyProgress` counts one
location's offer (32 of 101). So `/badges` doesn't compare those two: `badge_data` sends
`earned_in_scope`, what the player holds among the badges the tallying location offers
(from `location_badges`, below), and the page warns only when *that* disagrees with the
tally. Without the location's stored list it falls back to the whole count, which then
disagrees honestly for anyone holding a badge no tracked location offers. `earned_on` is stamped only on an observed false→true transition
and dated `streak.activity_day` — the same one-day backdate the visit rows get, so a badge
and the visit from the same session can't disagree. On a player's *first* poll there is no
transition to observe, so everything already earned is left NULL rather than backdated to
today, which would be a fabricated date. Multi-handle players go through
`scraper.combine_badges`: earned is OR'd and progress maxed, never summed — two of one
person's accounts hold overlapping sets, so a sum is a total neither account has.

`/badges` shows the enumerated count and the page's own tally side by side and says so
when they disagree, rather than picking a winner. Note `.rec-badge` is the records crown in
`app.css`, not an achievement; the old page's `Rank_10-19.png` header badge, which once
shared the word, went with that page.

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
`10 for 10`/`10 For 10`), and 117 of 118 live badges only match once normalised. Activate
also rewords descriptions now and then — 8 of the 114 live ones no longer match the
document's (Riddle 2.0 says "coins" where the document still says "stars") — and those
resolve through the name-only index, which is only safe because their names are unique. If
either `Untouchable 5.0` is ever reworded, it loses its room rather than taking the other's.

Where the sources disagree about a room the document wins: it was right about both
`Untouchable 5.0` rooms (checked against `location_games`, which the other source got
wrong for both). Recollection's Memory is the conflict that check can't settle — it runs
at no tracked location — and the document is taken.

**A badge only ryflix lists is still built**, from ryflix alone. That is Steady Stream:
Activate still serves it, but the October 2026 document moved it from its badge list to
its notes on retired mechanics, and dropping it would have cost it its grade and room.
Its room is the one rooms-from-ryflix-only case the generator second-guesses: when the
claimed room runs none of what the badge names and the master document places that
gamemode in exactly one room, that room is taken, with a note to stderr. Ryflix says Push;
Photon Rush is a Laser game. Rooms from the document are never second-guessed.

**`games` is the (room, gamemode) pairs a badge names**, matched against
`master_document.GAMEMODES` across the name, description and `Game/Level` — name-matching
rather than sentence-parsing because the sentences vary ("Complete Bop level 7", "Win level
1 of Mega Grid", "Easter Egg Statues", "Scramble 1") and the names don't. Longest name
first and a matched span is spent, so The Marathon's "Mega Relay" can't also count as
Hide's "Relay". `GAMEMODES` holds only co-op gamemodes, so a badge played in a competitive
one (Snake Island's Tails) names nothing and is placed by its room alone. Because the
generator imports `master_document`, rerun it after editing that file too.
`master_document` keeps Laser Maze and Photon Rush, which the October 2026 document moved
to "Removed Games", precisely so Steady Stream can still be placed.

`gen_badge_reference.ESTIMATES` can carry this repo's own `difficulty` for a badge neither
source grades, declared by `difficulty_estimated` with the reasoning in
`difficulty_note`. It is empty today: its two estimates retired themselves the two ways an
estimate should go — ryflix graded Photobomb (Easy, as estimated), and Mascot left the
API. An estimate only ever *fills a hole* — one that finds a sourced grade already there is
dropped with a warning to stderr, so a document catching up retires it rather than being
argued with. The page shows an estimated grade muted and starred, the same treatment
`/games` gives a player count the document records without consensus: a soft answer beats
a hole in the column, but it must not read as something a document said. The filter and
the sort treat an estimated Easy as an Easy — same answer, less settled.

`hint` and `giveaway` are the Easter Egg and Riddle answers. The source document hides
them as white-on-white text because each can only be solved once; this page shows them
outright alongside the other detail, so expanding a badge is enough to spend that. That
is a deliberate call — the page exists to surface everything known about a badge — but
it is the reason the detail is behind a click at all rather than sitting in the grid.

### /badges by location: the site's list first, then the gamemode
`app/badge_locations.py` answers, per badge and per tracked location, whether it can be
done there (`where`) and any location-specific facts (`here`); `/api/badge-data` sends both
for every location up front, so the Location filter never refetches. Pure functions over
`location_games` rows and the location's offer, so the rules are tested without a database.

**The site's own list decides.** `location_badges` holds, per location, the badge ids the
site's per-location badge list carried — the poll already reads that list for every
`(handle, location)`, so storing it costs nothing. The list is the same whoever asks, so
`poller.persist_location_badges` swaps in the union of whichever answers landed, and a
location none answered for keeps last night's rows. With a location's list, every badge's
answer is the site's: on it is `yes`, off it is `no` — photo-room and roomless badges
included, which the inference below can't place — and there is never an `unknown`. A `no`
keeps the inference's reason when the inference agreed ("no Climb room") and otherwise says
`NOT_OFFERED`. A location with a list but no catalog yet is still answered for. Everything
below is the fallback for a location with no stored list, and the source of the reasons.

**Availability checks the gamemode as well as the room.** Rooms swap what they run:
Langley has a Laser room, but it runs Sneak and Chopper, and Steady Stream wants Photon
Rush. So a room counts only if it runs every gamemode the badge's `games` names there.

**Three answers, because the negative is weak in one specific way.** `location_games` holds
only *scoring* rooms, so the photo room is in no location's list whether or not the
building has one. A room the master document doesn't know and the location doesn't list
is therefore `unknown`, never `no`; only a known scoring room that is missing, or one
running something else, is a `no`. A badge with no room gets no answer — anywhere will do.
A location with no catalog is left out entirely rather than answering "no" to everything.

The Location select has two kinds of option and they differ on purpose. A specific
location hides only a definite `no` — the question is what can be done in that building,
and a badge earned elsewhere can't be done there. **Any of my locations** (shown only with
more than one tracked location; it replaced a checkbox, and a saved tick migrates to it)
is the weaker claim, so it keeps the older rule: a badge somebody already holds is never
hidden, because badges transfer between locations — `Row By Row` (Climb, at neither tracked
location) is earned all the same, which is also why the **Where** line words that case as
"earned already, so somewhere else" rather than "you can't get this". Choosing a location
narrows the Room filter to rooms doable there and limits the detail panel's facts to it;
a room the catalog can't see (the photo room) is offered there whenever a badge needing it
is answered `yes` or `unknown`.

**Facts** are keyed by badge name in `badge_locations._RULES`: Riddle 7.0's co-op S games
with a level 7, Activated's and Halfway Mark's level counts (half rounds up, so an odd
count never reads as done a level early), The Grand Tour's rooms in the site's order, and
Completionist's co-op game count — a floor only, since competitive games count towards it
and the catalog keeps only co-op ones (`/games` does list them now, as `type: 4`, so the
floor could become the full count if the catalog ever stores them). Per-player lines come from the newest snapshot at that
location, intersected with the catalog so a count can't exceed its total, and the page
skips them for a player who already holds the badge.

### /badges export: a brief for Claude, not a data dump
`GET /badges/export.md?players=<ids>&location=<id|tracked>` (the page's **Copy for Claude** and
**Download**) is Markdown built by `app/badge_export.py`, a pure module over `_badge_payload`'s
output — the same payload `/api/badge-data` sends, plus the per-location catalogs (`games`,
`beaten`) the page itself never needs. It opens with a brief asking Claude for a route that
stacks badges (one careful Zones 5 is Tiptoeing *and* one of 5 For 5's five), then every badge a
ticked player lacks with Activate's wording and the community detail, then every gamemode at the
location with each player's beaten levels as ranges — the evidence for "a level 5 we can win".

It takes the players and the location from the page and **ignores room, difficulty and
search**: stacking only shows up across rooms and grades, so a filtered export would miss what
it exists for. "Any of my locations" differs from the page in one way — a badge no tracked
location can host is dropped even if somebody holds it, since a route can't visit it. Copy tries
`ClipboardItem` with a promise (Safari wants the write inside the click), then `writeText`, then
`execCommand('copy')`, which is the only one that works over plain HTTP or where the clipboard
permission is denied — the Claude desktop app's browser pane denies it.

### The description bubble is a pointer's shortcut, never the only way in
A long press is the only gesture a touch screen has for the hover bubble, and it is the
text-selection gesture too, so on a phone the bubble is a fight to summon and a fight to
dismiss. The rule is therefore that no description lives *only* in the bubble:

- Where the name sits in a row that expands — the `/badges` grid and Badge farmer, the
  `/games` level breakdown — the panel that row opens carries a `Description` line, and the
  bubble is skipped on a device that cannot hover (`tipWanted`, in both `badges.js` and
  `games.js`, read at event time so plugging in a mouse changes the answer). The dotted
  underline goes with it, under `@media (hover: none)`: the underline is the promise of
  something to read, and there the row's own ▸ is the affordance instead.
- Where the name is its own control — `/badges` Closest to earning, `/games` Point farmer
  and Never scored, the three calls that pass `focusable` and so carry a `tabindex` — there
  is no panel to expand, but a tap focuses the name and the focus path opens the bubble.
  Those keep both the bubble and the underline on touch. `[tabindex]` is what tells the two
  cases apart, in the CSS as much as the JS.

The `/badges` panel's `Description` is Activate's own wording, not a community field, so it
sits *above* the empty-panel message rather than replacing it: a badge can have a description
from Activate and still have nothing recorded by the documents.

### The page never scrolls sideways; .table-wrap does
Wide content is reached by scrolling the table, never the document, so `html` carries
`overflow-x: clip` (with `hidden` as the fallback for browsers without `clip`; `clip`
avoids making the root a scroll container). Without it a single element wider than the
viewport lets the whole page pan, and because block backgrounds only paint to the
viewport width the card ends up adrift in a strip of bare background — the `.topbar`
was doing exactly that below ~310px, where the brand and nav together no longer fit,
hence its `flex-wrap: wrap`. The clip is a backstop, not a licence to overflow: an
element that needs more width belongs in a `.table-wrap`, or it will simply be cut off.

`.table-wrap` also sets `overscroll-behavior-x: contain`, so a horizontal swipe that
has run out of table stops there instead of chaining into the page or into Chrome for
Android's back-navigation gesture. `.mode-tip` sets the same for its vertical scroll.

### Top scores are per-location and sparse
A room's `/highscores` is the best score anyone *at that location* has posted, not a global
best: coquitlam and langley disagree on every shared Hoops entry. It is also sparse — langley
returns 38 Hoops rows where coquitlam returns 40 — and a missing entry means nobody at that
location has ever scored that level. The `/games` table shows that as a dash. Point Farmer can't,
since a level nobody has touched would then rank *below* one people have already played, which is
backwards; `games.js` substitutes the median top score for the same level index across the
location (scores track the level number closely, level 1 ≈ 2k up to level 10 ≈ 10k).

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
