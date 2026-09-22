"""Every migration is reversible: downgrade to base and back leaves the schema intact."""

import asyncpg

from tests.conftest import run_alembic


async def test_downgrade_and_upgrade_round_trip(owner_conn: asyncpg.Connection):
    run_alembic("downgrade", "base")
    tables = {
        r["tablename"]
        for r in await owner_conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    assert not ({"app_user", "api_token", "session", "idempotency_key"} & tables)
    exts = {r["extname"] for r in await owner_conn.fetch("SELECT extname FROM pg_extension")}
    assert "pg_trgm" not in exts
    run_alembic("upgrade", "head")
    tables = {
        r["tablename"]
        for r in await owner_conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    assert {"app_user", "api_token", "session", "idempotency_key"} <= tables
    # The round trip recreated the unit table empty; later tests expect the seed.
    from app.core.db import get_sessionmaker
    from app.services.units import seed_units

    async with get_sessionmaker()() as db:
        assert await seed_units(db) == 16
