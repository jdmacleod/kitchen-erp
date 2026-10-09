"""Recipe name resolution, the resolve queue and pins (07, 3C; package 4).

Criteria 14 to 18 and the 1G rulings VS2 and VC3: a name confirmed once resolves
in every later recipe across re-indexing; one decision applies to every recipe
and writes one spelling through ``add_spelling``, with a collision refused as
``alias_taken``; "not an ingredient" is recorded and leaves the queue;
negligible lines are never queued; a pin must fulfil the line's ingredient and
survives a rescan; the receipt side is untouched (its own suite runs unchanged).
Recipes, ingredients and products are invented.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.models import IngredientAlias, RecipeIngredient, RecipeNameIgnore, RecipePin
from app.services import recipe_resolution
from app.services.spellings import add_spelling
from tests.catalog_helpers import make_ingredient
from tests.conftest import run_alembic
from tests.pricebook_helpers import make_product
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401

STEW = """---
title: Lantern stew
---

Soften @onions{2} with @minced garlic{2%cloves} in @olive oil{1%tbsp}, then add
@water{500%ml}, @salt and @feta{100%g}(crumbled).
"""

BOWL = """---
title: Lantern bowl
---

Toss @chickpeas{400%g} with @minced garlic{1%clove}, @lemon juice{1%tbsp} and
@parsley{a handful}(chopped).
"""


async def rescan(client: httpx.AsyncClient) -> dict:
    r = await client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200, r.text
    return r.json()


async def recipes_by_path(client: httpx.AsyncClient) -> dict[str, dict]:
    return {i["path"]: i for i in (await client.get("/api/v1/recipes")).json()["items"]}


async def detail(client: httpx.AsyncClient, recipe_id: str) -> dict:
    r = await client.get(f"/api/v1/recipes/{recipe_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def lines_of(client: httpx.AsyncClient, recipe_id: str) -> dict[str, dict]:
    return {line["name_norm"]: line for line in (await detail(client, recipe_id))["ingredients"]}


async def queue(client: httpx.AsyncClient) -> dict:
    r = await client.get("/api/v1/recipes/resolve")
    assert r.status_code == 200, r.text
    return r.json()


async def decide(client: httpx.AsyncClient, **body) -> httpx.Response:
    return await client.post("/api/v1/recipes/resolve", json=body)


async def inbox_kinds(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get("/api/v1/inbox")
    assert r.status_code == 200, r.text
    return [i for i in r.json()["items"] if i["kind"] == "recipe"]


async def spell(ingredient_id: str, text: str) -> None:
    """A curated spelling, as the ingredient page writes one (1G)."""
    async with get_sessionmaker()() as db:
        await add_spelling(db, uuid.UUID(ingredient_id), text)
        await db.commit()


async def alias_rows() -> dict[str, IngredientAlias]:
    async with get_sessionmaker()() as db:
        found = (await db.execute(select(IngredientAlias))).scalars().all()
        return {a.name_norm: a for a in found}


# --- the lookup at scan time (VC3) ----------------------------------------------------


async def test_names_spellings_and_inflections_resolve_without_a_person(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    onion = await make_ingredient(admin_client, "Onion")  # generated plural: onions
    feta = await make_ingredient(admin_client, "Feta cheese")
    await spell(feta["id"], "feta")
    recipes_repo.write("lantern-stew.cook", STEW)
    await rescan(admin_client)
    stew = (await recipes_by_path(admin_client))["lantern-stew.cook"]
    got = await lines_of(admin_client, stew["id"])
    # An inflection of the name, a curated spelling, and nothing else.
    assert got["onions"]["resolution"] == "alias" and got["onions"]["ingredient_id"] == onion["id"]
    assert got["onions"]["ingredient_name"] == "Onion"
    assert got["feta"]["resolution"] == "alias" and got["feta"]["ingredient_id"] == feta["id"]
    assert got["minced garlic"]["resolution"] == "unmatched"
    assert got["olive oil"]["resolution"] == "unmatched"
    # The negligible list: by name, whatever the quantity (criterion 16).
    assert got["water"]["resolution"] == "negligible" and got["water"]["negligible"]
    assert got["salt"]["resolution"] == "negligible" and got["salt"]["negligible"]
    # Only unmatched, non-negligible names are queued.
    names = [item["name_norm"] for item in (await queue(admin_client))["items"]]
    assert names == ["minced garlic", "olive oil"]


async def test_the_negligible_list_is_configurable(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(get_settings(), "recipes_negligible_names", ["Olive Oil", "water"])
    recipes_repo.write("lantern-stew.cook", STEW)
    await rescan(admin_client)
    stew = (await recipes_by_path(admin_client))["lantern-stew.cook"]
    got = await lines_of(admin_client, stew["id"])
    assert got["olive oil"]["resolution"] == "negligible"
    assert got["water"]["resolution"] == "negligible"
    # "salt" has no quantity, so it is negligible by quantity, but it is not on
    # the list: it still waits for a name, and the queue leaves it out (16).
    assert got["salt"]["resolution"] == "unmatched" and got["salt"]["negligible"]
    assert "salt" not in [i["name_norm"] for i in (await queue(admin_client))["items"]]


# --- the queue ------------------------------------------------------------------------


async def test_the_queue_groups_names_most_used_first_with_proposals(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    basil = await make_ingredient(admin_client, "Basil", standard_key="basil")
    recipes_repo.write("lantern-stew.cook", STEW)
    recipes_repo.write("lantern-bowl.cook", BOWL)
    recipes_repo.write("pesto.cook", "Blend @fresh basil{2%cups} and @Minced Garlic{1%clove}.\n")
    await rescan(admin_client)
    body = await queue(admin_client)
    assert body["names"] == 7 and body["recipes"] == 3
    items = {i["name_norm"]: i for i in body["items"]}
    assert [i["name_norm"] for i in body["items"]][0] == "minced garlic"
    garlic = items["minced garlic"]
    assert garlic["line_count"] == 3
    assert sorted(garlic["raw_names"]) == ["Minced Garlic", "minced garlic"]
    assert [r["title"] for r in garlic["recipes"]] == ["Lantern bowl", "Lantern stew", "Pesto"]
    assert {r["path"] for r in garlic["recipes"]} == {
        "lantern-bowl.cook",
        "lantern-stew.cook",
        "pesto.cook",
    }
    # Standard tier: the entry's existing ingredient, or creating one from it.
    [proposal] = [p for p in items["fresh basil"]["proposals"] if p["tier"] == "standard"]
    assert proposal["ingredient_id"] == basil["id"] and proposal["standard_key"] == "basil"
    [proposal] = [p for p in items["olive oil"]["proposals"] if p["tier"] == "standard"]
    assert proposal["ingredient_id"] is None and proposal["standard_key"] == "olive-oil"
    assert proposal["name"] == "olive oil"
    # Similar tier: a trigram match is a suggestion, never applied (VC3).
    similar = [p for p in items["fresh basil"]["proposals"] if p["tier"] == "similar"]
    assert [p["ingredient_id"] for p in similar] == [basil["id"]]
    assert all(len(i["proposals"]) <= 3 for i in body["items"])
    assert items["lemon juice"]["proposals"] == []
    # The queue is the inbox row (UI-7.2), one row however many names wait.
    [row] = await inbox_kinds(admin_client)
    assert row["title"] == "7 recipe names to resolve"
    assert row["detail"].startswith("In 3 recipes.")
    assert row["action_label"] == "Resolve" and row["action_route"] == "/cook/recipes/resolve"


async def test_a_missing_recipes_lines_are_not_queued(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("lantern-bowl.cook", BOWL)
    await rescan(admin_client)
    assert (await queue(admin_client))["names"] == 3
    recipes_repo.remove("lantern-bowl.cook")
    await rescan(admin_client)
    assert (await queue(admin_client))["names"] == 0
    assert await inbox_kinds(admin_client) == []


# --- decisions ------------------------------------------------------------------------


async def test_one_decision_resolves_every_recipe_and_writes_one_spelling(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    garlic = await make_ingredient(admin_client, "Garlic")
    recipes_repo.write("lantern-stew.cook", STEW)
    recipes_repo.write("lantern-bowl.cook", BOWL)
    await rescan(admin_client)
    r = await decide(admin_client, name_norm="minced garlic", ingredient_id=garlic["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "matched" and body["ingredient"]["id"] == garlic["id"]
    assert (body["lines"], body["recipes"]) == (2, 2)
    assert body["remaining"] == 5  # onions, olive oil, feta, chickpeas, lemon juice
    by_path = await recipes_by_path(admin_client)
    for path in ("lantern-stew.cook", "lantern-bowl.cook"):
        line = (await lines_of(admin_client, by_path[path]["id"]))["minced garlic"]
        assert line["resolution"] == "manual" and line["ingredient_id"] == garlic["id"]
        assert line["ingredient_name"] == "Garlic"
    # One spelling, through the 1G service: kind synonym, source recipe, counted once.
    alias = (await alias_rows())["minced garlic"]
    assert str(alias.ingredient_id) == garlic["id"]
    assert (alias.kind, alias.source, alias.confirmed_count) == ("synonym", "recipe", 1)
    assert alias.last_seen_at is not None
    assert "minced garlic" not in [i["name_norm"] for i in (await queue(admin_client))["items"]]

    # Criterion 14: a later recipe with the name resolves on its own, across re-indexing.
    recipes_repo.write("later.cook", "Fry @minced garlic{1%tsp} in @olive oil{1%tsp}.\n")
    await rescan(admin_client)
    later = (await recipes_by_path(admin_client))["later.cook"]
    line = (await lines_of(admin_client, later["id"]))["minced garlic"]
    assert line["resolution"] == "alias" and line["ingredient_id"] == garlic["id"]
    recipes_repo.edit("lantern-stew.cook", "Finish with @parsley{a handful}.\n")
    await rescan(admin_client)
    line = (await lines_of(admin_client, by_path["lantern-stew.cook"]["id"]))["minced garlic"]
    assert line["resolution"] == "alias" and line["ingredient_id"] == garlic["id"]


async def test_a_name_another_ingredient_has_is_refused_not_moved(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    # An inactive ingredient's name and spellings resolve nothing (the lookup
    # reads active ingredients, as the search does), so its names wait in the
    # queue; it still holds them, so another ingredient cannot take them.
    scallion = await make_ingredient(admin_client, "Scallion")
    await spell(scallion["id"], "green onion")
    spring = await make_ingredient(admin_client, "Spring onion")
    leek = await make_ingredient(admin_client, "Leek")
    for held in (scallion, spring):
        r = await admin_client.post(f"/api/v1/ingredients/{held['id']}/deactivate")
        assert r.status_code == 200, r.text
    recipes_repo.write("tart.cook", "Bake @green onion{2} with @spring onion{1}.\n")
    recipes_repo.write("pie.cook", "Bake @Green Onion{3}.\n")
    await rescan(admin_client)
    assert [i["name_norm"] for i in (await queue(admin_client))["items"]] == [
        "green onion",
        "spring onion",
    ]
    # A spelling another ingredient has (criterion 15; UI-7.10): refused, holder named.
    r = await decide(admin_client, name_norm="green onion", ingredient_id=leek["id"])
    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["code"] == "alias_taken"
    assert error["details"]["holder"] == "Scallion"
    assert error["details"]["holder_id"] == scallion["id"]
    # Nothing moved: the spelling still names Scallion, and the lines still wait.
    assert str((await alias_rows())["green onion"].ingredient_id) == scallion["id"]
    assert "green onion" in [i["name_norm"] for i in (await queue(admin_client))["items"]]
    # A name another ingredient has, the same way.
    r = await decide(admin_client, name_norm="spring onion", ingredient_id=leek["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "alias_taken"
    assert r.json()["error"]["details"]["holder"] == "Spring onion"
    assert r.json()["error"]["details"]["holder_id"] == spring["id"]
    # An inactive ingredient cannot be chosen either; once reactivated, "Use
    # Scallion" confirms the spelling it already has and bumps its count.
    r = await decide(admin_client, name_norm="green onion", ingredient_id=scallion["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "ingredient_inactive"
    assert (
        await admin_client.post(f"/api/v1/ingredients/{scallion['id']}/activate")
    ).status_code == 200
    before = (await alias_rows())["green onion"].confirmed_count
    r = await decide(admin_client, name_norm="green onion", ingredient_id=scallion["id"])
    assert r.status_code == 200, r.text
    assert (r.json()["lines"], r.json()["recipes"], r.json()["remaining"]) == (2, 2, 1)
    after = (await alias_rows())["green onion"]
    assert after.confirmed_count == before + 1 and after.last_seen_at is not None
    # Its own name, chosen for a name that normalizes to it, writes no spelling row.
    assert (
        await admin_client.post(f"/api/v1/ingredients/{spring['id']}/activate")
    ).status_code == 200
    r = await decide(admin_client, name_norm="spring onion", ingredient_id=spring["id"])
    assert r.status_code == 200 and r.json()["remaining"] == 0
    assert "spring onion" not in await alias_rows()


async def test_creating_an_ingredient_inline_from_the_standard_list(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("lantern-stew.cook", STEW)
    recipes_repo.write("drizzle.cook", "Whisk @olive oil{3%tbsp} with @lemon juice{1%tbsp}.\n")
    await rescan(admin_client)
    r = await decide(
        admin_client,
        name_norm="olive oil",
        ingredient={"name": "olive oil", "standard_key": "olive-oil"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "created" and body["ingredient"]["name"] == "olive oil"
    assert (body["lines"], body["recipes"]) == (2, 2)
    created = (await admin_client.get(f"/api/v1/ingredients/{body['ingredient']['id']}")).json()
    assert created["slug"] == "olive-oil" and created["reconcile_state"] == "linked"
    # The name is the ingredient's own, so no spelling row is written for it.
    assert "olive oil" not in await alias_rows()
    # A typed name, with a spelling for the recipe's wording.
    r = await decide(
        admin_client,
        name_norm="lemon juice",
        ingredient={"name": "Lemon", "canonical_unit": "each"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "created"
    alias = (await alias_rows())["lemon juice"]
    assert str(alias.ingredient_id) == r.json()["ingredient"]["id"] and alias.source == "recipe"
    # A taken name names its holder, so the page can offer it.
    recipes_repo.write("zest.cook", "Grate @lemon zest{1%tsp}.\n")
    await rescan(admin_client)
    r = await decide(admin_client, name_norm="lemon zest", ingredient={"name": "lemon"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ingredient_name_taken"
    assert r.json()["error"]["details"]["holder"] == "Lemon"


async def test_a_new_ingredients_own_name_settles_other_waiting_names(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("a.cook", "Chop @parsnips{2}.\n")
    recipes_repo.write("b.cook", "Roast @parsnip{1}.\n")
    await rescan(admin_client)
    assert (await queue(admin_client))["names"] == 2
    r = await decide(admin_client, name_norm="parsnips", ingredient={"name": "Parsnip"})
    assert r.status_code == 200, r.text
    # "parsnip" is the new ingredient's own name: the re-run resolved it at once (14).
    assert r.json()["remaining"] == 0
    b = (await recipes_by_path(admin_client))["b.cook"]
    assert (await lines_of(admin_client, b["id"]))["parsnip"]["resolution"] == "alias"
    # "parsnips" was the generated plural already, so no second spelling exists.
    assert (await alias_rows())["parsnips"].kind == "inflection"


async def test_not_an_ingredient_is_recorded_and_leaves_the_queue(
    admin_client: httpx.AsyncClient,
    admin,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write(
        "wrap.cook", "Line a tin with @parchment paper{1%sheet} and @butter{10%g}.\n"
    )
    recipes_repo.write("bake.cook", "Use @Parchment paper{2%sheets}.\n")
    await rescan(admin_client)
    assert (await queue(admin_client))["names"] == 2
    r = await decide(admin_client, name_norm="parchment paper", ignore=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "ignored" and body["ingredient"] is None
    assert (body["lines"], body["recipes"], body["remaining"]) == (2, 2, 1)
    async with get_sessionmaker()() as db:
        ignored = (await db.execute(select(RecipeNameIgnore))).scalar_one()
        assert ignored.name_norm == "parchment paper" and ignored.created_by == admin.id
        resolutions = (
            await db.execute(
                select(RecipeIngredient.resolution).where(
                    RecipeIngredient.name_norm == "parchment paper"
                )
            )
        ).scalars()
        assert set(resolutions) == {"ignored"}
    assert [i["name_norm"] for i in (await queue(admin_client))["items"]] == ["butter"]
    [row] = await inbox_kinds(admin_client)
    assert row["title"] == "1 recipe name to resolve" and row["detail"].startswith("In 1 recipe.")
    # Not counted as unmapped on any recipe again: a new recipe with the name is ignored at once.
    recipes_repo.write("roll.cook", "Roll on @parchment paper{1%sheet}.\n")
    await rescan(admin_client)
    roll = (await recipes_by_path(admin_client))["roll.cook"]
    assert (await lines_of(admin_client, roll["id"]))["parchment paper"]["resolution"] == "ignored"
    assert (await queue(admin_client))["names"] == 1
    # A name nobody wrote is not found.
    assert (await decide(admin_client, name_norm="nothing here", ignore=True)).status_code == 404


async def test_a_decision_names_exactly_one_choice(admin_client: httpx.AsyncClient):
    assert (await decide(admin_client, name_norm="x")).status_code == 422
    r = await decide(admin_client, name_norm="x", ignore=True, ingredient_id=str(uuid.uuid4()))
    assert r.status_code == 422
    r = await decide(admin_client, name_norm="x", ingredient_id=str(uuid.uuid4()))
    assert r.status_code == 404  # no line has the name; nothing is looked up or written


# --- pins (criterion 17) ---------------------------------------------------------------


async def pin(client: httpx.AsyncClient, recipe_id: str, name: str, product_id: str):
    return await client.put(
        f"/api/v1/recipes/{recipe_id}/pins/{name}", json={"product_id": product_id}
    )


async def test_a_pin_must_fulfil_the_lines_ingredient_and_survives_a_rescan(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    feta_block = await make_product(admin_client, "Feta cheese", "Brindlewood feta block")
    feta = feta_block["ingredient"]
    olives = await make_product(admin_client, "Olive", "Brindlewood olives")
    recipes_repo.write("lantern-stew.cook", STEW)
    await rescan(admin_client)
    stew = (await recipes_by_path(admin_client))["lantern-stew.cook"]
    # Unresolved: nothing to fulfil yet.
    r = await pin(admin_client, stew["id"], "feta", feta_block["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "line_unresolved"
    r = await pin(admin_client, stew["id"], "no such line", feta_block["id"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_such_line"
    assert (
        await decide(admin_client, name_norm="feta", ingredient_id=feta["id"])
    ).status_code == 200
    # The wrong ingredient's product is refused, with both names in the reason.
    r = await pin(admin_client, stew["id"], "feta", olives["id"])
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "product_does_not_fulfil"
    assert r.json()["error"]["details"] == {
        "product_ingredient": "Olive",
        "line_ingredient": "Feta cheese",
    }
    assert (await pin(admin_client, stew["id"], "feta", str(uuid.uuid4()))).status_code == 404
    r = await pin(admin_client, stew["id"], "feta", feta_block["id"])
    assert r.status_code == 200, r.text
    assert r.json()["pins"] == [
        {
            "name_norm": "feta",
            "product_id": feta_block["id"],
            "product_name": "Brindlewood feta block",
            "brand": None,
        }
    ]
    # Re-indexing with the name unchanged keeps the pin; the line is rebuilt and resolved.
    recipes_repo.edit("lantern-stew.cook", "Finish with @feta{20%g}(more).\n")
    scan = await rescan(admin_client)
    assert scan["updated"] == 1
    after = await detail(admin_client, stew["id"])
    assert [p["product_id"] for p in after["pins"]] == [feta_block["id"]]
    assert all(
        line["resolution"] == "alias"
        for line in after["ingredients"]
        if line["name_norm"] == "feta"
    )
    # Re-pinning replaces; unpinning removes; unpinning twice is not found.
    other = await admin_client.post(
        "/api/v1/products", json={"ingredient_id": feta["id"], "name": "Brindlewood feta tub"}
    )
    r = await pin(admin_client, stew["id"], "feta", other.json()["id"])
    assert r.status_code == 200 and r.json()["pins"][0]["product_id"] == other.json()["id"]
    r = await admin_client.delete(f"/api/v1/recipes/{stew['id']}/pins/feta")
    assert r.status_code == 204
    assert (await detail(admin_client, stew["id"]))["pins"] == []
    r = await admin_client.delete(f"/api/v1/recipes/{stew['id']}/pins/feta")
    assert r.status_code == 404
    assert (await pin(admin_client, str(uuid.uuid4()), "feta", feta_block["id"])).status_code == 404


async def test_a_pin_follows_merged_into_and_a_merge_repoints_pins(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    keep = await make_product(admin_client, "Feta cheese", "Brindlewood feta block")
    feta = keep["ingredient"]
    dupe = await admin_client.post(
        "/api/v1/products", json={"ingredient_id": feta["id"], "name": "Brindlewood feta blk"}
    )
    dupe = dupe.json()
    recipes_repo.write("lantern-stew.cook", STEW)
    await rescan(admin_client)
    stew = (await recipes_by_path(admin_client))["lantern-stew.cook"]
    assert (
        await decide(admin_client, name_norm="feta", ingredient_id=feta["id"])
    ).status_code == 200
    assert (await pin(admin_client, stew["id"], "feta", dupe["id"])).status_code == 200
    # A product merge repoints the pin to the survivor (07, "Merges repoint recipe rows").
    r = await admin_client.post(
        f"/api/v1/products/{dupe['id']}/merge", json={"survivor_id": keep["id"]}
    )
    assert r.status_code == 200, r.text
    assert [p["product_id"] for p in (await detail(admin_client, stew["id"]))["pins"]] == [
        keep["id"]
    ]
    async with get_sessionmaker()() as db:
        assert str((await db.execute(select(RecipePin))).scalar_one().product_id) == keep["id"]
    # Naming the merged product again fulfils through merged_into and stores the survivor.
    r = await pin(admin_client, stew["id"], "feta", dupe["id"])
    assert r.status_code == 200, r.text
    assert r.json()["pins"][0]["product_id"] == keep["id"]


async def test_an_ingredient_merge_repoints_recipe_lines(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    survivor = await make_ingredient(admin_client, "Feta cheese")
    loser = await make_ingredient(admin_client, "Feta")
    recipes_repo.write("lantern-stew.cook", STEW)
    await rescan(admin_client)
    stew = (await recipes_by_path(admin_client))["lantern-stew.cook"]
    line = (await lines_of(admin_client, stew["id"]))["feta"]
    assert line["resolution"] == "alias" and line["ingredient_id"] == loser["id"]
    r = await admin_client.post(
        "/api/v1/ingredients/merge",
        json={
            "survivor_id": survivor["id"],
            "loser_id": loser["id"],
            "name": survivor["name"],
            "copy_measures": [],
        },
    )
    assert r.status_code == 200, r.text
    line = (await lines_of(admin_client, stew["id"]))["feta"]
    assert line["ingredient_id"] == survivor["id"] and line["ingredient_name"] == "Feta cheese"
    # The merged name is a legacy spelling of the survivor, so a rescan keeps it there.
    recipes_repo.edit("lantern-stew.cook", "Serve.\n")
    await rescan(admin_client)
    line = (await lines_of(admin_client, stew["id"]))["feta"]
    assert line["ingredient_id"] == survivor["id"] and line["resolution"] == "alias"


# --- the service directly --------------------------------------------------------------


async def test_two_active_ingredients_sharing_a_key_resolve_nothing(
    admin_client: httpx.AsyncClient, db_session
):
    # Accents are dropped by the normalizer, so both names share the key "jalapeno";
    # the lookup offers neither rather than guessing (VC3).
    await make_ingredient(admin_client, "Jalapeño")
    await make_ingredient(admin_client, "Jalapeno")
    ctx = await recipe_resolution.load_context(db_session)
    assert recipe_resolution.lookup(ctx, "jalapeno") is None
    # The plural is unambiguous: whichever got the generated spelling "jalapenos"
    # (the other's was skipped as taken) holds it, and the exact step finds it.
    assert recipe_resolution.lookup(ctx, "jalapenos") is not None


async def test_recipe_name_routes_need_a_session(client: httpx.AsyncClient):
    for method, path in (
        ("GET", "/api/v1/recipes/resolve"),
        ("POST", "/api/v1/recipes/resolve"),
        ("PUT", f"/api/v1/recipes/{uuid.uuid4()}/pins/feta"),
        ("DELETE", f"/api/v1/recipes/{uuid.uuid4()}/pins/feta"),
    ):
        r = await client.request(method, path)
        assert r.status_code == 401, (method, path)


# --- migration 0044 --------------------------------------------------------------------


async def test_migration_0044_round_trips(owner_conn: asyncpg.Connection):
    async def allows_ignored() -> bool:
        definition = await owner_conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_recipe_ingredient_resolution'"
        )
        return "ignored" in (definition or "")

    assert await allows_ignored()
    recipe_id = uuid.uuid4()
    await owner_conn.execute(
        "INSERT INTO recipe (id, path, title, content_hash, dirty, status, "
        "last_indexed_at, last_seen_at, created_at, updated_at) "
        "VALUES ($1, 'x.cook', 'X', 'abc', false, 'ok', now(), now(), now(), now())",
        recipe_id,
    )
    await owner_conn.execute(
        "INSERT INTO recipe_ingredient (id, recipe_id, seq, raw_name, name_norm, qty_kind, "
        "resolution, negligible, yield_mode) "
        "VALUES ($1, $2, 1, 'twine', 'twine', 'none', 'ignored', true, 'auto')",
        uuid.uuid4(),
        recipe_id,
    )
    run_alembic("downgrade", "0043")
    assert not await allows_ignored()
    assert await owner_conn.fetchval("SELECT resolution FROM recipe_ingredient") == "unmatched"
    run_alembic("upgrade", "head")
    assert await allows_ignored()
    assert await owner_conn.fetchval("SELECT version_num FROM alembic_version") >= "0044"
    await dispose_engine()  # pooled connections may hold plans against the old constraint
