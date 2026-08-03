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
               s.player_id        AS player_id,
               s.location_id      AS location_id,
               s.polled_at        AS polled_at,
               s.total_score      AS total_score
        FROM score_snapshots s
        JOIN players p ON p.id = s.player_id
        WHERE p.hidden = 0
        ORDER BY p.handle, s.location_id, s.polled_at
        """
    ).fetchall()

    # location_id -> slug, looked up from current tracked locations. Snapshots
    # for a (player, location) pair whose row has since been removed will fall
    # back to "loc-<id>" in the breakdown.
    loc_slug = {
        (r["player_id"], r["location_id"]): r["slug"]
        for r in conn.execute(
            "SELECT player_id, location_id, slug FROM player_locations"
        ).fetchall()
    }

    # Group by handle. For each (handle, day), record the latest total_score per location.
    per_handle_per_day_per_loc: dict[str, dict[str, dict[int, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    display_name_for: dict[str, str | None] = {}
    handle_to_player_id: dict[str, int] = {}

    for r in rows:
        handle = r["handle"]
        display_name_for[handle] = r["display_name"]
        handle_to_player_id[handle] = r["player_id"]
        day = r["polled_at"][:10]
        per_handle_per_day_per_loc[handle][day][r["location_id"]] = r["total_score"]

    payload: list[dict[str, Any]] = []
    for handle, days in per_handle_per_day_per_loc.items():
        pid = handle_to_player_id[handle]
        # Forward-fill per-location scores so each day's breakdown reflects all
        # locations the player has ever scored at, not just ones polled that day.
        # Every point carries both metrics the front-end can plot — the best
        # single location and the sum of all of them — because the chart toggles
        # between them without re-fetching. A day is emitted when *either* moved
        # (or it's the first observation); dropping the days where neither did
        # keeps the chart from growing a dot per poll on uneventful days. Which
        # of the two actually moved is left to the front-end to work out, since
        # it can compare consecutive points itself.
        carry: dict[int, int] = {}
        points: list[dict[str, Any]] = []
        last_top: int | None = None
        last_total: int | None = None
        for day in sorted(days):
            carry.update(days[day])
            top = max(carry.values())
            total = sum(carry.values())
            if last_top is not None and top == last_top and total == last_total:
                continue
            breakdown = {
                loc_slug.get((pid, loc_id), f"loc-{loc_id}"): score
                for loc_id, score in sorted(carry.items())
            }
            points.append(
                {
                    "date": day,
                    "top_score": top,
                    "total_score": total,
                    "locations": breakdown,
                }
            )
            last_top = top
            last_total = total
        display = display_name_for.get(handle) or handle
        legend_label = display if display == handle else f"{display} ({handle})"
        payload.append(
            {
                "handle": handle,
                "display_name": display,
                "legend_label": legend_label,
                "points": points,
            }
        )

    return JSONResponse({"players": payload})


# ---------- helpers ----------

def _location_label(slug: str) -> str:
    """Human-readable location name from its slug (e.g. 'langley' -> 'Langley')."""
    return slug.replace("-", " ").replace("_", " ").title()


def _build_player_summaries(conn, *, today: date) -> list[dict[str, Any]]:
    players = conn.execute(
        """
        SELECT id, handle, display_name, initial_streak, initial_streak_set_at
        FROM players WHERE hidden = 0 ORDER BY handle
        """
    ).fetchall()

    out: list[dict[str, Any]] = []
    for p in players:
        visit_rows = conn.execute(
            "SELECT visit_date, location_id FROM visits WHERE player_id = ? ORDER BY visit_date",
            (p["id"],),
        ).fetchall()
        dates = [date.fromisoformat(r["visit_date"]) for r in visit_rows]

        # Group visit days by location id so each location's stamp card can be
        # summarised independently. A location whose player_locations row was
        # removed still surfaces under "loc-<id>".
        loc_name = {
            r["location_id"]: _location_label(r["slug"])
            for r in conn.execute(
                "SELECT location_id, slug FROM player_locations WHERE player_id = ?",
                (p["id"],),
            ).fetchall()
        }
        visit_dates_by_loc: dict[int, list[date]] = defaultdict(list)
        for r in visit_rows:
            visit_dates_by_loc[r["location_id"]].append(
                date.fromisoformat(r["visit_date"])
            )

        # Latest snapshot per location carries the leaderboard rank and levels
        # beaten. Ordered ascending so the last write per location wins.
        latest_snap: dict[int, Any] = {}
        for r in conn.execute(
            """
            SELECT location_id, player_rank, levels_beat, level_count
            FROM score_snapshots
            WHERE player_id = ?
            ORDER BY location_id, polled_at
            """,
            (p["id"],),
        ).fetchall():
            latest_snap[r["location_id"]] = r

        baseline = (
            date.fromisoformat(p["initial_streak_set_at"])
            if p["initial_streak_set_at"]
            else None
        )
        summary_kwargs = dict(
            initial_streak=p["initial_streak"] or 0,
            initial_streak_set_at=baseline,
            today=today,
        )
        summary = streak_mod.summarize(dates, **summary_kwargs)

        # Per-location breakdown using the same columns: every tracked location,
        # plus any historical-only ones still present in visits. The admin
        # baseline is player-level (no location attached) so it's applied to
        # each location's window — same rule as the aggregate above.
        loc_ids = list(loc_name) + [
            lid for lid in visit_dates_by_loc if lid not in loc_name
        ]
        locations: list[dict[str, Any]] = []
        for lid in loc_ids:
            ls = streak_mod.summarize(visit_dates_by_loc.get(lid, []), **summary_kwargs)
            snap = latest_snap.get(lid)
            locations.append(
                {
                    "name": loc_name.get(lid, f"loc-{lid}"),
                    "discount_pct": ls.discount_pct,
                    "days_since_last_visit": ls.days_since_last_visit,
                    "last_visit_date": ls.last_visit_date.isoformat()
                    if ls.last_visit_date
                    else None,
                    "visits_last_30_days": ls.visits_last_30_days,
                    "visits_ytd": ls.visits_ytd,
                    "rank": snap["player_rank"] if snap else None,
                    "levels_beat": snap["levels_beat"] if snap else None,
                    "level_count": snap["level_count"] if snap else None,
                }
            )
        # Highest discount first, then alphabetical; headline discount is the best.
        locations.sort(key=lambda l: (-l["discount_pct"], l["name"]))
        discount_pct = max((l["discount_pct"] for l in locations), default=0)
        # Headline rank is the best (lowest) across locations; headline levels
        # beat is the highest, carrying that location's level total with it.
        ranks = [l["rank"] for l in locations if l["rank"] is not None]
        best_rank = min(ranks) if ranks else None
        beat_locs = [l for l in locations if l["levels_beat"] is not None]
        top_beat = max(beat_locs, key=lambda l: l["levels_beat"], default=None)
        out.append(
            {
                "id": p["id"],
                "handle": p["handle"],
                "display_name": p["display_name"] or p["handle"],
                "discount_pct": discount_pct,
                "locations": locations,
                "rank": best_rank,
                "levels_beat": top_beat["levels_beat"] if top_beat else None,
                "level_count": top_beat["level_count"] if top_beat else None,
                "days_since_last_visit": summary.days_since_last_visit,
                "last_visit_date": summary.last_visit_date.isoformat()
                if summary.last_visit_date
                else None,
                "visits_last_30_days": summary.visits_last_30_days,
                "visits_ytd": summary.visits_ytd,
                "visits_by_year": summary.visits_by_year,
            }
        )
    return out
