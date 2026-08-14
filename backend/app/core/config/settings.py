from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # --- identity ---
    project_name: str = "Augur"
    project_slug: str = "augur"
    environment: Literal["local", "dev", "prod"] = "local"
    debug: bool = False

    # --- http ---
    base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"
    cors_origins: list[str] = ["http://localhost:5173"]

    # --- database ---
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/augur"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- auth (Voidauth OIDC) ---
    oidc_issuer: str = "https://auth.elysium-archive.com"
    oidc_client_id: str = "augur"
    oidc_client_secret: str = ""
    oidc_scopes: list[str] = ["openid", "profile", "email", "groups"]
    oidc_redirect_path: str = "/api/auth/callback"
    oidc_groups_claim: str = "groups"
    oidc_admin_group: str = "augur-admins"

    # Session cookie signing. MUST be overridden outside local.
    session_secret: str = ""
    session_cookie_name: str = "augur_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 14

    # --- AI gateway ---
    ai_config_path: str = "config/ai_routing.yaml"
    anthropic_api_key: str = ""
    ai_request_timeout_seconds: int = 120
    # Augur's own circuit breaker, not the provider's. It exists to stop a
    # runaway loop draining an account overnight, not to ration normal play.
    #
    # 500k was far too tight: a turn costs roughly 10-20k with the tools and
    # system prompt, so a single evening's session hit the cap and stopped
    # working while the account still had credit. Sized now for a long session
    # with headroom, and it resets whenever the process does.
    ai_session_token_budget: int = 5_000_000

    # --- static frontend ---
    # Set in the image. Empty in local dev, where Vite serves the SPA itself.
    static_dir: str = ""

    # --- observability ---
    metrics_enabled: bool = True

    @field_validator("session_secret")
    @classmethod
    def _require_strong_secret(cls, v: str, info) -> str:
        env = info.data.get("environment", "local")
        if env != "local" and len(v) < 32:
            raise ValueError(
                "SESSION_SECRET must be at least 32 chars outside local environment"
            )
        return v or "local-insecure-do-not-use-outside-local"

    @property
    def sync_database_url(self) -> str:
        """Alembic needs a sync driver."""
        return str(self.database_url).replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
