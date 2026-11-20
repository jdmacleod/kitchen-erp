"""Recipe references through the API (07, 3D; #526, Part 4).

The recipe detail's `makes`, `used_in` and linked body; the cost table's line
sources and `makes_unit_cost`; the ingredient's `made_by_recipe`,
`recipe_match` and the made-by PATCH. Stores, products and recipes are
invented; the geography is synthetic.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.db import get_sessionmaker
from app.models import Ingredient
from app.services import recipe_costing
from tests.catalog_helpers import make_ingredient
from tests.catalog_helpers import make_product as make_sibling_product
from tests.geo_helpers import NEAR_A, make_home_base
from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_recipe_cost_sources import SOUR, SOUR_B, SYRUP, made_by, recipe_id, sugar, write


async def detail(client: httpx.AsyncClient, path: str) -> dict:
    r = await client.get(f"/api/v1/recipes/{await recipe_id(path)}")
    assert r.status_code == 200, r.text
    return r.json()


async def cost(client: httpx.AsyncClient, path: str, **params) -> dict:
    r = await client.get(f"/api/v1/recipes/{await recipe_id(path)}/cost", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def line_of(body: dict, name: str) -> dict:
    return {line["line"]["name_norm"]: line for line in body["lines"]}[name]


async def patch(client: httpx.AsyncClient, ingredient: dict, **body) -> httpx.Response:
    return await client.patch(f"/api/v1/ingredients/{ingredient['id']}", json=body)


# --- the recipe detail ----------------------------------------------------------------------


async def test_the_detail_shows_what_a_recipe_makes_and_who_uses_it_by_either_route(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    sour_twice = SOUR.replace("with ice.", "and @./basics/lantern-syrup{1%tsp} with ice.")
    await write(
        admin_client,
        recipes_repo,
        {
            "basics/lantern-syrup.cook": SYRUP,
            "harbor-sour.cook": sour_twice,
            "comet-fizz.cook": SOUR_B.replace("Harbor sour", "Comet fizz"),
        },
    )
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    syrup = await detail(admin_client, "basics/lantern-syrup.cook")
    assert syrup["makes"] == {
        "text": "500 ml",
        "qty": "500",
        "unit": "ml",
        "display_qty": "16.9",
        "display_unit": "fl oz",
    }
    assert [u["title"] for u in syrup["used_in"]] == ["Comet fizz", "Harbor sour"]
    assert syrup["used_lines"] == 3
    sour = await detail(admin_client, "harbor-sour.cook")
    assert sour["makes"] is None and sour["used_in"] == []
    [ref, *_] = [i for i in sour["ingredients"] if i["resolution"] == "recipe"]
    assert ref["ref_path"] == "./basics/lantern-syrup" and ref["ref_recipe_id"] == syrup["id"]
    items = [item for step in sour["body"][0]["steps"] for item in step]
    linked = [i for i in items if i["t"] == "ingredient" and i.get("ref_path")]
    assert linked and all(i["recipe_id"] == syrup["id"] for i in linked)


async def test_a_count_yield_reads_as_its_text(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await write(
        admin_client, recipes_repo, {"comet-buns.cook": "---\nyield: 12 pieces\n---\nBake.\n"}
    )
    buns = await detail(admin_client, "comet-buns.cook")
    assert buns["makes"] == {
        "text": "12 pieces",
        "qty": None,
        "unit": None,
        "display_qty": None,
        "display_unit": None,
    }


# --- the cost table -----------------------------------------------------------------------


async def test_a_line_from_a_recipe_names_it_and_the_source_costs_per_unit_made(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    body = await cost(admin_client, "harbor-sour.cook")
    line = line_of(body, "lantern-syrup")
    assert line["status"] == "priced" and line["consumed_cost"] == "0.0591"
    assert line["source_recipe"]["title"] == "Lantern syrup"
    assert line["source_partial"] is False and line["source_basis"] is None
    assert line["source"]["makes"]["display_qty"] == "16.9"
    assert line["source"]["target_path"] == "basics/lantern-syrup.cook"
    assert line["usda_offer"] is None and line["price"] is None
    assert body["makes_unit_cost"] is None  # the sour makes nothing
    syrup = await cost(admin_client, "basics/lantern-syrup.cook")
    # $1.0000 for 16.907 fl oz.
    assert syrup["makes_unit_cost"] == {
        "amount": "0.059",
        "unit": "fl oz",
        "partial": False,
        "source_basis": None,
    }


@pytest.mark.parametrize(
    ("grams", "unit", "amount"), [("650", "lb", "0.698"), ("300", "oz", "0.094")]
)
async def test_a_yield_by_weight_costs_per_lb_or_per_oz_under_a_pound(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    grams: str,
    unit: str,
    amount: str,
):
    await sugar(admin_client)
    await write(
        admin_client,
        recipes_repo,
        {"lantern-syrup.cook": SYRUP.replace("yield: 500 ml", f"yield: {grams} g")},
    )
    found = (await cost(admin_client, "lantern-syrup.cook"))["makes_unit_cost"]
    assert (found["unit"], found["amount"]) == (unit, amount)


async def test_an_incomplete_source_reads_partial_and_its_unit_cost_too(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Moonflower")
    partial = SYRUP.replace("in @water", "with @moonflower{2%g} in @water")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": partial, "harbor-sour.cook": SOUR},
    )
    line = line_of(await cost(admin_client, "harbor-sour.cook"), "lantern-syrup")
    assert line["source_partial"] is True
    syrup = await cost(admin_client, "basics/lantern-syrup.cook")
    assert syrup["makes_unit_cost"]["partial"] is True


async def test_failures_carry_what_their_words_need(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await make_ingredient(admin_client, "Sugar")  # no price
    await write(
        admin_client,
        recipes_repo,
        {
            "syrups/lantern-syrup.cook": SYRUP,
            "harbor-sour.cook": SOUR,  # looks in basics/, the file is in syrups/
            "comet-sour.cook": SOUR.replace("./basics/", "./syrups/"),
        },
    )
    missing = line_of(await cost(admin_client, "harbor-sour.cook"), "lantern-syrup")
    assert (missing["status"], missing["failure_code"]) == ("unmapped", "no_recipe")
    assert missing["source"]["target_path"] == "basics/lantern-syrup.cook"
    assert missing["source"]["suggestion"]["path"] == "syrups/lantern-syrup.cook"
    assert missing["source_recipe"] is None
    unpriced = line_of(await cost(admin_client, "comet-sour.cook"), "lantern-syrup")
    assert unpriced["failure_code"] == "recipe_unpriced"
    assert unpriced["source_counts"] == {"priced": 0, "total": 2}


async def test_a_cycle_line_names_the_walk_round_and_each_hop(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Comet dip", canonical_unit="ml")
    glaze = "---\ntitle: Comet glaze\nyield: 1 l\n---\nWarm @sugar{100%g} and @comet dip{50%ml}.\n"
    dip = "---\ntitle: Comet dip\nyield: 1 l\n---\nStir @sugar{100%g} and @./comet-glaze{100%ml}.\n"
    await write(admin_client, recipes_repo, {"comet-glaze.cook": glaze, "comet-dip.cook": dip})
    await made_by("Comet dip", "comet-dip.cook")
    async with get_sessionmaker()() as db:
        await recipe_costing.recompute_all(db)
    line = line_of(await cost(admin_client, "comet-dip.cook"), "comet-glaze")
    assert line["failure_code"] == "cycle"
    hops = line["source"]["cycle"]
    assert [h["recipe"]["title"] for h in hops] == ["Comet dip", "Comet glaze", "Comet dip"]
    assert hops[0]["via_ingredient"] is None  # the dip's file names the glaze
    assert hops[1]["via_ingredient"]["name"] == "Comet dip"  # the glaze uses the dip's ingredient


async def test_a_kitchen_view_says_the_source_was_costed_under_latest(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    harbor = await make_home_base(admin_client, "Harbor kitchen", NEAR_A)
    loc = await make_location(
        admin_client, "Harbor Market", "Harbor Market", coords=SYNTH[0], home_base_id=harbor["id"]
    )
    bag = await make_product(admin_client, "Sugar", "Sugar bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, bag["id"], loc["id"], "4.00")
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    for basis in ("average", "offers"):
        body = await cost(admin_client, "harbor-sour.cook", basis=basis, home_base_id=harbor["id"])
        line = line_of(body, "lantern-syrup")
        assert line["status"] == "priced" and line["source_basis"] == "latest", basis
    syrup = await cost(
        admin_client, "basics/lantern-syrup.cook", basis="offers", home_base_id=harbor["id"]
    )
    assert syrup["makes_unit_cost"]["source_basis"] == "latest"


# --- the ingredient ------------------------------------------------------------------------


async def test_the_ingredient_offers_its_one_namesake_recipe(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    stuff = await sugar(admin_client)
    syrup_ing = await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    await write(admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP})
    got = (await admin_client.get(f"/api/v1/ingredients/{syrup_ing['id']}")).json()
    assert got["recipe_match"]["title"] == "Lantern syrup" and got["made_by_recipe"] is None

    # Linked: no offer, and the link says what it makes and costs.
    r = await patch(admin_client, syrup_ing, made_by_recipe_id=got["recipe_match"]["id"])
    assert r.status_code == 200, r.text
    linked = r.json()
    assert linked["recipe_match"] is None
    assert linked["made_by_recipe"]["makes_ok"] is True
    assert linked["made_by_recipe"]["unit_cost"]["unit"] == "fl oz"

    # Cleared: the offer comes back; a product price takes it away.
    r = await patch(admin_client, syrup_ing, made_by_recipe_id=None)
    assert r.json()["made_by_recipe"] is None and r.json()["recipe_match"] is not None
    bottle = await make_sibling_product(
        admin_client, syrup_ing["id"], "Lantern syrup bottle", pack_qty="750", pack_unit="ml"
    )
    await shelf(admin_client, bottle["id"], stuff["loc"]["id"], "6.00")
    got = (await admin_client.get(f"/api/v1/ingredients/{syrup_ing['id']}")).json()
    assert got["recipe_match"] is None


async def test_two_namesake_recipes_offer_neither(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    syrup_ing = await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "old/lantern-syrup.cook": SYRUP},
    )
    got = (await admin_client.get(f"/api/v1/ingredients/{syrup_ing['id']}")).json()
    assert got["recipe_match"] is None


async def test_the_link_is_checked_and_errors_use_the_envelope(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    ing = await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "basics/comet-syrup.cook": SYRUP},
    )
    r = await patch(admin_client, ing, made_by_recipe_id=str(uuid.uuid4()))
    assert r.status_code == 422 and r.json()["error"]["code"] == "recipe_not_found"

    recipes_repo.write("basics/comet-syrup.cook", SYRUP + "Stir @sugar{2 and serve.\n")
    recipes_repo.remove("basics/lantern-syrup.cook")
    recipes_repo.commit("break one, lose one")
    await admin_client.post("/api/v1/recipes/rescan")
    r = await patch(
        admin_client, ing, made_by_recipe_id=str(await recipe_id("basics/lantern-syrup.cook"))
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "recipe_missing"
    assert r.json()["error"]["details"]["path"] == "basics/lantern-syrup.cook"
    r = await patch(
        admin_client, ing, made_by_recipe_id=str(await recipe_id("basics/comet-syrup.cook"))
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "recipe_unreadable"


async def test_linking_recomputes_counts_recipes_and_clearing_alone_changes_nothing_else(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    ing = await make_ingredient(
        admin_client, "Lantern syrup", canonical_unit="ml", notes="house made"
    )
    await write(
        admin_client,
        recipes_repo,
        {
            "basics/lantern-syrup.cook": SYRUP,
            "harbor-sour.cook": SOUR_B,
            "comet-fizz.cook": SOUR_B.replace("Harbor sour", "Comet fizz"),
        },
    )
    syrup = await recipe_id("basics/lantern-syrup.cook")
    r = await patch(admin_client, ing, made_by_recipe_id=str(syrup))
    assert r.status_code == 200, r.text
    assert (r.json()["recomputed"], r.json()["recompute_failed"]) == (2, 0)
    assert r.json()["warnings"] == []
    assert line_of(await cost(admin_client, "harbor-sour.cook"), "lantern syrup")["status"] == (
        "priced"
    )
    r = await patch(admin_client, ing, made_by_recipe_id=None)
    after = r.json()
    assert after["made_by_recipe"] is None and after["notes"] == "house made"
    assert after["canonical_unit"] == "ml" and after["name"] == "Lantern syrup"
    # Leaving it out leaves the link alone.
    await patch(admin_client, ing, made_by_recipe_id=str(syrup))
    r = await patch(admin_client, ing, notes="still house made")
    assert r.json()["made_by_recipe"]["id"] == str(syrup) and r.json()["recomputed"] is None


async def test_a_link_that_closes_a_cycle_is_kept_with_a_warning(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    ing = await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    loop = SYRUP.replace("in @water", "with @lantern syrup{10%ml} in @water")
    await write(admin_client, recipes_repo, {"basics/lantern-syrup.cook": loop})
    r = await patch(
        admin_client, ing, made_by_recipe_id=str(await recipe_id("basics/lantern-syrup.cook"))
    )
    assert r.status_code == 200, r.text
    [warning] = r.json()["warnings"]
    assert warning["code"] == "recipe_cycle"
    assert [c["title"] for c in warning["chain"]] == ["Lantern syrup", "Lantern syrup"]
    async with get_sessionmaker()() as db:
        row = await db.get(Ingredient, uuid.UUID(ing["id"]))
        assert row.made_by_recipe_id is not None


async def test_a_failed_recompute_leaves_the_link_and_is_counted(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    await sugar(admin_client)
    ing = await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR_B},
    )

    async def boom(*_args, **_kw):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(recipe_costing, "recompute_recipe_core", boom)
    r = await patch(
        admin_client, ing, made_by_recipe_id=str(await recipe_id("basics/lantern-syrup.cook"))
    )
    assert r.status_code == 200, r.text
    assert (r.json()["recomputed"], r.json()["recompute_failed"]) == (0, 1)
    assert r.json()["made_by_recipe"]["title"] == "Lantern syrup"
