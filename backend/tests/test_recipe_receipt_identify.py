"""Receipt-line identification beside recipe names (07, 3C): criterion 18.

Recipes add a second queue of names to resolve, and a recipe decision writes an
ingredient spelling. Neither reaches the receipt side: the to-identify queue,
its inbox row and its apply endpoint answer as they did before any recipe was
indexed. Stores, recipes, products and figures are invented.
"""

from __future__ import annotations

import httpx

from tests.catalog_helpers import make_ingredient, make_product
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_match_waiting import _imported

SPRITZ = """---
title: Lantern spritz
---

Top @sparkling water{200%ml} with @bitter orange{1%tbsp}.
"""


async def to_identify(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get("/api/v1/to-identify")
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def inbox(client: httpx.AsyncClient) -> dict[str, dict]:
    r = await client.get("/api/v1/inbox")
    assert r.status_code == 200, r.text
    return {i["kind"]: i for i in r.json()["items"] if i["kind"] in {"identify", "recipe"}}


async def test_criterion_18_recipes_leave_the_to_identify_queue_as_it_was(
    admin_client: httpx.AsyncClient,
    admin,
    tmp_path,
    recipes_repo: TempRepo,  # noqa: F811
):
    await _imported(admin_client, admin, tmp_path)
    before = await to_identify(admin_client)
    assert [g["raw_text_norm"] for g in before] == ["RIGATONI 16OZ", "SPARKLING WTR 1L"]
    title = (await inbox(admin_client))["identify"]["title"]
    assert title == "3 receipt lines to identify"

    # A recipe whose names are unresolved: they queue on the recipe side only.
    recipes_repo.write("lantern-spritz.cook", SPRITZ)
    recipes_repo.commit("spritz")
    assert (await admin_client.post("/api/v1/recipes/rescan")).status_code == 200
    names = (await admin_client.get("/api/v1/recipes/resolve")).json()["items"]
    assert {n["name_norm"] for n in names} == {"sparkling water", "bitter orange"}
    assert await to_identify(admin_client) == before
    rows = await inbox(admin_client)
    assert rows["identify"]["title"] == title
    assert rows["identify"]["action_route"] == "/shop/receipts/identify"
    assert "recipe" in rows

    # A recipe decision writes a spelling; the receipt line still waits.
    water = await make_ingredient(admin_client, "Sparkling water")
    r = await admin_client.post(
        "/api/v1/recipes/resolve",
        json={"name_norm": "sparkling water", "ingredient_id": water["id"]},
    )
    assert r.status_code == 200, r.text
    assert await to_identify(admin_client) == before

    # The receipt side's own apply still empties its queue, and leaves the recipe one.
    product = await make_product(
        admin_client, water["id"], "Sparkling water 1 L", pack_qty="1", pack_unit="l"
    )
    group = before[1]
    r = await admin_client.post(
        "/api/v1/to-identify/apply",
        json={
            "vendor_id": group["vendor"]["id"],
            "raw_text_norm": group["raw_text_norm"],
            "product_id": product["id"],
        },
    )
    assert r.status_code == 200, r.text
    assert await to_identify(admin_client) == before[:1]
    assert (await inbox(admin_client))["identify"]["title"] == "2 receipt lines to identify"
    left = (await admin_client.get("/api/v1/recipes/resolve")).json()["items"]
    assert [n["name_norm"] for n in left] == ["bitter orange"]
