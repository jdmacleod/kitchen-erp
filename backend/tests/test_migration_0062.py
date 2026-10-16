"""Migration 0062 adds the bunch unit and the per-bunch price status, and back (issue 371)."""

from __future__ import annotations

import asyncpg
import pytest

from tests.conftest import run_alembic


async def _constraint(conn: asyncpg.Connection, name: str) -> str:
    return await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1", name
    )


async def test_round_trip(owner_conn: asyncpg.Connection):
    assert await owner_conn.fetchval("SELECT dimension FROM unit WHERE code = 'bunch'") == "bunch"
    assert "no_bunch_weight" in await _constraint(owner_conn, "ck_price_norm_status")
    run_alembic("downgrade", "0061")
    try:
        assert await owner_conn.fetchval("SELECT count(*) FROM unit WHERE code = 'bunch'") == 0
        assert "bunch" not in await _constraint(owner_conn, "ck_unit_dimension")
        assert "no_bunch_weight" not in await _constraint(owner_conn, "ck_price_norm_status")
        with pytest.raises(asyncpg.CheckViolationError):
            await owner_conn.execute(
                "INSERT INTO unit (code, dimension, to_base_factor, system, aliases) "
                "VALUES ('bunch', 'bunch', 1, 'any', '{}')"
            )
    finally:
        run_alembic("upgrade", "head")
        # Pooled connections hold statements prepared against the old constraints.
        from app.core.db import dispose_engine

        await dispose_engine()
    row = await owner_conn.fetchrow("SELECT * FROM unit WHERE code = 'bunch'")
    assert row["to_base_factor"] == 1 and list(row["aliases"]) == ["bunches", "bnch"]
