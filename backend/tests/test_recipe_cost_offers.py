"""Recipe costing's "this week's offers" basis (18, X2). Stores, offers and recipes
are invented; the geography is synthetic.

A per-kitchen view only: each line at the cheapest offer valid today at the kitchen's
stores, else its latest price, an estimate where the offer printed no size. A 1 kg
bag on offer at $2.00 is $0.002000 per g, so 200 g comes to $0.4000.
"""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from app.services import flyers
from tests.geo_helpers import NEAR_A, make_home_base
from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_flyer_offers import THURSDAY
from tests.test_flyer_reads import learned
from tests.test_flyers import candidate, queue, report, requests, seed, source_row
from tests.test_products_helper import helper  # noqa: F401  the stub helper's client
from tests.test_recipe_costing import BOWL, cost, days_ago, indexed, lines_by_name


@pytest.fixture(autouse=True)
def _in_the_ad_week(monkeypatch):
    monkeypatch.setattr(flyers, "today", lambda now=None: THURSDAY)


@pytest.fixture
async def harbor(admin_client: httpx.AsyncClient, recipes_repo: TempRepo) -> dict:  # noqa: F811
    """A kitchen whose market sells barley at $4.00 a bag, and a bowl of 200 g of it."""
    kitchen = await make_home_base(admin_client, "Harbor kitchen", NEAR_A)
    market = await make_location(
        admin_client, "Harbor Market", "Harbor Market", coords=SYNTH[0], home_base_id=kitchen["id"]
    )
    barley = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, barley["id"], market["id"], "4.00", observed_at=days_ago(30))
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    return {"kitchen": kitchen, "market": market, "barley": barley, "recipe": recipe}


async def circular(helper, owner_conn, harbor: dict, **printed) -> None:  # noqa: F811
    """The market's week, with its printed name for the barley already learned."""
    assert (await seed([source_row(harbor["market"])])).created == 1
    await learned(owner_conn, harbor["market"], harbor["barley"], "barley bag")
    await queue()
    [request] = await requests(helper)
    r = await report(helper, request["id"], candidates=[candidate("Barley Bag", "2.00", **printed)])
    assert r.status_code == 200, r.text


async def offers_cost(client: httpx.AsyncClient, harbor: dict) -> dict:
    return await cost(
        client, harbor["recipe"]["id"], basis="offers", home_base_id=harbor["kitchen"]["id"]
    )


async def test_a_line_is_costed_at_this_weeks_offer_at_the_kitchens_store(
    admin_client,
    helper,  # noqa: F811
    owner_conn,
    app_conn: asyncpg.Connection,
    harbor,
):
    await circular(helper, owner_conn, harbor, size_qty="1", size_unit="kg")
    body = await offers_cost(admin_client, harbor)
    assert (body["basis"], body["home_base_id"]) == ("offers", harbor["kitchen"]["id"])
    barley = lines_by_name(body)["barley"]
    price = barley["price"]
    assert (price["norm_unit_price"], barley["consumed_cost"]) == ("0.002000", "0.4000")
    assert (price["location_id"], price["product_id"]) == (
        harbor["market"]["id"],
        harbor["barley"]["id"],
    )
    assert price["flyer_offer_id"] is not None and price["observation_id"] is None
    assert (price["estimate"], price["stale"]) == (False, False)
    # The stored snapshot never reads an offer: the latest basis is the shelf price.
    latest = lines_by_name(await cost(admin_client, harbor["recipe"]["id"]))["barley"]
    assert (latest["price"]["norm_unit_price"], latest["price"]["flyer_offer_id"]) == (
        "0.004000",
        None,
    )
    assert (
        await app_conn.fetchval("SELECT count(*) FROM recipe_cost_snapshot WHERE basis = 'offers'")
        == 0
    )


async def test_an_offer_priced_by_the_products_size_is_an_estimate(
    admin_client,
    helper,  # noqa: F811
    owner_conn,
    harbor,
):
    await circular(helper, owner_conn, harbor)  # printed without a size
    price = lines_by_name(await offers_cost(admin_client, harbor))["barley"]["price"]
    assert (price["norm_unit_price"], price["estimate"]) == ("0.002000", True)


async def test_without_an_offer_a_line_takes_its_latest_price(admin_client, harbor):
    price = lines_by_name(await offers_cost(admin_client, harbor))["barley"]["price"]
    assert (price["norm_unit_price"], price["flyer_offer_id"], price["estimate"]) == (
        "0.004000",
        None,
        False,
    )


async def test_the_offers_basis_needs_a_kitchen(admin_client, harbor):
    r = await admin_client.get(
        f"/api/v1/recipes/{harbor['recipe']['id']}/cost", params={"basis": "offers"}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (422, "offers_need_kitchen")
