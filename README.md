# activateTracker

Self-hosted web app that tracks Activate ([playactivate.com](https://playactivate.com))
player scores over time. Public landing page shows a graph plus per-player
streak / discount / visit summary; password-protected admin section adds and
removes tracked players.

There is no public Activate API, so the app scrapes each player's per-location
scores page on a daily schedule. A "visit" is inferred whenever a player's
`totalScore` at any of their tracked locations increases between two
consecutive snapshots.

## Quick start (Docker)

```bash
git clone <this repo> activateTracker && cd activateTracker

# 1. Generate a bcrypt hash for the admin password
docker run --rm -it -v "$PWD":/app -w /app python:3.12-slim sh -c \
    "pip install -q bcrypt && python -c 'import bcrypt,getpass; print(bcrypt.hashpw(getpass.getpass().encode(),bcrypt.gensalt(12)).decode())'"

# 2. Generate a session secret
python -c "import secrets; print(secrets.token_hex(32))"

# 3. Create config.yaml from the example and paste the hash + secret
cp config.yaml.example config.yaml
$EDITOR config.yaml

# 4. Build and run
docker compose up -d --build
```

Open [http://localhost:8000/](http://localhost:8000/) — public landing page.
[/login](http://localhost:8000/login) — admin login.

The SQLite database lives at `./data/tracker.db` and survives container
rebuilds.

## Adding a player

1. Log in at `/login`.
2. Go to `/admin`.
3. Fill out the form:
   - **Handle** — the URL slug Activate uses, e.g. `gmebagholder`. Find it in
     the URL of the player's profile page.
   - **Display name** — optional pretty name shown in the chart legend.
   - **Initial streak** — visits the player has already accumulated *before*
     tracking started (0–5). Use this so the discount displays correctly on day
     one. Today's date is recorded as the baseline; if 30 days pass without a
     real visit being detected, the streak still resets.
   - **Locations** — one per line as `id,slug`, e.g.
     ```
     72,langley
     38,coquitlam
     ```
     Find these in the player's scores page URL:
     `https://playactivate.com/scores/<handle>/<id>/<slug>/scores`.

A daily background poll fetches scores for every (player, location) at the time
configured in `config.yaml` (default 11:00 UTC ≈ 04:00 Pacific). Use **Refresh
all now** on the admin page to poll immediately.

## Configuration (`config.yaml`)

See `config.yaml.example` for the full schema. Notable knobs:

| key | meaning |
|-----|---------|
| `admin.username`, `admin.password_bcrypt` | login credentials. Hash with `python -m app.tools.hashpw`. |
| `session.secret_key` | signs the session cookie. Rotate to invalidate every session. ≥32 chars. |
| `session.max_age_hours` | how long a login lasts. Default 24h. |
| `poll.schedule` | `"daily"`, `"manual"`, or a 5-field crontab like `"0 11 * * *"`. |
| `poll.hour_utc` | hour of day for `"daily"` (0–23 UTC). |
| `poll.user_agent` | UA string sent to playactivate.com. |
| `server.trusted_proxy_hops` | number of proxies in front (nginx-proxy-manager = 1). |

To change the admin password, generate a new hash, edit `config.yaml`, and
restart the container. There is intentionally no UI for this.

## Behind nginx-proxy-manager

The app expects to live behind a TLS-terminating reverse proxy:

- Container listens on `8000`. Map your public hostname to it.
- The included `--proxy-headers --forwarded-allow-ips "*"` flags trust
  `X-Forwarded-Proto` so secure-cookie behaviour works.
- HSTS, CSP, X-Frame-Options, Permissions-Policy, Referrer-Policy, and
  X-Content-Type-Options headers are emitted by the app itself.
- `/robots.txt` denies all crawlers.

## Security notes

- Admin login: bcrypt-hashed password, signed session cookie (HttpOnly,
  SameSite=Lax, Secure when scheme is https), 5-attempt-per-5-minute IP
  throttle (visitor IPs are SHA-256-hashed before being persisted).
- All state-changing routes require a CSRF token derived from the session.
- Strict CSP: `script-src 'self'` only — Chart.js is vendored to
  `app/static/`, no third-party CDN at runtime.
- Container runs as UID 10001 with a read-only root filesystem; only `/data`
  is writable.
- `/docs`, `/redoc`, `/openapi.json` are disabled.

## Visit / streak heuristics

Activate's site doesn't expose visit timestamps, so visits are inferred:

- A snapshot's `totalScore` strictly greater than the previous snapshot for
  that (player, location) → one visit recorded with today's date.
- Multiple location-increases on the same calendar day count as one visit
  (matches Activate's per-day visit accounting).
- Streak: +1 per visit, resets to 0 if 30 days elapse without a visit.
  Discount = `min(streak, 5) * 5%`, capped at 25%.
- Edge case: if a player visits but doesn't beat any high score on any game,
  the visit will not be detected. In practice `totalScore` tends to creep up
  on every visit because at least one low-level score improves.

Polling daily is therefore more accurate than manual-only — a missed day
collapses into the next refresh's date.

## Development

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -e ".[dev]"
pytest
ACTIVATETRACKER_DB=data/dev.db uvicorn app.main:app --reload
```

The unit tests use a real captured page from playactivate.com as a fixture
(`tests/fixtures/gmebagholder_langley.html`) — no live network access required.

## Project layout

```
app/
├── main.py            FastAPI app + APScheduler lifespan
├── config.py          YAML loader (pydantic-validated)
├── db.py              SQLite connection + schema
├── scraper.py         curl_cffi fetch + JSON-blob extractor
├── poller.py          poll_all() + visit detection
├── streak.py          pure streak/visit math
├── auth.py            bcrypt, signed sessions, login throttling
├── security.py        CSRF + security headers middleware
├── routes/            public, auth_routes, admin
├── templates/         Jinja2: base, index, login, admin
├── static/            CSS, vendored Chart.js, app.js
└── tools/hashpw.py    python -m app.tools.hashpw
```

## Why curl_cffi instead of requests/httpx?

playactivate.com is fronted by Cloudflare, which TLS-fingerprints the
ClientHello and rejects non-browser handshakes with `403 Forbidden`. `curl_cffi`
impersonates Chrome's TLS stack so the requests look identical to a real
browser. Same User-Agent via `httpx` returns 403; via `curl_cffi` it returns
200. If Activate ever changes provider this can be swapped back to `httpx`
trivially.
