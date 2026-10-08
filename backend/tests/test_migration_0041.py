"""Migration 0041 tidies the names and brands of products saved before (2P).

Seeded at 0040 by writing signs straight into the table (the model would tidy them):
one product with a trademark sign in its name and brand, one with a curly apostrophe
and a non-breaking space, and one already plain. The upgrade tidies the first two;
a downgrade restores exactly their originals. Every name is invented.
"""

from __future__ import annotations

import uuid

from tests.conftest import run_alembic
from tests.pricebook_helpers import make_product

TM, REG, RSQUO, NBSP = chr(0x2122), chr(0x00AE), chr(0x2019), chr(0x00A0)


async def _engine_reset() -> None:
    from app.core.db import dispose_engine

    await dispose_engine()


async def _row(conn, product_id: str) -> tuple[str, str | None]:
    row = await conn.fetchrow(
        "SELECT name, brand FROM product WHERE id = $1", uuid.UUID(product_id)
    )
    return row["name"], row["brand"]


async def test_names_are_tidied_and_restored(admin_client, owner_conn):
    marked = await make_product(admin_client, "Plum jam", "Plum Jam")
    curly = await make_product(admin_client, "Kelp chips", "Kelp Chips")
    plain = await make_product(admin_client, "Barley", "Pearl barley bag")
    originals = {
        marked["id"]: (f"Fernhill{TM} Plum Jam", f"Fernhill{REG}"),
        curly["id"]: (f"Moss Bay{RSQUO}s{NBSP}Kelp  Chips", None),
    }

    run_alembic("downgrade", "0040")
    await _engine_reset()
    try:
        for product_id, (name, brand) in originals.items():
            await owner_conn.execute(
                "UPDATE product SET name = $2, brand = $3 WHERE id = $1",
                uuid.UUID(product_id),
                name,
                brand,
            )

        run_alembic("upgrade", "0041")
        assert await _row(owner_conn, marked["id"]) == ("Fernhill Plum Jam", "Fernhill")
        assert await _row(owner_conn, curly["id"]) == ("Moss Bay's Kelp Chips", None)
        assert await _row(owner_conn, plain["id"]) == ("Pearl barley bag", None)
        kept = await owner_conn.fetchval("SELECT count(*) FROM product_name_before_0041")
        assert kept == 2

        run_alembic("downgrade", "0040")
        for product_id, original in originals.items():
            assert await _row(owner_conn, product_id) == original
        assert await _row(owner_conn, plain["id"]) == ("Pearl barley bag", None)
        table = await owner_conn.fetchval("SELECT to_regclass('product_name_before_0041')")
        assert table is None
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()
