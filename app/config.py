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


class ServerConfig(BaseModel):
    trusted_proxy_hops: int = 1
    forwarded_allow_ips: str = "*"


class AppConfig(BaseModel):
    admin: AdminConfig
    session: SessionConfig
    poll: PollConfig = Field(default_factory=PollConfig)
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
