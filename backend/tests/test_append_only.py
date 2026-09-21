"""Non-negotiable 4: append-only tables cannot be updated or deleted by anyone."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import pytest

from app.core.ids import new_id

TABLES = ("price_observation", "price_observation_void", "ingest_stage_result")


async def _seed_observation(owner: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    user = new_id()
    await owner.execute(
        "INSERT INTO app_user (id, email, display_name, password_hash, role) "
        "VALUES ($1, 'seed@example.com', 'Seed', 'x', 'admin')",
        user,
    )
    ing = new_id()
    await owner.execute(
        "INSERT INTO ingredient (id, name, canonical_unit) VALUES ($1, 'Seed ingredient', 'g')", ing
    )
    prod = new_id()
    await owner.execute(
        "INSERT INTO product (id, ingredient_id, name) VALUES ($1, $2, 'Seed product')", prod, ing
    )
    place = new_id()
    await owner.execute(
        "INSERT INTO place (id, lat, lon, geom) VALUES ($1, 33.5, -120.5, "
        "ST_SetSRID(ST_MakePoint(-120.5, 33.5), 4326)::geography)",
        place,
    )
    vendor = new_id()
    await owner.execute(
        "INSERT INTO vendor (id, name, kind, price_scope) VALUES ($1, 'Seed vendor', "
        "'stand', 'location')",
        vendor,
    )
    loc = new_id()
    await owner.execute(
        "INSERT INTO vendor_location (id, vendor_id, place_id, name) VALUES ($1, $2, $3, "
        "'Seed stand')",
        loc,
        vendor,
        place,
    )
    obs = new_id()
    await owner.execute(
        "INSERT INTO price_observation (id, product_id, vendor_location_id, observed_at, "
        "price, qty, "
        "unit, is_promo, source, entered_by) VALUES ($1, $2, $3, $4, 3.99, 1, 'each', "
        "false, 'shelf', $5)",
        obs,
        prod,
        loc,
        datetime.now(UTC),
        user,
    )
    return obs, user


@pytest.mark.parametrize("table", TABLES)
async def test_app_role_lacks_update_and_delete(app_conn: asyncpg.Connection, table: str):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute(f"UPDATE {table} SET id = id")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute(f"DELETE FROM {table}")


async def test_owner_is_stopped_by_trigger(owner_conn: asyncpg.Connection):
    obs, user = await _seed_observation(owner_conn)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
        await owner_conn.execute("UPDATE price_observation SET price = 1 WHERE id = $1", obs)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
        await owner_conn.execute("DELETE FROM price_observation WHERE id = $1", obs)
    void = new_id()
    await owner_conn.execute(
        "INSERT INTO price_observation_void (id, observation_id, reason, voided_by) "
        "VALUES ($1, $2, 'test', $3)",
        void,
        obs,
        user,
    )
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
        await owner_conn.execute("DELETE FROM price_observation_void WHERE id = $1", void)
    assert Decimal(
        await owner_conn.fetchval("SELECT price FROM price_observation WHERE id = $1", obs)
    ) == Decimal("3.99")


async def test_price_norm_is_rebuildable_by_app_role(app_conn: asyncpg.Connection):
    # Derived table: full DML for the runtime role.
    await app_conn.execute("DELETE FROM price_norm")
    await app_conn.execute("TRUNCATE price_norm")


async def test_views_exist(app_conn: asyncpg.Connection):
    for view in ("price_current", "offer_latest", "offer_latest_regular", "ingredient_offer"):
        await app_conn.fetch(f"SELECT * FROM {view} LIMIT 1")
