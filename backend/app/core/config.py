"""Configuration from environment variables. Every variable is documented in .env.example."""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Build identity sentinels. An image built without the args genuinely does not
# know what it is, so it says so rather than guessing. Named here because three
# places compare against them: the defaults below, the `is_dev` derivation in the
# health router, and `kerp --version`.
DEV_VERSION = "dev"
UNKNOWN_COMMIT = "unknown"

# `git describe` appends this when HEAD is not the tag itself: `v1.2.3-5-gabc1234`
# is five commits past v1.2.3.
_COMMITS_PAST_THE_TAG = re.compile(r"-\d+-g[0-9a-f]+$")


def is_dev_build(version: str, commit: str) -> bool:
    """True for anything that is not a clean, tagged release.

    `git describe --tags --always --dirty` returns a bare tag name only when HEAD
    is exactly that tag on a clean tree. Every other output says why it is not a
    release: `-dirty` for uncommitted changes, `-5-gabc1234` for commits since the
    tag, and the abbreviated sha itself when `--always` fell back because no tag
    exists at all. That last case is the one this repository is in today, and
    checking only the sentinels would have called it a release.

    Decided here rather than in the frontend so the rule lives in one language
    instead of being re-derived against the sentinels in TypeScript.
    """
    if version == DEV_VERSION or commit == UNKNOWN_COMMIT:
        return True  # built without the args; the image does not know what it is
    if version.endswith("-dirty") or _COMMITS_PAST_THE_TAG.search(version):
        return True
    return version.startswith(commit)  # `--always` fallback: there is no tag


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    migration_database_url: str

    # Baked into the image by `make up` and by CI, not read from `.env`: these are
    # build-time inputs to `docker build`, not runtime configuration. Compose has no
    # command substitution, so a bare `docker compose up --build` leaves the
    # defaults in place.
    build_version: str = DEV_VERSION
    build_commit: str = UNKNOWN_COMMIT

    ollama_base_url: str = "http://host.docker.internal:11434"
    llm_model: str = "gpt-oss:20b"
    ocr_adapter: str = "tesseract"
    # Ingest (Phase 2C). OCR adapters are tried in order; `client` uses text sent
    # with the upload, `tesseract` runs the binary in the worker image. As an
    # environment variable this is a JSON list: OCR_ADAPTERS='["client","tesseract"]'.
    ocr_adapters: list[str] = ["client", "tesseract"]
    tesseract_command: str = "tesseract"
    receipt_max_bytes: int = 32 * 1024 * 1024
    ingest_max_attempts: int = 5
    ingest_backoff_base_seconds: float = 5.0
    ingest_backoff_factor: float = 4.0
    ingest_backoff_max_seconds: float = 300.0  # cap while the model server is unreachable
    ingest_location_radius_m: int = 300
    llm_max_retries: int = 2  # extra attempts when model output fails schema validation
    llm_timeout_seconds: float = 120.0
    enable_overpass: bool = False
    enable_nominatim: bool = False

    tiles_path: str = "/data/tiles"
    receipts_path: str = "/data/receipts"
    recipes_path: str = "/data/recipes"  # read-only mount of the cooklang-recipes checkout

    household_timezone: str = "America/Los_Angeles"
    currency: str = "USD"
    ingest_lock_timeout_seconds: int = 600

    # Resolution ladder.
    price_outlier_factor: Decimal = Decimal("2")  # flag when implied unit price is off by this
    alias_fuzzy_threshold: Decimal = Decimal("0.35")  # trigram similarity for fuzzy alias hints
    llm_shortlist_size: int = 10
    llm_ranker_enabled: bool = True  # ask the model to rank shortlists during resolution
    price_plausibility_factor: Decimal = Decimal("5")  # shortlist narrowing where history exists

    # Price staleness by perishability (days); stale prices are shown but marked.
    stale_days_fresh: int = 14
    stale_days_refrigerated: int = 45
    stale_days_shelf_stable: int = 120

    session_ttl_days: int = 30
    idempotency_ttl_hours: int = 24
    cookie_name: str = "kerp_session"
    cookie_secure: bool = False
    model_server_timeout_seconds: float = 2.0

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
