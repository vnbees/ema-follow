from __future__ import annotations

import json
import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_cors(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return ["*"]
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in raw.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/coins_signal"
    signal_api_key: str = ""
    cors_origins_raw: str = "*"
    port: int = 8000

    @property
    def cors_origins(self) -> list[str]:
        return _parse_cors(self.cors_origins_raw or os.getenv("CORS_ORIGINS", "*"))

    @property
    def async_database_url(self) -> str:
        url = self.database_url or os.getenv("DATABASE_URL", "")
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    # Map Railway-style env names onto settings fields.
    data: dict[str, str] = {}
    if os.getenv("DATABASE_URL"):
        data["database_url"] = os.environ["DATABASE_URL"]
    if os.getenv("SIGNAL_API_KEY"):
        data["signal_api_key"] = os.environ["SIGNAL_API_KEY"]
    if os.getenv("CORS_ORIGINS"):
        data["cors_origins_raw"] = os.environ["CORS_ORIGINS"]
    if os.getenv("PORT"):
        data["port"] = os.environ["PORT"]
    return Settings(**data)


settings = get_settings()
