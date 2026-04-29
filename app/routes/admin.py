"""Authenticated admin routes: manage players, trigger refresh."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, poller
from ..db import transaction

router = APIRouter()


def _require_session(request: Request) -> dict:
    cfg = request.app.state.config
    session = auth.read_session(cfg, request.cookies.get(cfg.session.cookie_name))
    if session is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return session


@router.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    session = _require_session(request)
    conn = request.app.state.db
    templates = request.app.state.templates

    players = conn.execute(
        """
        SELECT p.id, p.handle, p.display_name, p.initial_streak,
               p.initial_streak_set_at, p.created_at
        FROM players p ORDER BY p.handle
        """
    ).fetchall()

    locations_by_player: dict[int, list[dict]] = {}
    for r in conn.execute(
        "SELECT player_id, location_id, slug FROM player_locations ORDER BY player_id, location_id"
    ).fetchall():
        locations_by_player.setdefault(r["player_id"], []).append(
            {"location_id": r["location_id"], "slug": r["slug"]}
        )

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "players": [
                {
                    **dict(p),
                    "locations": locations_by_player.get(p["id"], []),
                }
                for p in players
            ],
            "csrf_token": auth.csrf_token_for(session),
            "username": session.get("u"),
        },
    )


@router.post("/admin/players")
async def add_player(
    request: Request,
    handle: str = Form(...),
    display_name: str = Form(""),
    initial_streak: int = Form(0),
    locations: str = Form(""),  # newline-separated  "id,slug"
):
    _require_session(request)
    conn = request.app.state.db

    handle = handle.strip()
    if not handle:
        raise HTTPException(400, "handle is required")

    parsed_locations = _parse_locations(locations)
    if not parsed_locations:
        raise HTTPException(
            400,
            "At least one location is required, formatted as 'id,slug' per line "
            "(e.g. '72,langley').",
        )

    today = datetime.now(timezone.utc).date().isoformat()

    try:
        with transaction(conn):
            cur = conn.execute(
                """
                INSERT INTO players (handle, display_name, initial_streak,
                                     initial_streak_set_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    handle,
                    display_name.strip() or None,
                    max(0, int(initial_streak)),
                    today if initial_streak > 0 else None,
                ),
            )
            new_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO player_locations (player_id, location_id, slug) VALUES (?, ?, ?)",
                [(new_id, loc_id, slug) for loc_id, slug in parsed_locations],
            )
    except Exception as e:
        # Most common: UNIQUE constraint on handle.
        raise HTTPException(400, f"Could not add player: {e}") from e

    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/players/{player_id}/edit", response_class=HTMLResponse)
async def edit_player_form(player_id: int, request: Request):
    session = _require_session(request)
    conn = request.app.state.db
    templates = request.app.state.templates

    player = conn.execute(
        """
        SELECT id, handle, display_name, initial_streak, initial_streak_set_at
        FROM players WHERE id = ?
        """,
        (player_id,),
    ).fetchone()
    if player is None:
        raise HTTPException(404, "player not found")

    locations = conn.execute(
        "SELECT location_id, slug FROM player_locations WHERE player_id = ? ORDER BY location_id",
        (player_id,),
    ).fetchall()
    locations_text = "\n".join(f"{r['location_id']},{r['slug']}" for r in locations)

    return templates.TemplateResponse(
        "edit_player.html",
        {
            "request": request,
            "player": dict(player),
            "locations_text": locations_text,
            "csrf_token": auth.csrf_token_for(session),
        },
    )


@router.post("/admin/players/{player_id}")
async def update_player(
    player_id: int,
    request: Request,
    handle: str = Form(...),
    display_name: str = Form(""),
    initial_streak: int = Form(0),
    locations: str = Form(""),
):
    _require_session(request)
    conn = request.app.state.db

    handle = handle.strip()
    if not handle:
        raise HTTPException(400, "handle is required")

    parsed_locations = _parse_locations(locations)
    if not parsed_locations:
        raise HTTPException(
            400,
            "At least one location is required, formatted as 'id,slug' per line.",
        )

    existing = conn.execute(
        "SELECT initial_streak, initial_streak_set_at FROM players WHERE id = ?",
        (player_id,),
    ).fetchone()
    if existing is None:
        raise HTTPException(404, "player not found")

    new_streak = max(0, int(initial_streak))
    today = datetime.now(timezone.utc).date().isoformat()

    # Reset the baseline date only when the streak value actually changes,
    # so editing display name / locations doesn't silently reset the 30-day clock.
    if new_streak != (existing["initial_streak"] or 0):
        new_streak_set_at = today if new_streak > 0 else None
    else:
        new_streak_set_at = existing["initial_streak_set_at"]

    new_locs = {loc_id: slug for loc_id, slug in parsed_locations}
    old_locs = {
        r["location_id"]: r["slug"]
        for r in conn.execute(
            "SELECT location_id, slug FROM player_locations WHERE player_id = ?",
            (player_id,),
        ).fetchall()
    }
    to_delete = [lid for lid in old_locs if lid not in new_locs]
    to_upsert = [(lid, slug) for lid, slug in new_locs.items() if old_locs.get(lid) != slug]

    try:
        with transaction(conn):
            conn.execute(
                """
                UPDATE players
                SET handle = ?, display_name = ?, initial_streak = ?, initial_streak_set_at = ?
                WHERE id = ?
                """,
                (
                    handle,
                    display_name.strip() or None,
                    new_streak,
                    new_streak_set_at,
                    player_id,
                ),
            )
            if to_delete:
                placeholders = ",".join("?" * len(to_delete))
                conn.execute(
                    f"DELETE FROM player_locations WHERE player_id = ? AND location_id IN ({placeholders})",
                    [player_id, *to_delete],
                )
            for loc_id, slug in to_upsert:
                conn.execute(
                    """
                    INSERT INTO player_locations (player_id, location_id, slug) VALUES (?, ?, ?)
                    ON CONFLICT(player_id, location_id) DO UPDATE SET slug = excluded.slug
                    """,
                    (player_id, loc_id, slug),
                )
    except Exception as e:
        raise HTTPException(400, f"Could not update player: {e}") from e

    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/players/{player_id}/delete")
async def delete_player(player_id: int, request: Request):
    _require_session(request)
    conn = request.app.state.db
    with transaction(conn):
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/refresh")
async def manual_refresh(request: Request, background: BackgroundTasks):
    _require_session(request)
    cfg = request.app.state.config
    conn = request.app.state.db

    async def run():
        await poller.poll_all(conn, cfg.poll)

    background.add_task(run)
    return RedirectResponse("/admin?refreshed=1", status_code=303)


# ---------- helpers ----------

def _parse_locations(raw: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",", 1)]
        if len(parts) != 2:
            raise HTTPException(400, f"Bad location line: {line!r}")
        try:
            loc_id = int(parts[0])
        except ValueError as e:
            raise HTTPException(400, f"Location id must be integer in {line!r}") from e
        slug = parts[1].strip().lower()
        if not slug:
            raise HTTPException(400, f"Slug missing in {line!r}")
        if loc_id in seen:
            continue
        seen.add(loc_id)
        out.append((loc_id, slug))
    return out
