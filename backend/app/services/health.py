"""Health checks: database, migration head, model server, ingest queue depth."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.schemas.health import Check, HealthOut

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def expected_migration_head() -> str | None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    return heads[0] if heads else None


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
