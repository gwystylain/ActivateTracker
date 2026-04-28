"""Public, unauthenticated routes: landing page + chart data."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .. import streak as streak_mod
from ..auth import csrf_token_for, read_session

router = APIRouter()


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots() -> str:
    return "User-agent: *\nDisallow: /\n"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = request.app.state.config
    conn = request.app.state.db
    templates = request.app.state.templates

    session = read_session(cfg, request.cookies.get(cfg.session.cookie_name))

    today = date.today()
    summaries = _build_player_summaries(conn, today=today)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "players": summaries,
            "today": today.isoformat(),
            "logged_in": session is not None,
            "csrf_token": csrf_token_for(session),
            "history_years": [today.year - i for i in range(4)],
        },
    )


@router.get("/api/chart-data")
async def chart_data(request: Request) -> JSONResponse:
    conn = request.app.state.db
    rows = conn.execute(
        """
        SELECT p.handle           AS handle,
               p.display_name     AS display_name,
               s.location_id      AS location_id,
               s.polled_at        AS polled_at,
               s.total_score      AS total_score
        FROM score_snapshots s
        JOIN players p ON p.id = s.player_id
        ORDER BY p.handle, s.location_id, s.polled_at
        """
    ).fetchall()

    # Group by handle. For each (handle, day), sum the latest total_score per location.
    per_handle_per_day_per_loc: dict[str, dict[str, dict[int, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    display_name_for: dict[str, str | None] = {}

    for r in rows:
        handle = r["handle"]
        display_name_for[handle] = r["display_name"]
        day = r["polled_at"][:10]  # YYYY-MM-DD
        # Last write wins per (day, location) because rows are ordered by polled_at ASC.
        per_handle_per_day_per_loc[handle][day][r["location_id"]] = r["total_score"]

    payload: list[dict[str, Any]] = []
    for handle, days in per_handle_per_day_per_loc.items():
        # Forward-fill location totals so the per-day sum reflects all known locations.
        carry: dict[int, int] = {}
        points: list[dict[str, Any]] = []
        for day in sorted(days):
            carry.update(days[day])
            points.append({"date": day, "total_score": sum(carry.values())})
        payload.append(
            {
                "handle": handle,
                "display_name": display_name_for.get(handle) or handle,
                "points": points,
            }
        )

    return JSONResponse({"players": payload})


# ---------- helpers ----------

def _build_player_summaries(conn, *, today: date) -> list[dict[str, Any]]:
    players = conn.execute(
        """
        SELECT id, handle, display_name, initial_streak, initial_streak_set_at
        FROM players ORDER BY handle
        """
    ).fetchall()

    out: list[dict[str, Any]] = []
    for p in players:
        visit_rows = conn.execute(
            "SELECT visit_date FROM visits WHERE player_id = ? ORDER BY visit_date",
            (p["id"],),
        ).fetchall()
        dates = [date.fromisoformat(r["visit_date"]) for r in visit_rows]
        baseline = (
            date.fromisoformat(p["initial_streak_set_at"])
            if p["initial_streak_set_at"]
            else None
        )
        summary = streak_mod.summarize(
            dates,
            initial_streak=p["initial_streak"] or 0,
            initial_streak_set_at=baseline,
            today=today,
        )
        out.append(
            {
                "id": p["id"],
                "handle": p["handle"],
                "display_name": p["display_name"] or p["handle"],
                "current_streak": summary.current_streak,
                "discount_pct": summary.discount_pct,
                "days_since_last_visit": summary.days_since_last_visit,
                "last_visit_date": summary.last_visit_date.isoformat()
                if summary.last_visit_date
                else None,
                "visits_this_month": summary.visits_this_month,
                "visits_ytd": summary.visits_ytd,
                "visits_by_year": summary.visits_by_year,
            }
        )
    return out
