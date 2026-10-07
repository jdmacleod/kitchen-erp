"""Merging an ingredient from its own page (#211; spec 03 §1G).

The page uses the link page's merge unchanged, naming the survivor's own name
as the target, so the survivor keeps its name and standard link. The merged
ingredient's response says where it went, and it can't be reactivated.
"""

from __future__ import annotations

from tests.catalog_helpers import make_ingredient
from tests.pricebook_helpers import make_product


async def _merge(admin_client, survivor: dict, loser: dict, *, preview: bool = False):
    path = "/api/v1/ingredients/merge" + ("/preview" if preview else "")
    body = {"survivor_id": survivor["id"], "loser_id": loser["id"], "name": survivor["name"]}
    if not preview:
        body["copy_measures"] = []
    return await admin_client.post(path, json=body)


async def test_merging_into_another_keeps_the_survivors_name(admin_client):
    product = await make_product(admin_client, "Tarragon sprigs", "Tarragon bundle")
    loser = product["ingredient"]
    survivor = await make_ingredient(admin_client, "Tarragon leaves")

    p = (await _merge(admin_client, survivor, loser, preview=True)).json()
    assert p["target_name"] == "Tarragon leaves" and p["products_moving"] == 1

    r = await _merge(admin_client, survivor, loser)
    assert r.status_code == 200, r.text
    kept = (await admin_client.get(f"/api/v1/ingredients/{survivor['id']}")).json()
    assert kept["name"] == "Tarragon leaves" and kept["merged_into"] is None
    gone = (await admin_client.get(f"/api/v1/ingredients/{loser['id']}")).json()
    assert gone["active"] is False and gone["merged_into"] == survivor["id"]
    assert gone["name"] == "Tarragon sprigs (merged into Tarragon leaves)"
    moved = (await admin_client.get(f"/api/v1/products/{product['id']}")).json()
    assert moved["ingredient"]["id"] == survivor["id"]
    # The old name finds the survivor.
    found = (
        await admin_client.get("/api/v1/ingredients/search", params={"q": "tarragon sprigs"})
    ).json()
    assert [i["id"] for i in found["items"] if i["kind"] == "ingredient"] == [survivor["id"]]


async def test_a_linked_survivor_stays_linked(admin_client):
    linked = await make_ingredient(admin_client, "Basil", standard_key="basil")
    assert linked["reconcile_state"] == "linked"
    loser = await make_ingredient(admin_client, "Basil leaves, torn")
    r = await _merge(admin_client, linked, loser)
    assert r.status_code == 200, r.text
    kept = (await admin_client.get(f"/api/v1/ingredients/{linked['id']}")).json()
    assert (kept["name"], kept["slug"], kept["reconcile_state"]) == ("basil", "basil", "linked")


async def test_a_merged_ingredient_cant_be_reactivated(admin_client):
    survivor = await make_ingredient(admin_client, "Chervil")
    loser = await make_ingredient(admin_client, "Chervil sprigs")
    assert (await _merge(admin_client, survivor, loser)).status_code == 200
    r = await admin_client.post(f"/api/v1/ingredients/{loser['id']}/activate")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ingredient_merged"
    # Deactivating and reactivating an ordinary one still works.
    assert (
        await admin_client.post(f"/api/v1/ingredients/{survivor['id']}/deactivate")
    ).status_code == 200
    assert (
        await admin_client.post(f"/api/v1/ingredients/{survivor['id']}/activate")
    ).status_code == 200


async def test_merging_into_a_merged_ingredient_is_refused(admin_client):
    a = await make_ingredient(admin_client, "Sorrel")
    b = await make_ingredient(admin_client, "Sorrel leaves")
    c = await make_ingredient(admin_client, "Sorrel, French")
    assert (await _merge(admin_client, a, b)).status_code == 200
    r = await _merge(admin_client, b, c)
    assert r.status_code == 404
