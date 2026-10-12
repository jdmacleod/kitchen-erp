"""Shopping lists (17, Phase 4A; criteria 1, 3, 4, 5, 6 and the archive rule of
26 as 4A builds it). Kitchens, ingredients, products and recipes are invented;
the geography is the synthetic grid.

Figures are hand-computed: a cup is 236.5882365 ml (the unit table); an
ingredient with density 0.8 g/ml makes 1 cup 189.2705892 g.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from app.core.db import get_sessionmaker
from app.main import app
from app.services import shopping_lists
from tests import geo_helpers as gh
from tests.catalog_helpers import make_ingredient, make_product
from tests.conftest import MEMBER, make_user
from tests.geo_helpers import NEAR_A, NEAR_B, make_home_base
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_scopes import token

clean_geo = gh.clean_geo

D = Decimal
LIST = "/api/v1/plan/list"
ITEMS = "/api/v1/plan/list/items"

STEW = """---
title: Harbor stew
---

Simmer @barley{200%g} with @barley{1%cup}, then @barley{200-300%g} more and @barley{2}.
Add @thyme{a few sprigs}, @thyme{2%sprigs}, @dates{4} and @dragon root{2}.
Season with @cumin{1%tsp} and @salt{1%tsp}.
"""


# --- helpers ------------------------------------------------------------------------------


@pytest.fixture
async def kitchen(admin_client: httpx.AsyncClient) -> dict:
    return await make_home_base(admin_client, "Harbor kitchen", NEAR_A)


@pytest.fixture
async def member_client() -> AsyncIterator[httpx.AsyncClient]:
    """Another member of the household, signed in on their own phone."""
    await make_user("member")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/api/v1/auth/login", json={"email": MEMBER["email"], "password": MEMBER["password"]}
        )
        assert r.status_code == 200, r.text
        yield c


async def get_list(client: httpx.AsyncClient, kitchen: dict, **params) -> dict:
    r = await client.get(LIST, params={"home_base_id": kitchen["id"], **params})
    assert r.status_code == 200, r.text
    return r.json()


async def add(client: httpx.AsyncClient, kitchen: dict, **fields) -> dict:
    body = {"home_base_id": kitchen["id"], "kind": "ingredient", **fields}
    r = await client.post(ITEMS, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def amounts(item: dict) -> list[tuple[str, str]]:
    return [(a["amount"], a["unit"]) for a in item["amounts"]]


async def indexed(client: httpx.AsyncClient, repo: TempRepo, path: str, text: str) -> dict:
    repo.write(path, text)
    repo.commit()
    r = await client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200, r.text
    return {i["path"]: i for i in (await client.get("/api/v1/recipes")).json()["items"]}[path]


async def from_recipe(client: httpx.AsyncClient, kitchen: dict, recipe_id: str, scale="1") -> dict:
    r = await client.post(
        f"{ITEMS}/from-recipe",
        json={"home_base_id": kitchen["id"], "recipe_id": recipe_id, "scale": scale},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- 1: one open list per kitchen ----------------------------------------------------------


async def test_the_list_needs_a_session_and_no_token_scope_reaches_it(
    admin_client: httpx.AsyncClient,
):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as nobody:
        r = await nobody.get(LIST, params={"home_base_id": str(uuid.uuid4())})
        assert r.status_code == 401
    headers = await token(admin_client, "products:read")
    admin_client.cookies.clear()
    r = await admin_client.get(LIST, params={"home_base_id": str(uuid.uuid4())}, headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_scope"


async def test_the_list_takes_an_explicit_kitchen(admin_client: httpx.AsyncClient, kitchen: dict):
    # Never inferred from the session or the setting (09): without one, a 422.
    await admin_client.put("/api/v1/me/settings", json={"current_home_base_id": kitchen["id"]})
    r = await admin_client.get(LIST)
    assert r.status_code == 422, r.text
    assert (await admin_client.get("/api/v1/plan/lists")).status_code == 422
    r = await admin_client.get(LIST, params={"home_base_id": str(uuid.uuid4())})
    assert r.status_code == 404


async def test_first_read_creates_the_open_list_once(
    admin_client: httpx.AsyncClient, kitchen: dict, app_conn: asyncpg.Connection
):
    first = await get_list(admin_client, kitchen)
    assert first["status"] == "open" and first["home_base_name"] == "Harbor kitchen"
    assert first["unchecked"] == [] and first["checked"] == [] and first["archived_at"] is None
    assert (await get_list(admin_client, kitchen))["id"] == first["id"]
    # Another kitchen has its own list.
    ridge = await make_home_base(admin_client, "Ridge kitchen", NEAR_B)
    assert (await get_list(admin_client, ridge))["id"] != first["id"]
    # A second open list is refused by the database, whoever asks.
    with pytest.raises(asyncpg.UniqueViolationError):
        await app_conn.execute(
            "INSERT INTO shopping_list (id, home_base_id, status) VALUES ($1, $2, 'open')",
            uuid.uuid4(),
            uuid.UUID(kitchen["id"]),
        )
    # An archived one beside it is fine.
    await app_conn.execute(
        "INSERT INTO shopping_list (id, home_base_id, status, archived_at) "
        "VALUES ($1, $2, 'archived', now())",
        uuid.uuid4(),
        uuid.UUID(kitchen["id"]),
    )


async def test_two_concurrent_first_reads_yield_one_list(
    admin_client: httpx.AsyncClient, kitchen: dict
):
    home_base_id = uuid.UUID(kitchen["id"])
    make = get_sessionmaker()

    async def first_read() -> uuid.UUID:
        async with make() as db:
            return (await shopping_lists.open_list(db, home_base_id)).id

    ids = await asyncio.gather(*(first_read() for _ in range(4)))
    assert len(set(ids)) == 1
    assert (await get_list(admin_client, kitchen))["id"] == str(ids[0])


# --- 3: an ingredient or a product, never both --------------------------------------------------


async def test_an_item_names_an_ingredient_or_a_product_never_both(
    admin_client: httpx.AsyncClient,
    kitchen: dict,
    app_conn: asyncpg.Connection,
    admin,
):
    barley = await make_ingredient(admin_client, "Barley")
    bag = await make_product(admin_client, barley["id"], "Barley bag", pack_qty="1", pack_unit="kg")
    ok = await add(admin_client, kitchen, ingredient_id=barley["id"], min_quality=3)
    assert ok["kind"] == "ingredient" and ok["ingredient"]["name"] == "Barley"
    assert ok["product"] is None and ok["min_quality"] == 3
    prod = await add(admin_client, kitchen, kind="product", product_id=bag["id"])
    assert prod["product"]["name"] == "Barley bag" and prod["ingredient"] is None
    for body in (
        {"kind": "ingredient", "ingredient_id": barley["id"], "product_id": bag["id"]},
        {"kind": "ingredient"},
        {"kind": "product", "ingredient_id": barley["id"]},
        {
            "kind": "product",
            "product_id": bag["id"],
            "min_quality": 3,
        },  # quality is an ingredient's
        {"kind": "ingredient", "ingredient_id": barley["id"], "min_quality": 6},
        {
            "kind": "ingredient",
            "ingredient_id": barley["id"],
            "amounts": [{"amount": "0", "unit": "g"}],
        },
        {
            "kind": "ingredient",
            "ingredient_id": barley["id"],
            "amounts": [{"amount": "1", "unit": "sprig"}],
        },
    ):
        r = await admin_client.post(ITEMS, json={"home_base_id": kitchen["id"], **body})
        assert r.status_code == 422, (body, r.text)
    r = await admin_client.post(
        ITEMS,
        json={
            "home_base_id": kitchen["id"],
            "kind": "ingredient",
            "ingredient_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 404
    # The database checks again, whatever the client said.
    list_id = uuid.UUID(ok["list_id"])

    async def insert(kind: str, ingredient_id, product_id, min_quality=None) -> None:
        await app_conn.execute(
            "INSERT INTO shopping_list_item (id, list_id, kind, ingredient_id, product_id, "
            "min_quality, added_by, added_from) VALUES ($1, $2, $3, $4, $5, $6, $7, 'manual')",
            uuid.uuid4(),
            list_id,
            kind,
            ingredient_id,
            product_id,
            min_quality,
            admin.id,
        )

    for args in (
        ("ingredient", uuid.UUID(barley["id"]), uuid.UUID(bag["id"])),
        ("ingredient", None, None),
        ("product", uuid.UUID(barley["id"]), None),
        ("product", None, uuid.UUID(bag["id"]), 3),
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(*args)
    with pytest.raises(asyncpg.CheckViolationError):
        await app_conn.execute(
            "INSERT INTO shopping_list_item_qty (id, item_id, seq, amount, unit) "
            "VALUES ($1, $2, 1, 0, 'g')",
            uuid.uuid4(),
            uuid.UUID(ok["id"]),
        )


# --- add, patch, remove -------------------------------------------------------------------------


async def test_add_patch_and_remove_an_item(admin_client: httpx.AsyncClient, kitchen: dict):
    barley = await make_ingredient(admin_client, "Barley")
    item = await add(
        admin_client,
        kitchen,
        ingredient_id=barley["id"],
        amounts=[{"amount": "250.5", "unit": "g"}, {"amount": "2", "unit": "each"}],
        note="the pearl kind",
        deferrable=True,
    )
    assert amounts(item) == [("250.5", "g"), ("2", "each")]
    assert item["note"] == "the pearl kind" and item["deferrable"] is True
    assert item["added_from"] == "manual" and item["added_by"]["display_name"] == "Admin"
    assert item["claimed_by"] is None and item["checked_at"] is None
    assert item["carried_from_item_id"] is None and item["recipe_id"] is None
    listed = await get_list(admin_client, kitchen)
    assert [i["id"] for i in listed["unchecked"]] == [item["id"]]

    r = await admin_client.patch(
        f"{ITEMS}/{item['id']}",
        json={
            "updated_at": item["updated_at"],
            "amounts": [{"amount": "1", "unit": "kg"}],
            "note": None,
            "deferrable": False,
            "min_quality": 4,
        },
    )
    assert r.status_code == 200, r.text
    edited = r.json()
    assert amounts(edited) == [("1", "kg")] and edited["note"] is None
    assert edited["deferrable"] is False and edited["min_quality"] == 4
    assert edited["updated_at"] > item["updated_at"]
    # A patch that states nothing but the token leaves the rest alone.
    r = await admin_client.patch(f"{ITEMS}/{item['id']}", json={"updated_at": edited["updated_at"]})
    assert r.status_code == 200 and amounts(r.json()) == [("1", "kg")]
    assert (await admin_client.patch(f"{ITEMS}/{item['id']}", json={})).status_code == 422

    r = await admin_client.delete(f"{ITEMS}/{item['id']}")
    assert r.status_code == 204
    assert (await get_list(admin_client, kitchen))["unchecked"] == []
    assert (await admin_client.delete(f"{ITEMS}/{item['id']}")).status_code == 404
    assert (
        await admin_client.patch(f"{ITEMS}/{item['id']}", json={"updated_at": item["updated_at"]})
    ).status_code == 404


async def test_min_quality_is_refused_on_a_product_item_by_patch_too(
    admin_client: httpx.AsyncClient, kitchen: dict
):
    barley = await make_ingredient(admin_client, "Barley")
    bag = await make_product(admin_client, barley["id"], "Barley bag")
    item = await add(admin_client, kitchen, kind="product", product_id=bag["id"])
    r = await admin_client.patch(
        f"{ITEMS}/{item['id']}", json={"updated_at": item["updated_at"], "min_quality": 2}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "min_quality_on_product"


async def test_add_is_idempotent_under_a_key(admin_client: httpx.AsyncClient, kitchen: dict):
    barley = await make_ingredient(admin_client, "Barley")
    body = {"home_base_id": kitchen["id"], "kind": "ingredient", "ingredient_id": barley["id"]}
    headers = {"Idempotency-Key": "phone-tap-1"}
    first = await admin_client.post(ITEMS, json=body, headers=headers)
    again = await admin_client.post(ITEMS, json=body, headers=headers)
    assert first.status_code == 201 and again.status_code == 201
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["id"] == first.json()["id"]
    assert len((await get_list(admin_client, kitchen))["unchecked"]) == 1


# --- 6: the optimistic token ----------------------------------------------------------------


async def test_a_stale_updated_at_is_a_409_with_the_current_row(
    admin_client: httpx.AsyncClient, member_client: httpx.AsyncClient, kitchen: dict
):
    barley = await make_ingredient(admin_client, "Barley")
    item = await add(admin_client, kitchen, ingredient_id=barley["id"], note="one")
    # Someone else edits first.
    r = await member_client.patch(
        f"{ITEMS}/{item['id']}", json={"updated_at": item["updated_at"], "note": "two"}
    )
    assert r.status_code == 200
    current = r.json()
    r = await admin_client.patch(
        f"{ITEMS}/{item['id']}", json={"updated_at": item["updated_at"], "note": "three"}
    )
    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["code"] == "stale_item"
    assert error["details"]["item"]["note"] == "two"
    assert error["details"]["item"]["updated_at"] == current["updated_at"]
    # Nothing was written; the current token works.
    assert (await get_list(admin_client, kitchen))["unchecked"][0]["note"] == "two"
    r = await admin_client.patch(
        f"{ITEMS}/{item['id']}", json={"updated_at": current["updated_at"], "note": "three"}
    )
    assert r.status_code == 200 and r.json()["note"] == "three"
    # A check moves the token too: a form opened before it is stale.
    await member_client.post(f"{ITEMS}/{item['id']}/check")
    r = await admin_client.patch(
        f"{ITEMS}/{item['id']}", json={"updated_at": r.json()["updated_at"], "note": "four"}
    )
    assert r.status_code == 409


# --- 5: soft claims and check-offs ------------------------------------------------------------


async def test_claim_shows_the_name_and_a_check_by_another_user_clears_it(
    admin_client: httpx.AsyncClient, member_client: httpx.AsyncClient, kitchen: dict
):
    barley = await make_ingredient(admin_client, "Barley")
    item = await add(admin_client, kitchen, ingredient_id=barley["id"])
    r = await admin_client.post(f"{ITEMS}/{item['id']}/claim")
    assert r.status_code == 200, r.text
    claimed = r.json()
    assert claimed["claimed_by"] == {"id": claimed["added_by"]["id"], "display_name": "Admin"}
    assert claimed["claimed_at"] is not None
    assert (await get_list(member_client, kitchen))["unchecked"][0]["claimed_by"][
        "display_name"
    ] == "Admin"
    # Another member may check it; the check clears the claim and records who.
    r = await member_client.post(f"{ITEMS}/{item['id']}/check")
    assert r.status_code == 200, r.text
    checked = r.json()
    assert checked["claimed_by"] is None and checked["claimed_at"] is None
    assert checked["checked_by"]["display_name"] == "Member" and checked["checked_at"] is not None
    listed = await get_list(admin_client, kitchen)
    assert listed["unchecked"] == [] and [i["id"] for i in listed["checked"]] == [item["id"]]
    # A retried tap is a 200 with no change.
    r = await admin_client.post(f"{ITEMS}/{item['id']}/check")
    assert r.status_code == 200
    assert r.json()["checked_at"] == checked["checked_at"]
    assert r.json()["checked_by"]["display_name"] == "Member"
    # Uncheck puts it back, unclaimed; unclaim and uncheck are idempotent too.
    r = await admin_client.delete(f"{ITEMS}/{item['id']}/check")
    assert (
        r.status_code == 200 and r.json()["checked_at"] is None and r.json()["checked_by"] is None
    )
    assert (await admin_client.delete(f"{ITEMS}/{item['id']}/check")).status_code == 200
    assert (await admin_client.delete(f"{ITEMS}/{item['id']}/claim")).status_code == 200
    # A later claim replaces the earlier one: it is only a display.
    await admin_client.post(f"{ITEMS}/{item['id']}/claim")
    r = await member_client.post(f"{ITEMS}/{item['id']}/claim")
    assert r.json()["claimed_by"]["display_name"] == "Member"
    r = await member_client.delete(f"{ITEMS}/{item['id']}/claim")
    assert r.json()["claimed_by"] is None
    assert (await admin_client.post(f"{ITEMS}/{uuid.uuid4()}/claim")).status_code == 404


# --- 4: from a recipe ------------------------------------------------------------------------


async def pantry(client: httpx.AsyncClient) -> dict:
    """Barley (g, density 0.8), thyme (g), dates (each) with a box, cumin (g), salt (g)."""
    barley = await make_ingredient(
        client, "Barley", density_g_per_ml="0.8", density_source="manual"
    )
    thyme = await make_ingredient(client, "Thyme")
    dates = await make_ingredient(client, "Dates", canonical_unit="each")
    box = await make_product(client, dates["id"], "Dates box", pack_qty="20", pack_unit="each")
    cumin = await make_ingredient(client, "Cumin")
    salt = await make_ingredient(client, "Salt")
    return {
        "barley": barley,
        "thyme": thyme,
        "dates": dates,
        "box": box,
        "cumin": cumin,
        "salt": salt,
    }


ONE_CUP_G = D("236.5882365") * D("0.8")  # 189.2705892


async def test_a_recipe_at_scale_2_aggregates_as_06_settled(
    admin_client: httpx.AsyncClient,
    kitchen: dict,
    recipes_repo: TempRepo,  # noqa: F811
):
    p = await pantry(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "stew.cook", STEW)
    r = await admin_client.put(
        f"/api/v1/recipes/{recipe['id']}/pins/dates", json={"product_id": p["box"]["id"]}
    )
    assert r.status_code == 200, r.text
    detail = (await admin_client.get(f"/api/v1/recipes/{recipe['id']}")).json()
    by_name = {}
    for line in detail["ingredients"]:
        by_name.setdefault(line["name_norm"], line)
    assert by_name["dragon root"]["resolution"] == "unmatched"
    assert by_name["salt"]["resolution"] == "negligible"  # by name, the default list

    out = await from_recipe(admin_client, kitchen, recipe["id"], scale="2")
    assert out["skipped"] == [{"raw_name": "dragon root", "reason": "unmatched"}]
    assert out["added"] == 4 and out["merged"] == 4  # barley ×3 more, thyme ×1 more
    items = {
        (i["kind"], (i["ingredient"] or i["product"])["name"]): i for i in out["list"]["unchecked"]
    }
    assert set(items) == {
        ("ingredient", "Barley"),
        ("ingredient", "Thyme"),
        ("product", "Dates box"),
        ("ingredient", "Cumin"),
    }
    barley = items[("ingredient", "Barley")]
    # 200 g, 1 cup (converts through density) and the high of 200-300 g, all
    # doubled, in one canonical row; the bare "2" is a count no measure explains,
    # so it keeps its own row, doubled.
    grams = (D("200") + ONE_CUP_G + D("300")) * 2
    assert amounts(barley) == [(format(grams.normalize(), "f"), "g"), ("4", "each")]
    assert barley["added_from"] == "recipe" and barley["recipe_id"] == recipe["id"]
    thyme = items[("ingredient", "Thyme")]
    # A text quantity is a note and no row; "2 sprigs" names no unit, so it joins the note.
    assert amounts(thyme) == [] and thyme["note"] == "a few sprigs; 4 sprigs"
    dates = items[("product", "Dates box")]
    assert dates["ingredient"] is None and amounts(dates) == [("8", "each")]
    assert dates["added_from"] == "recipe"
    cumin = items[("ingredient", "Cumin")]
    # 1 tsp of a mass ingredient with no density: the pair stays in teaspoons.
    assert amounts(cumin) == [("2", "tsp")]
    assert ("ingredient", "Salt") not in items  # negligible by name: not on the list

    # Again: the same items, folded in once more; nothing new appears.
    again = await from_recipe(admin_client, kitchen, recipe["id"], scale="2")
    assert again["added"] == 0 and again["merged"] == 8
    assert len(again["list"]["unchecked"]) == 4
    barley = next(i for i in again["list"]["unchecked"] if i["id"] == barley["id"])
    assert amounts(barley) == [(format((grams * 2).normalize(), "f"), "g"), ("8", "each")]
    thyme = next(i for i in again["list"]["unchecked"] if i["id"] == thyme["id"])
    assert thyme["note"] == "a few sprigs; 4 sprigs"


async def test_a_recipe_merges_into_an_item_already_on_the_list(
    admin_client: httpx.AsyncClient,
    kitchen: dict,
    recipes_repo: TempRepo,  # noqa: F811
):
    barley = await make_ingredient(admin_client, "Barley")
    manual = await add(
        admin_client,
        kitchen,
        ingredient_id=barley["id"],
        amounts=[{"amount": "100", "unit": "g"}],
        note="pearl",
    )
    recipe = await indexed(admin_client, recipes_repo, "bowl.cook", "Serve @barley{250%g}.\n")
    out = await from_recipe(admin_client, kitchen, recipe["id"])
    assert out["added"] == 0 and out["merged"] == 1 and out["skipped"] == []
    [item] = out["list"]["unchecked"]
    assert item["id"] == manual["id"] and amounts(item) == [("350", "g")]
    assert item["added_from"] == "manual" and item["note"] == "pearl"  # the row keeps its origin
    # A checked item is done with: the recipe adds a fresh one beside it.
    await admin_client.post(f"{ITEMS}/{item['id']}/check")
    out = await from_recipe(admin_client, kitchen, recipe["id"])
    assert out["added"] == 1 and len(out["list"]["unchecked"]) == 1
    assert amounts(out["list"]["unchecked"][0]) == [("250", "g")]
    r = await admin_client.post(
        f"{ITEMS}/from-recipe", json={"home_base_id": kitchen["id"], "recipe_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404
    r = await admin_client.post(
        f"{ITEMS}/from-recipe",
        json={"home_base_id": kitchen["id"], "recipe_id": recipe["id"], "scale": "0"},
    )
    assert r.status_code == 422


# --- archive: keep the old list readable, carry unchecked items forward ----------------------


async def test_archive_carries_unchecked_items_forward_and_keeps_the_old_list(
    admin_client: httpx.AsyncClient, member_client: httpx.AsyncClient, kitchen: dict
):
    barley = await make_ingredient(admin_client, "Barley")
    thyme = await make_ingredient(admin_client, "Thyme")
    keep = await add(
        admin_client,
        kitchen,
        ingredient_id=barley["id"],
        amounts=[{"amount": "200", "unit": "g"}, {"amount": "2", "unit": "each"}],
        note="pearl",
        deferrable=True,
        min_quality=2,
    )
    await member_client.post(f"{ITEMS}/{keep['id']}/claim")
    done = await add(admin_client, kitchen, ingredient_id=thyme["id"])
    await admin_client.post(f"{ITEMS}/{done['id']}/check")
    old_id = keep["list_id"]

    r = await admin_client.post("/api/v1/plan/list/archive", json={"home_base_id": kitchen["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["archived"]["id"] == old_id and out["archived"]["status"] == "archived"
    assert out["archived"]["archived_at"] is not None
    # The old rows are untouched: still claimed, still unchecked, beside the checked one.
    [old_keep] = out["archived"]["unchecked"]
    assert old_keep["id"] == keep["id"] and old_keep["claimed_by"]["display_name"] == "Member"
    assert [i["id"] for i in out["archived"]["checked"]] == [done["id"]]
    # The new list carries the unchecked item forward as a new row, no claim.
    new = out["list"]
    assert new["id"] != old_id and new["status"] == "open" and new["checked"] == []
    [carried] = new["unchecked"]
    assert carried["id"] != keep["id"] and carried["carried_from_item_id"] == keep["id"]
    assert carried["ingredient"]["id"] == barley["id"]
    assert amounts(carried) == [("200", "g"), ("2", "each")]
    assert carried["note"] == "pearl" and carried["deferrable"] is True
    assert carried["min_quality"] == 2 and carried["added_from"] == "manual"
    assert carried["added_by"]["display_name"] == "Admin"
    assert carried["claimed_by"] is None and carried["checked_at"] is None
    assert (await get_list(admin_client, kitchen))["id"] == new["id"]

    # The archived list stays readable by id, and is listed with its counts.
    archived = await get_list(admin_client, kitchen, list_id=old_id)
    assert archived["status"] == "archived" and len(archived["checked"]) == 1
    r = await admin_client.get("/api/v1/plan/lists", params={"home_base_id": kitchen["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [
        {
            "id": old_id,
            "home_base_id": kitchen["id"],
            "status": "archived",
            "created_at": archived["created_at"],
            "archived_at": archived["archived_at"],
            "item_count": 2,
            "unchecked_count": 1,
        }
    ]
    r = await admin_client.get(
        "/api/v1/plan/lists", params={"home_base_id": kitchen["id"], "status": "open"}
    )
    assert [i["id"] for i in r.json()["items"]] == [new["id"]]
    # Another kitchen's lists are not this kitchen's.
    ridge = await make_home_base(admin_client, "Ridge kitchen", NEAR_B)
    r = await admin_client.get(LIST, params={"home_base_id": ridge["id"], "list_id": old_id})
    assert r.status_code == 404
    # Its items no longer change.
    for method, path in (
        ("POST", f"{ITEMS}/{keep['id']}/check"),
        ("DELETE", f"{ITEMS}/{keep['id']}/claim"),
        ("DELETE", f"{ITEMS}/{keep['id']}"),
    ):
        r = await admin_client.request(method, path)
        assert r.status_code == 409 and r.json()["error"]["code"] == "list_archived", path
    r = await admin_client.patch(
        f"{ITEMS}/{keep['id']}", json={"updated_at": old_keep["updated_at"], "note": "x"}
    )
    assert r.status_code == 409


async def test_archive_without_carry_over_starts_empty(
    admin_client: httpx.AsyncClient, kitchen: dict
):
    barley = await make_ingredient(admin_client, "Barley")
    await add(admin_client, kitchen, ingredient_id=barley["id"])
    r = await admin_client.post(
        "/api/v1/plan/list/archive", json={"home_base_id": kitchen["id"], "carry_over": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["list"]["unchecked"] == [] and len(r.json()["archived"]["unchecked"]) == 1
    # Archiving an empty list still opens the next one.
    r = await admin_client.post("/api/v1/plan/list/archive", json={"home_base_id": kitchen["id"]})
    assert r.status_code == 200
    lists = (
        await admin_client.get("/api/v1/plan/lists", params={"home_base_id": kitchen["id"]})
    ).json()
    assert [i["item_count"] for i in lists["items"]] == [0, 1]
    assert (await admin_client.post("/api/v1/plan/list/archive", json={})).status_code == 422


async def test_qty_rows_go_with_their_item(
    admin_client: httpx.AsyncClient, kitchen: dict, app_conn: asyncpg.Connection
):
    barley = await make_ingredient(admin_client, "Barley")
    item = await add(
        admin_client, kitchen, ingredient_id=barley["id"], amounts=[{"amount": "1", "unit": "kg"}]
    )
    rows = await app_conn.fetch(
        "SELECT seq, amount, unit FROM shopping_list_item_qty WHERE item_id = $1",
        uuid.UUID(item["id"]),
    )
    assert [(r["seq"], r["amount"], r["unit"]) for r in rows] == [(1, D("1"), "kg")]
    await admin_client.delete(f"{ITEMS}/{item['id']}")
    assert await app_conn.fetchval("SELECT count(*) FROM shopping_list_item_qty") == 0
