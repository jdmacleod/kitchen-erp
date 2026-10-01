"""Read the runtime role's privileges straight from the catalogs, for comparison."""

from __future__ import annotations

import asyncpg

from app.core.grants import APP_ROLE

RELATIONS = """
    SELECT c.relname, a.privilege_type
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(c.relacl) a
    WHERE n.nspname = 'public' AND a.grantee = $1::regrole
"""
COLUMNS = """
    SELECT c.relname, att.attname, a.privilege_type
    FROM pg_attribute att
    JOIN pg_class c ON c.oid = att.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL aclexplode(att.attacl) a
    WHERE n.nspname = 'public' AND a.grantee = $1::regrole
"""
DEFAULTS = """
    SELECT d.defaclobjtype, a.privilege_type
    FROM pg_default_acl d
    CROSS JOIN LATERAL aclexplode(d.defaclacl) a
    WHERE a.grantee = $1::regrole
"""


async def privileges(conn: asyncpg.Connection) -> dict[str, set[tuple]]:
    """Every table, column, default and schema privilege the runtime role holds."""
    return {
        "relations": {tuple(r) for r in await conn.fetch(RELATIONS, APP_ROLE)},
        "columns": {tuple(r) for r in await conn.fetch(COLUMNS, APP_ROLE)},
        "defaults": {tuple(r) for r in await conn.fetch(DEFAULTS, APP_ROLE)},
        "schema": {
            (
                "public",
                await conn.fetchval("SELECT has_schema_privilege($1, 'public', 'USAGE')", APP_ROLE),
            )
        },
    }
