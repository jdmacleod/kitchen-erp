"""Migration 0071: recipe references and what a recipe makes, down and up again (#526).

A database with a recipe-reference line goes down to 0070, where the line turns
back into an unmatched one and loses its path, and up again, where the new
columns are back and empty until a `--reparse` rescan (tested in
`test_recipe_refs_index`). Every recipe is invented.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from tests.conftest import run_alembic

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


async def _engine_reset() -> None:
    from app.core.db import dispose_engine

    await dispose_engine()


async def _recipe(conn, path: str, **extra) -> uuid.UUID:
    rid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO recipe (id, path, title, content_hash, dirty, status, last_indexed_at,"
        " last_seen_at, makes_qty, makes_unit, makes_text)"
        " VALUES ($1, $2, $3, $4, false, 'ok', $5, $5, $6, $7, $8)",
        rid,
        path,
        path.removesuffix(".cook"),
        "0" * 40,
        T0,
        extra.get("makes_qty"),
        extra.get("makes_unit"),
        extra.get("makes_text"),
    )
    return rid


async def test_reference_rows_go_down_to_unmatched_and_the_columns_come_back(owner_conn):
    syrup = await _recipe(
        owner_conn, "lantern-syrup.cook", makes_qty=500, makes_unit="ml", makes_text="500 ml"
    )
    drink = await _recipe(owner_conn, "harbor-sour.cook")
    await owner_conn.execute(
        "INSERT INTO recipe_ingredient (id, recipe_id, seq, raw_name, name_norm, qty_kind, qty,"
        " unit, resolution, negligible, yield_mode, ref_path, ref_recipe_id)"
        " VALUES ($1, $2, 1, 'lantern-syrup', 'lantern syrup', 'number', 1, 'fl_oz', 'recipe',"
        " false, 'auto', './lantern-syrup', $3)",
        uuid.uuid4(),
        drink,
        syrup,
    )

    run_alembic("downgrade", "0070")
    await _engine_reset()
    try:
        row = await owner_conn.fetchrow("SELECT resolution, negligible FROM recipe_ingredient")
        assert (row["resolution"], row["negligible"]) == ("unmatched", False)
        columns = {
            r["column_name"]
            for r in await owner_conn.fetch(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name IN ('recipe', 'recipe_ingredient', 'ingredient',"
                " 'recipe_cost_line', 'recipe_cost_snapshot')"
            )
        }
        assert not columns & {
            "makes_qty",
            "makes_unit",
            "makes_text",
            "ref_path",
            "ref_recipe_id",
            "made_by_recipe_id",
            "source_recipe_id",
            "source_partial",
            "lines_partial",
        }
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()

    row = await owner_conn.fetchrow(
        "SELECT resolution, ref_path, ref_recipe_id FROM recipe_ingredient"
    )
    assert (row["resolution"], row["ref_path"], row["ref_recipe_id"]) == ("unmatched", None, None)
    assert await owner_conn.fetchval("SELECT makes_qty FROM recipe WHERE id = $1", syrup) is None
    await owner_conn.execute(
        "UPDATE recipe_ingredient SET resolution = 'recipe', ref_path = './lantern-syrup'"
    )
