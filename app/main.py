"""FastAPI entrypoint. Wires routes, middleware, scheduler."""
from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config as config_mod
from . import db as db_mod
from . import poller
from .routes import admin as admin_routes
from .routes import auth_routes
from .routes import public as public_routes
from .security import CsrfMiddleware, SecurityHeadersMiddleware

log = logging.getLogger("activatetracker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


STATIC_DIR = Path("app/static")


def _make_static_versioner(static_dir: Path):
    """Return a Jinja helper that appends a content hash to static URLs.

    Browsers cache /static/app.css and /static/app.js aggressively; without a
    cache-busting token a client that loaded an older deploy keeps serving the
    stale asset against fresh HTML. The hash is computed once per file per
    process (the process restarts on deploy, so the cache is always fresh) and
    only changes when the file's bytes change."""
    cache: dict[str, str] = {}

    def static_v(filename: str) -> str:
        if filename not in cache:
            try:
                data = static_dir.joinpath(filename).read_bytes()
                cache[filename] = hashlib.sha256(data).hexdigest()[:8]
            except OSError:
                cache[filename] = "0"
        return cache[filename]

    return static_v


def _build_trigger(schedule: str, hour_utc: int) -> CronTrigger | None:
    if schedule == "manual":
        return None
    if schedule == "daily":
        return CronTrigger(hour=hour_utc, minute=0)
    # Treat anything else as a 5-field crontab.
    return CronTrigger.from_crontab(schedule)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = config_mod.load_config()
    conn = db_mod.connect(config_mod.db_path())
    db_mod.init_schema(conn)

    app.state.config = cfg
    app.state.db = conn
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["static_v"] = _make_static_versioner(STATIC_DIR)
    app.state.templates = templates

    scheduler: AsyncIOScheduler | None = None
    trigger = _build_trigger(cfg.poll.schedule, cfg.poll.hour_utc)
    if trigger is not None:
        scheduler = AsyncIOScheduler(timezone="UTC")

        async def scheduled_poll():
            try:
                await poller.poll_all(conn, cfg.poll)
            except Exception:
                log.exception("scheduled poll failed")

        scheduler.add_job(scheduled_poll, trigger, id="poll_all", max_instances=1)
        scheduler.start()
        log.info("scheduler started: %s", trigger)
    else:
        log.info("scheduler disabled (poll.schedule = manual)")

    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        conn.close()


def create_app() -> FastAPI:
    cfg = config_mod.load_config()  # validate config at import time too
    app = FastAPI(
        title="activateTracker",
        docs_url=None,         # don't expose /docs publicly
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CsrfMiddleware, config=cfg)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(public_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(admin_routes.router)
    return app


app = create_app()
