from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class AdminConfig(BaseModel):
    username: str
    password_bcrypt: str

    @field_validator("password_bcrypt")
    @classmethod
    def _check_hash(cls, v: str) -> str:
        if not v.startswith(("$2a$", "$2b$", "$2y$")):
            raise ValueError(
                "admin.password_bcrypt must be a bcrypt hash. "
                "Generate one with: python -m app.tools.hashpw"
            )
        return v


class SessionConfig(BaseModel):
    secret_key: str
    cookie_name: str = "atrk_session"
    max_age_hours: int = 24

    @field_validator("secret_key")
    @classmethod
    def _check_secret(cls, v: str) -> str:
        if len(v) < 32 or v.startswith("REPLACE_ME"):
            raise ValueError(
                "session.secret_key must be at least 32 characters of random data. "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v


class PollConfig(BaseModel):
    schedule: str = "daily"  # "daily" | "manual" | crontab
    hour_utc: int = 11
    request_timeout_sec: float = 20.0
    jitter_seconds: tuple[float, float] = (0.5, 2.0)
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )

    @field_validator("hour_utc")
    @classmethod
    def _check_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("poll.hour_utc must be in 0..23")
        return v


class BadgeConfig(BaseModel):
    """Where to read per-player badges from.

    The default is a community-run proxy in front of an official Activate badge
    API — the score pages we scrape carry no badge list at all. It is somebody's
    personal server, so the base URL is configuration: pointing at the upstream
    directly, or at a mirror, should never need a code change. Setting
    `enabled: false` skips the fetch entirely and leaves the dashboard's badge
    count running off the page's own trophyProgress tally.
    """

    enabled: bool = True
    api_base: str = "https://api.ryflix.ca/api/badges"

    # This leg gets its own spacing rather than sharing `poll.jitter_seconds`
    # (0.5-2.0s), which is tuned for a Cloudflare-fronted site that served 28
    # score pages back to back without complaint. The badge proxy is somebody's
    # personal server and rate-limits far harder: observed live, five handles
    # landed and every one after was refused with an instant 429, still refusing
    # 16 seconds after the first request. ~15s apart keeps a dozen handles
    # inside 5/minute and puts the whole leg under three minutes, which a daily
    # poll doesn't notice.
    spacing_seconds: tuple[float, float] = (12.0, 18.0)

    # A 429 is waited out rather than lost. Without this the handles that lose
    # are the ones at the back of a stable queue, every night, which is how a
    # player's badge row goes ten days stale while the dashboard shows a count
    # that was true when it was written.
    max_retries: int = 3
    backoff_seconds: float = 30.0
    # But never wait longer than this in one go, whatever `Retry-After` asks
    # for. A poll holds a global lock, so an hour-long sleep would block the
    # scheduler and the admin's Refresh button behind it; tomorrow's poll is a
    # better place to try again than the inside of today's.
    max_wait_seconds: float = 120.0

    @field_validator("spacing_seconds")
    @classmethod
    def _check_spacing(cls, v: tuple[float, float]) -> tuple[float, float]:
        lo, hi = v
        if lo < 0 or hi < lo:
            raise ValueError("badges.spacing_seconds must be (lo, hi) with 0 <= lo <= hi")
        return v

    @field_validator("max_retries")
    @classmethod
    def _check_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError("badges.max_retries must be >= 0")
        return v

    @field_validator("backoff_seconds", "max_wait_seconds")
    @classmethod
    def _check_positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError("badge backoff/wait seconds must be >= 0")
        return v


class ServerConfig(BaseModel):
    trusted_proxy_hops: int = 1
    forwarded_allow_ips: str = "*"


class AppConfig(BaseModel):
    admin: AdminConfig
    session: SessionConfig
    poll: PollConfig = Field(default_factory=PollConfig)
    badges: BadgeConfig = Field(default_factory=BadgeConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    config_path = Path(path or os.environ.get("ACTIVATETRACKER_CONFIG", "config.yaml"))
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Config file not found at {config_path}. "
            "Copy config.yaml.example to config.yaml and fill in real values."
        )
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)


def db_path() -> Path:
    return Path(os.environ.get("ACTIVATETRACKER_DB", "data/tracker.db"))
