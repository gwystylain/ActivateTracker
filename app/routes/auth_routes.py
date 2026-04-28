"""Login / logout."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth
from ..security import client_ip

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = None):
    cfg = request.app.state.config
    templates = request.app.state.templates

    # Already logged in? Bounce to admin.
    session = auth.read_session(cfg, request.cookies.get(cfg.session.cookie_name))
    if session is not None:
        return RedirectResponse("/admin", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    cfg = request.app.state.config
    conn = request.app.state.db
    templates = request.app.state.templates

    ip = client_ip(request)
    if auth.is_throttled(conn, ip):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Too many attempts. Try again in a few minutes."},
            status_code=429,
        )

    expected_user = cfg.admin.username
    user_ok = hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
    pass_ok = auth.verify_password(password, cfg.admin.password_bcrypt)

    if not (user_ok and pass_ok):
        auth.record_failure(conn, ip)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password."},
            status_code=401,
        )

    auth.clear_failures(conn, ip)
    cookie_value, _csrf = auth.issue_session(cfg, expected_user)
    response = RedirectResponse("/admin", status_code=303)
    # Honour the proxy-forwarded scheme (uvicorn rewrites .scheme via --proxy-headers).
    # Behind nginx-proxy-manager this will be "https"; in a plain-HTTP dev setup
    # we drop the Secure flag so the cookie is actually sent.
    is_https = request.url.scheme == "https"
    response.set_cookie(
        cfg.session.cookie_name,
        cookie_value,
        max_age=cfg.session.max_age_hours * 3600,
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    cfg = request.app.state.config
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(cfg.session.cookie_name, path="/")
    return response
