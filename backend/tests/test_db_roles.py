"""Privilege scaffolding for the runtime role."""

import asyncpg
import pytest


async def test_app_role_cannot_create_tables(app_conn: asyncpg.Connection):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("CREATE TABLE should_not_exist (id int)")


async def test_app_role_has_dml_on_ordinary_tables(app_conn: asyncpg.Connection):
    await app_conn.execute("SELECT count(*) FROM app_user")
    await app_conn.execute("SELECT version_num FROM alembic_version")


async def test_reject_function_exists(owner_conn: asyncpg.Connection):
    found = await owner_conn.fetchval(
        "SELECT count(*) FROM pg_proc WHERE proname = 'kerp_reject_modification'"
    )
    assert found == 1


async def test_extensions_enabled(owner_conn: asyncpg.Connection):
    names = {r["extname"] for r in await owner_conn.fetch("SELECT extname FROM pg_extension")}
    assert {"postgis", "pg_trgm"} <= names
