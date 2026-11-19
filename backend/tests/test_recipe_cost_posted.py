"""A posted price as a recipe line's last resort (07, 3D and 27a; #523, ruling P1–P3).

Stores, products and recipes are invented; the geography is synthetic. A 1 kg
bag posted at $1.00 is $0.001000 per g, so the bowl's 200 g comes to $0.2000.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx

from app.core.db import dispose_engine
from tests.catalog_helpers import make_product as make_sibling_product
from tests.conftest import run_alembic
from tests.geo_helpers import NEAR_A, NEAR_B, make_home_base
from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_posted_prices import post_listing_price
from tests.test_recipe_costing import (
    BOWL,
    PORRIDGE,
    cost,
    days_ago,
    indexed,
    lines_by_name,
    market,
    recipe_by_path,
    recost,
)


def ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def bag(client: httpx.AsyncClient) -> dict:
    return await make_product(client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")


async def test_a_voided_or_closed_stores_posted_price_never_costs_a_line(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    closed = await make_location(admin_client, "Cove Grocer", "Cove Grocer")
    barley = await bag(admin_client)
    voided = await post_listing_price(loc, barley, "1.00")
    r = await admin_client.post(
        f"/api/v1/price-observations/{voided}/void", json={"reason": "test: wrong page"}
    )
    assert r.status_code == 200, r.text
    await post_listing_price(closed, barley, "1.00")
    await owner_conn.execute(
        "UPDATE vendor_location SET active = false WHERE id = $1", uuid.UUID(closed["id"])
    )
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    line = lines_by_name(await cost(admin_client, recipe["id"]))["barley"]
    assert line["status"] == "unpriced" and line["price"] is None


async def test_every_basis_takes_the_latest_posted_price_and_a_stale_one_is_marked(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    barley = await bag(admin_client)
    await post_listing_price(loc, barley, "1.00", when=ago(110))
    newer = await post_listing_price(loc, barley, "2.00", when=ago(100))
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    for params in ({"basis": "latest"}, {"basis": "cheapest"}, {"basis": "average"}):
        line = lines_by_name(await cost(admin_client, recipe["id"], **params))["barley"]
        # Not the cheaper one, nor an average, and no window drops it (P3).
        assert line["status"] == "posted", params
        assert line["price"]["observation_id"] == newer, params
        assert line["consumed_cost"] == "0.4000", params
        assert line["price"]["stale"] is True, params
    line = lines_by_name(await cost(admin_client, recipe["id"], basis="average", window_days=30))
    assert line["barley"]["status"] == "posted"
    listed = (await recipe_by_path(admin_client, "harbor-bowl.cook"))["cost"]
    assert listed["lines_posted"] == 1 and listed["posted_stale"] is True
    assert listed["posted_consumed_cost"] == "0.4000" and listed["consumed_cost"] is None


async def test_a_real_price_older_than_the_average_window_beats_a_newer_posted_one(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    barley = await bag(admin_client)
    real = await shelf(admin_client, barley["id"], loc["id"], "4.00", observed_at=days_ago(90))
    await post_listing_price(loc, barley, "1.00")
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    body = await cost(admin_client, recipe["id"], basis="average", window_days=30)
    line = lines_by_name(body)["barley"]
    assert line["status"] == "priced" and line["price"]["observation_id"] == real["id"]
    assert line["consumed_cost"] == "0.8000"


async def test_a_pin_takes_only_its_own_products_posted_price(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    barley = await bag(admin_client)
    iid = barley["ingredient"]["id"]
    premium = await make_sibling_product(
        admin_client, iid, "Barley premium", pack_qty="1", pack_unit="kg"
    )
    sack = await make_sibling_product(
        admin_client, iid, "Barley sack", pack_qty="5", pack_unit="kg"
    )
    await post_listing_price(loc, barley, "1.00")
    mine = await post_listing_price(loc, premium, "3.00", when=ago(5))
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)

    async def pin(product: dict) -> dict:
        r = await admin_client.put(
            f"/api/v1/recipes/{recipe['id']}/pins/barley", json={"product_id": product["id"]}
        )
        assert r.status_code == 200, r.text
        await recost(recipe["id"])
        return lines_by_name(await cost(admin_client, recipe["id"]))["barley"]

    line = await pin(premium)
    assert line["status"] == "posted" and line["price"]["observation_id"] == mine
    assert line["consumed_cost"] == "0.6000" and line["hint"] is None

    # A pin without a posted price of its own stays unpriced, and says who has one.
    line = await pin(sack)
    assert line["status"] == "unpriced" and line["price"] is None
    assert line["hint"]["kind"] == "posted_other_product"
    assert line["hint"]["product_name"] in {"Barley bag", "Barley premium"}


async def test_counted_and_posted_totals_of_a_recipe_with_priced_posted_and_unpriced_lines(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    barley = await bag(admin_client)
    milk = await make_product(
        admin_client,
        "Oat milk",
        "Oat milk carton",
        canonical_unit="ml",
        pack_qty="1",
        pack_unit="l",
    )
    await make_product(
        admin_client, "Dates", "Dates box", canonical_unit="each", pack_qty="20", pack_unit="each"
    )
    await shelf(admin_client, barley["id"], loc["id"], "4.00")
    await post_listing_price(loc, milk, "3.00")
    recipe = await indexed(admin_client, recipes_repo, "harbor-porridge.cook", PORRIDGE)
    body = await cost(admin_client, recipe["id"])
    lines = lines_by_name(body)
    assert [lines[n]["status"] for n in ("barley", "oat milk", "dates")] == [
        "priced",
        "posted",
        "unpriced",
    ]
    # Counted: barley alone, $0.80, so $0.20 a serving of four. Posted: 500 ml at $3.00 a L.
    assert body["totals"] == {
        "consumed_cost": "0.8000",
        "consumed_cost_high": "0.8000",
        "basket_cost": "4.0000",
        "basket_cost_high": "4.0000",
        "per_serving": "0.2000",
        "posted_consumed_cost": "1.5000",
        "posted_consumed_cost_high": "1.5000",
        "posted_basket_cost": "3.0000",
        "posted_basket_cost_high": "3.0000",
    }
    completeness = body["completeness"]
    assert (completeness["lines_priced"], completeness["lines_posted"]) == (1, 1)
    assert completeness["lines_unpriced"] == 1
    assert body["posted_share"] == "0.6522"  # 1.50 / 2.30
    assert body["unconfirmed_share"] == "0.0000"
    listed = (await recipe_by_path(admin_client, "harbor-porridge.cook"))["cost"]
    assert (listed["lines_posted"], listed["posted_stale"], listed["posted_share"]) == (
        1,
        False,
        "0.6522",
    )
    r = await admin_client.get("/api/v1/recipes", params={"completeness": "complete"})
    assert recipe["id"] not in {i["id"] for i in r.json()["items"]}


async def test_a_posted_price_is_scoped_to_the_kitchen_like_a_real_one(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    harbor = await make_home_base(admin_client, "Harbor kitchen", NEAR_A)
    ridge = await make_home_base(admin_client, "Ridge kitchen", NEAR_B)
    await make_location(
        admin_client, "Harbor Market", "Harbor Market", coords=SYNTH[0], home_base_id=harbor["id"]
    )
    far = await make_location(
        admin_client, "Ridge Grocer", "Ridge Grocer", coords=SYNTH[1], home_base_id=ridge["id"]
    )
    barley = await bag(admin_client)
    await post_listing_price(far, barley, "1.00")
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)

    household = lines_by_name(await cost(admin_client, recipe["id"]))["barley"]
    assert household["status"] == "posted"
    there = await cost(admin_client, recipe["id"], home_base_id=ridge["id"])
    assert lines_by_name(there)["barley"]["status"] == "posted"
    here = lines_by_name(await cost(admin_client, recipe["id"], home_base_id=harbor["id"]))
    assert here["barley"]["status"] == "unpriced"
    assert here["barley"]["hint"] == {
        "kind": "posted_outside_kitchen",
        "product_name": None,
        "store_name": "Ridge Grocer",
        "ingredient_name": None,
    }


async def test_a_line_pinned_to_a_moved_product_says_where_it_went(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    barley = await bag(admin_client)
    rye = await make_product(admin_client, "Rye", "Rye bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, barley["id"], loc["id"], "4.00")
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    r = await admin_client.put(
        f"/api/v1/recipes/{recipe['id']}/pins/barley", json={"product_id": barley["id"]}
    )
    assert r.status_code == 200, r.text
    r = await admin_client.patch(
        f"/api/v1/products/{barley['id']}", json={"ingredient_id": rye["ingredient"]["id"]}
    )
    assert r.status_code == 200, r.text
    line = lines_by_name(await cost(admin_client, recipe["id"]))["barley"]
    assert line["status"] == "unpriced" and line["pinned"] is True
    assert line["hint"] == {
        "kind": "pin_moved",
        "product_name": "Barley bag",
        "store_name": None,
        "ingredient_name": "Rye",
    }


async def test_migration_0070_downgrade_keeps_history_and_its_counts_add_up(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    barley = await bag(admin_client)
    await post_listing_price(loc, barley, "1.00")
    await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    recipes_repo.edit("harbor-bowl.cook", "\nAdd @barley{100%g}.\n")
    recipes_repo.commit("more barley")
    r = await admin_client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200, r.text
    before = await owner_conn.fetch("SELECT id FROM recipe_cost_snapshot ORDER BY id")
    assert len(before) == 2, "the first version stays as history"
    assert (
        await owner_conn.fetchval("SELECT count(*) FROM recipe_cost_line WHERE status = 'posted'")
        == 2
    )

    run_alembic("downgrade", "0069")
    after = await owner_conn.fetch(
        "SELECT id, lines_total, lines_priced, lines_unpriced, lines_unconvertible,"
        " lines_unmapped, lines_negligible FROM recipe_cost_snapshot ORDER BY id"
    )
    assert [r["id"] for r in after] == [r["id"] for r in before]
    for row in after:
        counts = dict(row)
        parts = sum(v for k, v in counts.items() if k.startswith("lines_") and k != "lines_total")
        assert parts == row["lines_total"] == row["lines_unpriced"]
    lines = await owner_conn.fetch("SELECT status, observation_id FROM recipe_cost_line")
    assert {(r["status"], r["observation_id"]) for r in lines} == {("unpriced", None)}

    run_alembic("upgrade", "head")
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM recipe_cost_snapshot WHERE costing_version = 0"
        )
        == 2
    )
    await dispose_engine()
