"""Centralised configuration loaded from environment (.env in dev).

All settings are immutable at runtime: import Settings once in app startup.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_base_url: str = "http://localhost:8765"
    app_host: str = "127.0.0.1"
    app_port: int = 8765
    app_env: str = "dev"
    log_level: str = "INFO"

    # Storage
    db_path: str = "./data/repost.db"

    # Crypto — required
    fernet_key: str = Field(min_length=44)
    session_secret_key: str = Field(min_length=32)

    # VK auth — required for participant login
    vk_app_id: int
    vk_protected_key: str | None = None
    vk_oauth_scope: str = "wall,offline"
    vk_api_version: str = "5.199"

    # Phase 3+ (admin)
    admin_password: str | None = None

    # Phase 2+ (source polling)
    vk_source_group_id: int | None = None
    vk_service_token: str | None = None
    poll_interval_seconds: int = 60
    repost_window_seconds: int = 18_000
    repost_min_delay_seconds: int = 60
    repost_lognormal_median_seconds: int = 3_600
    repost_lognormal_sigma: float = 0.7
    task_missed_threshold_seconds: int = 21_600
    worker_batch_size: int = 50

    @field_validator("app_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def db_absolute_path(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_absolute_path}"

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.app_base_url}/auth/vk/complete"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
