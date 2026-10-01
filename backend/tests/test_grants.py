"""Restore re-applies exactly the privileges the migrations give the runtime role.

`kerp restore` loads the dump with `--no-privileges`, so whatever
`app/core/grants.py` lists is all the runtime role gets back. This strips the
role's privileges the same way and checks the registry restores them.
"""

import asyncpg

from app.core.grants import APP_ROLE
from app.services.backup import reapply_grants
from tests.grants_helpers import privileges

STRIP = (
    f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}",
    f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {APP_ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {APP_ROLE}",
    f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}",
)


async def test_reapplied_grants_equal_migrated_grants(owner_conn: asyncpg.Connection):
    migrated = await privileges(owner_conn)
    try:
        for statement in STRIP:
            await owner_conn.execute(statement)
        assert await privileges(owner_conn) != migrated
        await reapply_grants()
        assert await privileges(owner_conn) == migrated
    finally:
        await reapply_grants()


async def test_vendor_suggestions_stay_decision_only_after_reapply(
    owner_conn: asyncpg.Connection,
):
    await reapply_grants()

    async def can(privilege: str) -> bool:
        return await owner_conn.fetchval(
            "SELECT has_table_privilege($1, 'vendor_suggestion', $2)", APP_ROLE, privilege
        )

    async def can_update(column: str) -> bool:
        return await owner_conn.fetchval(
            "SELECT has_column_privilege($1, 'vendor_suggestion', $2, 'UPDATE')", APP_ROLE, column
        )

    assert await can("INSERT")
    assert not await can("DELETE")
    assert await can_update("status")
    assert not await can_update("vendor_id")
