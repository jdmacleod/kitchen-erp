"""Configuration from environment variables. Every variable is documented in .env.example."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    migration_database_url: str

    ollama_base_url: str = "http://host.docker.internal:11434"
    llm_model: str = "gpt-oss:20b"
    ocr_adapter: str = "tesseract"
    enable_overpass: bool = False
    enable_nominatim: bool = False

    tiles_path: str = "/data/tiles"
    receipts_path: str = "/data/receipts"
    recipes_path: str | None = None

    household_timezone: str = "America/Los_Angeles"
    currency: str = "USD"
    ingest_lock_timeout_seconds: int = 600

    session_ttl_days: int = 30
    idempotency_ttl_hours: int = 24
    cookie_name: str = "kerp_session"
    cookie_secure: bool = False
    model_server_timeout_seconds: float = 2.0

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
