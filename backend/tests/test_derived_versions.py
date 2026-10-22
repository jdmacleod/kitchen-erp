"""`kerp migrate` rebuilds prices and recipe costs a new CONVERT_VERSION made stale (#437)."""

from __future__ import annotations

import asyncpg
import httpx

from app.core.db import dispose_engine
from app.units.convert import CONVERT_VERSION
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_cli_migrate import _kerp
from tests.test_recipe_costing import PORRIDGE, cost, indexed, porridge_pantry


async def test_migrate_rebuilds_prices_and_costs_from_an_older_conversion(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    await porridge_pantry(admin_client)
    porridge = await indexed(admin_client, recipes_repo, "harbor-porridge.cook", PORRIDGE)
    before = await cost(admin_client, porridge["id"])
    snapshots = set(await owner_conn.fetch("SELECT id FROM recipe_cost_snapshot"))
    # A deploy that brings a new version finds rows the previous version wrote.
    await owner_conn.execute("UPDATE price_norm SET convert_version = '1'")
    await dispose_engine()

    out = _kerp("migrate")
    assert f"Unit conversion is now version {CONVERT_VERSION}" in out
    assert "normalized with version 1." in out
    assert "recomputed 3 normalizations and costs for 1 recipes" in out
    versions = await owner_conn.fetch("SELECT DISTINCT convert_version FROM price_norm")
    assert [r["convert_version"] for r in versions] == [CONVERT_VERSION]
    assert not snapshots & set(await owner_conn.fetch("SELECT id FROM recipe_cost_snapshot"))
    after = await cost(admin_client, porridge["id"])
    assert (after["totals"], after["completeness"]) == (before["totals"], before["completeness"])

    out = _kerp("migrate")  # nothing stale now: nothing rebuilt
    assert "Rebuilding" not in out
    await dispose_engine()
