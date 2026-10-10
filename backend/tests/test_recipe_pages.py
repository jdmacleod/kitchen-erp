"""The Cook screens' extras (07, 3E; package 8): the rendered body on the recipe,
dismissing a relink proposal, and an ingredient's recipes for the "Used in" card
(criterion 33). Recipes and ingredients are invented.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.models import Ingredient, Recipe
from tests.catalog_helpers import make_ingredient
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401

GLOW = """---
title: Glowworm noodles
servings: 2
---

Boil @rice noodles{200%g} in a #pot{} for ~{8%minutes}.

= Sauce

Whisk @tamari{2%tbsp} with @ginger{1-2%tsp}(grated) and @chili flakes{a pinch}, then add @scallion.
"""

BROKEN = GLOW + "\nStir in @sesame oil{.\n"


async def rescan(client: httpx.AsyncClient) -> dict:
    r = await client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200, r.text
    return r.json()


async def by_path(client: httpx.AsyncClient) -> dict[str, dict]:
    return {i["path"]: i for i in (await client.get("/api/v1/recipes")).json()["items"]}


async def detail(client: httpx.AsyncClient, recipe_id: str) -> dict:
    r = await client.get(f"/api/v1/recipes/{recipe_id}")
    assert r.status_code == 200, r.text
    return r.json()


# --- the body ---------------------------------------------------------------------------


async def test_the_body_holds_the_steps_with_seq_linking_each_ingredient_to_its_row(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("glowworm.cook", GLOW)
    recipes_repo.commit("one")
    await rescan(admin_client)
    recipe = await detail(admin_client, (await by_path(admin_client))["glowworm.cook"]["id"])

    body = recipe["body"]
    assert [s["name"] for s in body] == [None, "Sauce"]
    [boil] = body[0]["steps"]
    kinds = [item["t"] for item in boil]
    assert kinds == ["text", "ingredient", "text", "cookware", "text", "timer", "text"]
    noodles = boil[1]
    assert noodles == {
        "t": "ingredient",
        "name": "rice noodles",
        "qty": "200",
        "unit": "g",
        "note": None,
        "seq": 1,
    }
    assert boil[3] == {"t": "cookware", "name": "pot", "qty": None}
    assert boil[5] == {"t": "timer", "name": None, "qty": "8", "unit": "minutes"}

    [whisk] = body[1]["steps"]
    refs = [item for item in whisk if item["t"] == "ingredient"]
    assert [r["name"] for r in refs] == ["tamari", "ginger", "chili flakes", "scallion"]
    # Decimals and ranges are text; a bare name has no quantity.
    assert [r["qty"] for r in refs] == ["2", "1–2", "a pinch", None]
    assert refs[1]["note"] == "grated"
    # seq links each reference to its recipe_ingredient row, in document order.
    assert [r["seq"] for r in refs] == [2, 3, 4, 5]
    rows = {line["seq"]: line["raw_name"] for line in recipe["ingredients"]}
    assert all(rows[r["seq"]] == r["name"] for r in refs)
    assert "." not in str(whisk[0]["v"]) or whisk[0]["t"] == "text"  # text is kept verbatim


async def test_the_body_stays_when_the_file_stops_parsing(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("glowworm.cook", GLOW)
    recipes_repo.commit("one")
    await rescan(admin_client)
    recipe_id = (await by_path(admin_client))["glowworm.cook"]["id"]
    good = (await detail(admin_client, recipe_id))["body"]

    recipes_repo.write("glowworm.cook", BROKEN)
    assert (await rescan(admin_client))["parse_errors"] == 1
    broken = await detail(admin_client, recipe_id)
    assert broken["status"] == "parse_error"
    assert broken["body"] == good  # criterion 4: the last good parse, steps included


# --- dismissing a relink ---------------------------------------------------------------


async def test_dismissing_a_relink_clears_it_and_remembers_the_path(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("index_pea_soup.cook", "Simmer @peas{300%g}.\n")
    recipes_repo.commit("one")
    await rescan(admin_client)
    old_id = (await by_path(admin_client))["index_pea_soup.cook"]["id"]

    # Nothing to dismiss yet.
    r = await admin_client.post(f"/api/v1/recipes/{old_id}/relink/dismiss")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_proposal"
    r = await admin_client.post(f"/api/v1/recipes/{uuid.uuid4()}/relink/dismiss")
    assert r.status_code == 404

    recipes_repo.move("index_pea_soup.cook", "index_pea_soup_two.cook")
    recipes_repo.edit("index_pea_soup_two.cook", "Add @mint{some}.\n")
    assert (await rescan(admin_client))["proposals"] == 1
    proposal = (await detail(admin_client, old_id))["relink"]
    assert proposal is not None and proposal["path"] == "index_pea_soup_two.cook"
    new_id = proposal["target_id"]

    r = await admin_client.post(f"/api/v1/recipes/{old_id}/relink/dismiss")
    assert r.status_code == 200, r.text
    assert r.json()["relink"] is None and r.json()["status"] == "missing"
    assert (await detail(admin_client, old_id))["relink"] is None
    async with get_sessionmaker()() as db:
        row = await db.get(Recipe, uuid.UUID(old_id))
        assert row is not None and row.relink_dismissed_paths == ["index_pea_soup_two.cook"]

    # A plain rescan keeps it dismissed, and the person can still relink by hand;
    # the body travels with the relink.
    await rescan(admin_client)
    assert (await detail(admin_client, old_id))["relink"] is None
    r = await admin_client.post(f"/api/v1/recipes/{old_id}/relink", json={"target_id": new_id})
    assert r.status_code == 200 and r.json()["path"] == "index_pea_soup_two.cook"
    assert r.json()["body"] is not None


async def test_a_scan_never_proposes_a_dismissed_path_again(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("index_pea_soup.cook", "Simmer @peas{300%g}.\n")
    recipes_repo.commit("one")
    await rescan(admin_client)
    old_id = (await by_path(admin_client))["index_pea_soup.cook"]["id"]
    recipes_repo.move("index_pea_soup.cook", "index_pea_soup_two.cook")
    recipes_repo.edit("index_pea_soup_two.cook", "Add @mint{some}.\n")
    assert (await rescan(admin_client))["proposals"] == 1
    new_id = (await detail(admin_client, old_id))["relink"]["target_id"]
    assert (await admin_client.post(f"/api/v1/recipes/{old_id}/relink/dismiss")).status_code == 200

    # The candidate is removed from the index and its file appears afresh: the
    # planner would propose it again, and the scan drops that proposal.
    recipes_repo.remove("index_pea_soup_two.cook")
    assert (await rescan(admin_client))["missing"] == 1
    assert (await admin_client.delete(f"/api/v1/recipes/{new_id}")).status_code == 204
    recipes_repo.write("index_pea_soup_two.cook", "Simmer @peas{300%g}.\nAdd @mint{some}.\n")
    result = await rescan(admin_client)
    assert result["created"] == 1 and result["proposals"] == 1
    assert (await detail(admin_client, old_id))["relink"] is None

    # A new file at another path is still proposed.
    recipes_repo.write("index_pea_soup_three.cook", "Simmer @peas{300%g}.\nAdd @dill{some}.\n")
    result = await rescan(admin_client)
    fresh = (await detail(admin_client, old_id))["relink"]
    assert fresh is not None and fresh["path"] == "index_pea_soup_three.cook"


# --- an ingredient's recipes --------------------------------------------------------------


async def test_an_ingredients_recipes_list_quantities_as_written_by_title(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    ginger = await make_ingredient(admin_client, "Ginger")
    tamari = await make_ingredient(admin_client, "Tamari")
    recipes_repo.write("glowworm.cook", GLOW)
    recipes_repo.write(
        "zephyr.cook", "---\ntitle: Zephyr broth\n---\n\nSteep @ginger{3%slices} and @ginger.\n"
    )
    recipes_repo.write("apple.cook", "---\ntitle: Apple pan\n---\n\nFry @apples{2}.\n")
    recipes_repo.commit("three")
    await rescan(admin_client)

    r = await admin_client.get(f"/api/v1/ingredients/{ginger['id']}/recipes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert [i["title"] for i in body["items"]] == ["Glowworm noodles", "Zephyr broth"]
    glow, zephyr = body["items"]
    assert glow["quantities"] == ["1–2 tsp"] and glow["status"] == "ok"
    assert zephyr["quantities"] == ["3 slices", None]
    assert glow["path"] == "glowworm.cook"

    # An ingredient used by none has an empty list, which the card omits (criterion 33).
    r = await admin_client.get(f"/api/v1/ingredients/{tamari['id']}/recipes")
    assert r.status_code == 200
    # tamari is resolved on glowworm: by its own name.
    assert r.json()["total"] == 1
    apple = await make_ingredient(admin_client, "Pear")
    r = await admin_client.get(f"/api/v1/ingredients/{apple['id']}/recipes")
    assert r.json() == {"items": [], "total": 0}
    assert (
        await admin_client.get(f"/api/v1/ingredients/{uuid.uuid4()}/recipes")
    ).status_code == 404


async def test_an_ingredients_recipes_follow_merged_into(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    ginger = await make_ingredient(admin_client, "Ginger")
    root = await make_ingredient(admin_client, "Ginger root")
    recipes_repo.write("a.cook", "---\ntitle: A\n---\n\nGrate @ginger{1%tsp}.\n")
    recipes_repo.write("b.cook", "---\ntitle: B\n---\n\nGrate @ginger root{2%tsp}.\n")
    recipes_repo.commit("two")
    await rescan(admin_client)
    # Merge the root into ginger without repointing b's line: the hub still
    # finds it through merged_into, from either side.
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(select(Ingredient).where(Ingredient.id == uuid.UUID(root["id"])))
        ).scalar_one()
        row.merged_into = uuid.UUID(ginger["id"])
        row.active = False
        await db.commit()
    for ingredient_id in (ginger["id"], root["id"]):
        r = await admin_client.get(f"/api/v1/ingredients/{ingredient_id}/recipes")
        assert r.status_code == 200, r.text
        assert [i["title"] for i in r.json()["items"]] == ["A", "B"]
        assert r.json()["total"] == 2


async def test_package_8_routes_need_a_session(client: httpx.AsyncClient):
    for method, path in (
        ("POST", f"/api/v1/recipes/{uuid.uuid4()}/relink/dismiss"),
        ("GET", f"/api/v1/ingredients/{uuid.uuid4()}/recipes"),
    ):
        r = await client.request(method, path)
        assert r.status_code == 401, (method, path)


async def test_the_queue_says_whether_a_model_is_configured(
    admin_client: httpx.AsyncClient, monkeypatch
):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_model", "")
    r = await admin_client.get("/api/v1/recipes/resolve")
    assert r.status_code == 200 and r.json()["model_configured"] is False
    monkeypatch.setattr(get_settings(), "llm_model", "invented-model")
    assert (await admin_client.get("/api/v1/recipes/resolve")).json()["model_configured"] is True
