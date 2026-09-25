"""Health checks: database, migration head, model server, ingest queue depth."""

from __future__ import annotations

import time
from functools import cache
from pathlib import Path

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.schemas.health import Check, HealthOut

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


@cache
def _scripts() -> ScriptDirectory:
    """The build's migration scripts. They never change at runtime, and /health is
    polled, so they are read from disk once per process."""
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def expected_migration_head() -> str | None:
    heads = _scripts().get_heads()
    return heads[0] if heads else None


# Each navigation section and the migration that built its tables. The client shows
# a section only when its feature is listed (docs/spec/09, "Phase gating"); a later
# phase adds its row here with the migration that introduces it.
FEATURE_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("catalog", "0003"),
    ("shop", "0005"),
)


def features(current_revision: str | None) -> list[str]:
    """The sections whose migrations the database has applied, in navigation order.

    Keyed to the database's revision rather than the build's head, so the list says
    what is actually migrated. An unknown or missing revision yields no features;
    the client then shows its Phase 1–2 defaults (D11).
    """
    if not current_revision:
        return []
    try:
        walk = _scripts().walk_revisions(base="base", head=current_revision)
        applied = {rev.revision for rev in walk}
    except CommandError:
        # A revision this build does not know, e.g. a newer database: say nothing
        # rather than guess. The migrations check already reports the mismatch.
        return []
    return [name for name, revision in FEATURE_MIGRATIONS if revision in applied]


async def check_database(db: AsyncSession) -> Check:
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        return Check(status="failed", detail={"error": type(exc).__name__})
    return Check(
        status="ok", detail={"latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    )


async def check_migrations(db: AsyncSession) -> Check:
    expected = expected_migration_head()
    try:
        current = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    except Exception as exc:
        return Check(status="failed", detail={"error": type(exc).__name__, "expected": expected})
    status = "ok" if current == expected else "failed"
    return Check(status=status, detail={"current": current, "expected": expected})


async def check_model_server() -> Check:
    settings = get_settings()
    url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=settings.model_server_timeout_seconds) as client:
            response = await client.get(url)
        if response.status_code != 200:
            return Check(status="degraded", detail={"http_status": response.status_code})
        names = [m.get("name") for m in response.json().get("models", []) if isinstance(m, dict)]
        loaded = settings.llm_model in names or any(
            str(n).split(":")[0] == settings.llm_model.split(":")[0] for n in names
        )
        return Check(status="ok", detail={"model": settings.llm_model, "model_present": loaded})
    except Exception as exc:
        return Check(
            status="degraded", detail={"error": type(exc).__name__, "model": settings.llm_model}
        )


async def check_ingest_queue(db: AsyncSession) -> Check:
    try:
        exists = (await db.execute(text("SELECT to_regclass('public.ingest_job')"))).scalar()
        if exists is None:
            return Check(status="ok", detail={"depth": 0, "note": "ingest not yet installed"})
        depth = (
            await db.execute(text("SELECT count(*) FROM ingest_job WHERE status = 'pending'"))
        ).scalar_one()
        return Check(status="ok", detail={"depth": int(depth)})
    except Exception as exc:
        return Check(status="failed", detail={"error": type(exc).__name__})


async def health(db: AsyncSession) -> HealthOut:
    checks = {
        "database": await check_database(db),
        "migrations": await check_migrations(db),
        "model_server": await check_model_server(),
        "ingest_queue": await check_ingest_queue(db),
    }
    if any(c.status == "failed" for c in checks.values()):
        overall = "failed"
    elif any(c.status == "degraded" for c in checks.values()):
        overall = "degraded"
    else:
        overall = "ok"
    return HealthOut(status=overall, checks=checks)
