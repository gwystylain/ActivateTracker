"""Session, password, CSRF, and login-throttle helpers."""
from __future__ import annotations

import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import AppConfig

LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 5


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


# ---------- session cookie ----------

def _serializer(cfg: AppConfig) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(cfg.session.secret_key, salt="atrk-session-v1")


def issue_session(cfg: AppConfig, username: str) -> tuple[str, str]:
    """Return (cookie_value, csrf_token) for a freshly authenticated user."""
    csrf = secrets.token_urlsafe(32)
    payload = {"u": username, "csrf": csrf, "iat": _now_ts()}
    cookie = _serializer(cfg).dumps(payload)
    return cookie, csrf


def read_session(cfg: AppConfig, cookie_value: str | None) -> dict | None:
    if not cookie_value:
        return None
    max_age = cfg.session.max_age_hours * 3600
    try:
        return _serializer(cfg).loads(cookie_value, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None


def csrf_token_for(session: dict | None) -> str:
    return (session or {}).get("csrf", "")


def csrf_ok(session: dict | None, submitted: str | None) -> bool:
    expected = csrf_token_for(session)
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)


# ---------- login throttle ----------

def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _ip_key(ip: str) -> str:
    # Hash the IP before persisting so a DB leak doesn't expose visitor IPs.
    return sha256(ip.encode("utf-8")).hexdigest()


def is_throttled(conn: sqlite3.Connection, ip: str, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=LOGIN_WINDOW_SECONDS)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM login_attempts WHERE ip = ? AND attempted_at > ?",
        (_ip_key(ip), cutoff),
    ).fetchone()
    return (row["n"] if row else 0) >= LOGIN_MAX_ATTEMPTS


def record_failure(conn: sqlite3.Connection, ip: str, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO login_attempts (ip, attempted_at) VALUES (?, ?)",
        (_ip_key(ip), now.isoformat()),
    )
    # Opportunistic prune of old rows (anything older than the window * 4).
    cutoff = (now - timedelta(seconds=LOGIN_WINDOW_SECONDS * 4)).isoformat()
    conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (cutoff,))


def clear_failures(conn: sqlite3.Connection, ip: str) -> None:
    conn.execute("DELETE FROM login_attempts WHERE ip = ?", (_ip_key(ip),))
