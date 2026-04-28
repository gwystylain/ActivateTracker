"""Security headers + CSRF middleware."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .auth import csrf_ok, read_session
from .config import AppConfig

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        h = response.headers
        h.setdefault("Content-Security-Policy", _CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault(
            "Permissions-Policy",
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()",
        )
        h.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        return response


class CsrfMiddleware(BaseHTTPMiddleware):
    """Verify CSRF token on state-changing requests.

    Token comes from the signed session cookie; forms must echo it back as
    a hidden ``csrf_token`` field. Requests without a session bypass the check
    (the underlying route is responsible for requiring a session itself).
    """

    def __init__(self, app, *, config: AppConfig):
        super().__init__(app)
        self.config = config

    async def dispatch(self, request: Request, call_next):
        if request.method in SAFE_METHODS:
            return await call_next(request)

        cookie = request.cookies.get(self.config.session.cookie_name)
        session = read_session(self.config, cookie)
        if session is not None:
            form = await _peek_form(request)
            submitted = form.get("csrf_token", "")
            if not csrf_ok(session, submitted):
                return Response("CSRF token missing or invalid", status_code=403)
        return await call_next(request)


async def _peek_form(request: Request) -> dict:
    """Parse the form body without consuming it for downstream handlers.

    Starlette's ``request.form()`` reads from the ASGI receive channel and
    caches nothing the route can re-use, so we drain the body once, parse
    it ourselves, and replace ``request._receive`` so the route handler
    sees the same bytes.
    """
    ctype = request.headers.get("content-type", "")
    if not ctype.startswith("application/x-www-form-urlencoded"):
        # Multipart and other content types: skip CSRF check at this layer.
        # Admin forms are all urlencoded, so this is fine.
        return {}

    body = await request.body()  # caches into request._body via Starlette internals

    # Replay the body for the next consumer.
    async def _replay():
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = _replay  # type: ignore[attr-defined]

    from urllib.parse import parse_qsl
    try:
        return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
    except UnicodeDecodeError:
        return {}


def client_ip(request: Request) -> str:
    """Best-effort client IP. Trusts X-Forwarded-For because uvicorn is started
    with --proxy-headers (so request.client.host already holds the forwarded IP
    when the proxy is trusted).
    """
    return request.client.host if request.client else "unknown"
