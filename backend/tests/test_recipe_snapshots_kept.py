"""What a recipe's cost snapshots and pins survive (07, 3A): criteria 4 and 5.

A file that stops parsing keeps its last good snapshot as well as its rows
(criterion 4), and a pure move keeps the pins and the snapshots along with the
id (criterion 5). Recipes, products and prices are invented; the geography is
synthetic.
"""

from __future__ import annotations

import asyncpg
import httpx

from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_recipe_costing import barley_only, cost, indexed, lines_by_name, rescan

BOWL = """---
title: Quay bowl
servings: 2
---

Serve @barley{200%g}.
"""

# The same bowl with a brace never closed.
BOWL_BROKEN = """---
title: Quay bowl
servings: 2
---

Serve @barley{200%g} with @barley{100%g
"""


async def snapshot_rows(conn: asyncpg.Connection, recipe_id: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, content_hash, consumed_cost, basket_cost, computed_at "
        "FROM recipe_cost_snapshot WHERE recipe_id = $1::uuid ORDER BY computed_at",
        recipe_id,
    )
    return [dict(r) for r in rows]


async def history_ids(client: httpx.AsyncClient, recipe_id: str) -> list[str]:
    r = await client.get(f"/api/v1/recipes/{recipe_id}/cost/history")
    assert r.status_code == 200, r.text
    return [h["id"] for h in r.json()["items"]]


async def test_criterion_4_a_file_that_stops_parsing_keeps_its_snapshot(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    await barley_only(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "quay-bowl.cook", BOWL)
    good = await cost(admin_client, recipe["id"])
    assert good["totals"]["consumed_cost"] == "0.8000"
    before = await snapshot_rows(owner_conn, recipe["id"])
    assert [str(row["id"]) for row in before] == [good["id"]]

    recipes_repo.write("quay-bowl.cook", BOWL_BROKEN)
    recipes_repo.commit("broken")
    await rescan(admin_client)
    broken = (await admin_client.get(f"/api/v1/recipes/{recipe['id']}")).json()
    assert broken["status"] == "parse_error"
    assert broken["content_hash"] != good["content_hash"]

    # The good snapshot is the same row with the same figures, keyed by the old hash.
    after = await snapshot_rows(owner_conn, recipe["id"])
    kept = {row["id"]: row for row in after}[before[0]["id"]]
    assert kept == before[0]
    assert kept["content_hash"] == good["content_hash"]
    assert good["id"] in await history_ids(admin_client, recipe["id"])


async def test_criterion_5_a_pure_move_keeps_the_pins_and_the_snapshots(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    pantry = await barley_only(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "quay-bowl.cook", BOWL)
    r = await admin_client.put(
        f"/api/v1/recipes/{recipe['id']}/pins/barley", json={"product_id": pantry["barley"]["id"]}
    )
    assert r.status_code == 200, r.text
    pins = r.json()["pins"]
    assert [p["name_norm"] for p in pins] == ["barley"]
    costed = await cost(admin_client, recipe["id"])
    assert lines_by_name(costed)["barley"]["pinned"] is True
    snapshots = await snapshot_rows(owner_conn, recipe["id"])
    history = await history_ids(admin_client, recipe["id"])
    assert history == [costed["id"]]

    recipes_repo.move("quay-bowl.cook", "bowls/quay-bowl.cook")
    recipes_repo.commit("move")
    await rescan(admin_client)

    moved = (await admin_client.get(f"/api/v1/recipes/{recipe['id']}")).json()
    assert moved["id"] == recipe["id"] and moved["path"] == "bowls/quay-bowl.cook"
    assert moved["content_hash"] == recipe["content_hash"]
    assert moved["pins"] == pins
    assert await snapshot_rows(owner_conn, recipe["id"]) == snapshots
    assert await history_ids(admin_client, recipe["id"]) == history
    again = await cost(admin_client, recipe["id"])
    assert again["id"] == costed["id"] and again["totals"] == costed["totals"]
    assert lines_by_name(again)["barley"]["pinned"] is True
