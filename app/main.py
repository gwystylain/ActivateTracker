"""FastAPI entrypoint. Wires routes, middleware, scheduler."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

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
    app.state.templates = Jinja2Templates(directory="app/templates")

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
