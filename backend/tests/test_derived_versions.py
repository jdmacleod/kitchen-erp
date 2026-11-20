"""`kerp migrate` rebuilds prices and recipe costs a new CONVERT_VERSION made stale (#437)."""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from app.core.db import dispose_engine, get_sessionmaker
from app.services import derived_versions, recipe_costing
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
    computed = dict(await owner_conn.fetch("SELECT id, computed_at FROM recipe_cost_snapshot"))
    # A deploy that brings a new version finds rows the previous version wrote.
    await owner_conn.execute("UPDATE price_norm SET convert_version = '1'")
    await dispose_engine()

    out = _kerp("migrate")
    assert f"Unit conversion is now version {CONVERT_VERSION}" in out
    assert "normalized with version 1." in out
    assert "recomputed 3 normalizations and costs for 1 recipes" in out
    versions = await owner_conn.fetch("SELECT DISTINCT convert_version FROM price_norm")
    assert [r["convert_version"] for r in versions] == [CONVERT_VERSION]
    # Recomputed in place: no snapshot, and so no cost history, is dropped.
    after = dict(await owner_conn.fetch("SELECT id, computed_at FROM recipe_cost_snapshot"))
    assert after.keys() == computed.keys()
    assert all(after[i] > computed[i] for i in computed)
    figures = await cost(admin_client, porridge["id"])
    assert (figures["totals"], figures["completeness"]) == (
        before["totals"],
        before["completeness"],
    )

    out = _kerp("migrate")  # nothing stale now: nothing rebuilt
    assert "Rebuilding" not in out
    await dispose_engine()


async def test_a_rebuild_stopped_part_way_is_found_again(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    await porridge_pantry(admin_client)
    await indexed(admin_client, recipes_repo, "harbor-porridge.cook", PORRIDGE)
    await owner_conn.execute("UPDATE price_norm SET convert_version = '1'")

    async def stops(*_args, **_kw) -> int:
        raise RuntimeError("the deploy stopped")

    monkeypatch.setattr(recipe_costing, "recompute_recipe_core", stops)
    async with get_sessionmaker()() as db:
        with pytest.raises(RuntimeError):
            await derived_versions.refresh_if_stale(db, print)
    # Nothing was kept, so the next migrate still sees the old version.
    assert await owner_conn.fetchval("SELECT count(*) FROM price_norm") == 3
    assert (
        await owner_conn.fetchval("SELECT count(*) FROM price_norm WHERE convert_version = '1'")
        == 3
    )

    monkeypatch.undo()
    async with get_sessionmaker()() as db:
        assert await derived_versions.refresh_if_stale(db, print)
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM price_norm WHERE convert_version = $1", CONVERT_VERSION
        )
        == 3
    )


async def test_migrate_recomputes_current_costs_of_an_older_costing_version_once(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    await porridge_pantry(admin_client)
    await indexed(admin_client, recipes_repo, "harbor-porridge.cook", PORRIDGE)
    recipes_repo.edit("harbor-porridge.cook", "\nFinish with @dates{2}.\n")
    recipes_repo.commit("more dates")
    r = await admin_client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200, r.text
    # A deploy that brings costing version 1 finds snapshots version 0 wrote.
    await owner_conn.execute("UPDATE recipe_cost_snapshot SET costing_version = 0")
    before = {
        r["id"]: (r["computed_at"], r["current"])
        for r in await owner_conn.fetch(
            "SELECT s.id, s.computed_at, s.content_hash = r.content_hash AS current"
            " FROM recipe_cost_snapshot s JOIN recipe r ON r.id = s.recipe_id"
        )
    }
    assert sorted(current for _, current in before.values()) == [False, True]
    await dispose_engine()

    out = _kerp("migrate")
    assert f"Recipe costing is now version {recipe_costing.COSTING_VERSION}" in out
    assert "recomputed costs for 1 recipes" in out
    after = {
        r["id"]: (r["computed_at"], r["costing_version"])
        for r in await owner_conn.fetch(
            "SELECT id, computed_at, costing_version FROM recipe_cost_snapshot"
        )
    }
    for snapshot_id, (computed_at, current) in before.items():
        if current:  # the current content is rebuilt and stamped
            assert after[snapshot_id][0] > computed_at
            assert after[snapshot_id][1] == recipe_costing.COSTING_VERSION
        else:  # history is a fact of its time, left alone
            assert after[snapshot_id] == (computed_at, 0)

    out = _kerp("migrate")  # it settles: nothing is rebuilt the second time
    assert "Rebuilding" not in out
    await dispose_engine()
