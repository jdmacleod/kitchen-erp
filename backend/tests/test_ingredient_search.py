"""Ingredient search and creating from the standard list (03, 1G, criteria 80-81)."""

from __future__ import annotations

import statistics
import time

import asyncpg
from sqlalchemy import select

from app.catalog.names import normalize_name
from app.core.ids import new_id
from app.models.catalog import Ingredient, IngredientAlias, IngredientMeasure, IngredientRef
from app.services.spellings import add_spelling
from tests.catalog_helpers import make_ingredient, seed_units_via_service


async def _search(client, q: str, **params) -> list[dict]:
    r = await client.get("/api/v1/ingredients/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_exact_then_prefix_then_similar(admin_client, db_session):
    await seed_units_via_service(db_session)
    for name in ("Onion powder", "Onion", "Green onion", "Red onions"):
        await make_ingredient(admin_client, name)
    items = await _search(admin_client, "onion")
    names = [i["name"] for i in items]
    assert names[0] == "Onion" and items[0]["exact"] is True
    assert names.index("Onion powder") < names.index("Green onion")
    assert all(i["kind"] == "ingredient" for i in items)


async def test_a_spelling_finds_its_ingredient_once(admin_client, db_session):
    await seed_units_via_service(db_session)
    scallion = await make_ingredient(admin_client, "Scallion")
    await add_spelling(db_session, scallion["id"], "green onion")
    await add_spelling(db_session, scallion["id"], "green onion stalk")
    await db_session.commit()
    items = await _search(admin_client, "Green Onion")
    assert [i["name"] for i in items] == ["Scallion"]
    assert items[0]["matched_spelling"] == "green onion" and items[0]["exact"] is True
    # The generated plural is a spelling too.
    items = await _search(admin_client, "scallions")
    assert items[0]["name"] == "Scallion" and items[0]["exact"] is True


async def test_accents_and_punctuation_do_not_hide_an_exact_name(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Jalapeño")
    items = await _search(admin_client, "jalapeno")
    assert items[0]["name"] == "Jalapeño" and items[0]["exact"] is True


async def test_inactive_ingredients_never_match(admin_client, db_session):
    await seed_units_via_service(db_session)
    leek = await make_ingredient(admin_client, "Leek")
    await add_spelling(db_session, leek["id"], "baby leek")
    await db_session.commit()
    await admin_client.post(f"/api/v1/ingredients/{leek['id']}/deactivate")
    assert await _search(admin_client, "leek") == []
    assert await _search(admin_client, "baby leek") == []


async def test_standard_names_follow_and_hide_once_in_the_catalog(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Scallion oil")
    assert all(i["kind"] == "ingredient" for i in await _search(admin_client, "scallion"))
    items = await _search(admin_client, "scallion", include_standard="true")
    kinds = [i["kind"] for i in items]
    assert kinds == sorted(kinds)  # catalog rows first
    standard = [i for i in items if i["kind"] == "standard"]
    assert standard[0]["key"] == "scallion" and standard[0]["exact"] is True
    assert standard[0]["id"] is None and standard[0]["category_key"] == "produce"
    # A spelling of a standard entry finds it and says so.
    by_spelling = await _search(admin_client, "green onion", include_standard="true")
    assert by_spelling[0]["key"] == "scallion"
    assert by_spelling[0]["matched_spelling"] == "green onion"
    # Once an ingredient has the key as its slug, the standard name is hidden.
    r = await admin_client.post(
        "/api/v1/ingredients", json={"name": "scallion", "standard_key": "scallion"}
    )
    assert r.status_code == 201, r.text
    items = await _search(admin_client, "scallion", include_standard="true")
    assert not [i for i in items if i["kind"] == "standard" and i["key"] == "scallion"]
    # And so is one whose name a local ingredient already has.
    await make_ingredient(admin_client, "Leek")
    assert not [
        i
        for i in await _search(admin_client, "leek", include_standard="true")
        if i["kind"] == "standard" and i["name"] == "leek"
    ]


async def test_product_create_from_the_standard_list(admin_client, db_session):
    await seed_units_via_service(db_session)
    r = await admin_client.post(
        "/api/v1/products",
        json={"name": "Bag of three", "ingredient": {"name": "lemon", "standard_key": "lemon"}},
    )
    assert r.status_code == 201, r.text
    assert r.json()["ingredient"]["name"] == "lemon"
    assert r.json()["ingredient"]["canonical_unit"] == "each"
    lemon = (await db_session.execute(select(Ingredient))).scalar_one()
    assert (lemon.slug, lemon.reconcile_state, lemon.category) == ("lemon", "linked", "produce")
    refs = (await db_session.execute(select(IngredientRef))).scalars().all()
    assert [(r.system, r.external_id, r.is_preferred) for r in refs] == [("fdc", "167746", True)]
    measures = (await db_session.execute(select(IngredientMeasure))).scalars().all()
    assert {(m.label, m.source, m.confirmed) for m in measures} == {
        ("tbsp juice", "manual", False),
        ("tsp zest", "manual", False),
    }
    spellings = {
        a.name_norm: (a.kind, a.source)
        for a in (await db_session.execute(select(IngredientAlias))).scalars()
    }
    assert spellings == {"lemons": ("inflection", "generated")}


async def test_standard_spellings_are_stored(admin_client, db_session):
    await seed_units_via_service(db_session)
    r = await admin_client.post(
        "/api/v1/ingredients", json={"name": "x", "standard_key": "powdered-sugar"}
    )
    assert r.status_code == 201 and r.json()["name"] == "powdered sugar"
    assert r.json()["slug"] == "powdered-sugar"
    spellings = {
        a.name_norm: a.source for a in (await db_session.execute(select(IngredientAlias))).scalars()
    }
    assert spellings == {
        normalize_name("confectioners sugar"): "standard",
        normalize_name("icing sugar"): "standard",
    }


async def test_a_standard_spelling_another_ingredient_has_is_skipped(admin_client, db_session):
    await seed_units_via_service(db_session)
    other = await make_ingredient(admin_client, "Allium greens")
    await add_spelling(db_session, other["id"], "green onion")
    await db_session.commit()
    r = await admin_client.post(
        "/api/v1/ingredients", json={"name": "scallion", "standard_key": "scallion"}
    )
    assert r.status_code == 201, r.text
    owners = {
        a.name_norm: a.ingredient_id
        for a in (await db_session.execute(select(IngredientAlias))).scalars()
    }
    assert str(owners["green onion"]) == other["id"]
    assert str(owners["spring onion"]) == r.json()["id"]


async def test_taken_name_and_unknown_key_leave_nothing_behind(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Lemon")
    r = await admin_client.post(
        "/api/v1/products",
        json={"name": "Bag of three", "ingredient": {"name": "lemon", "standard_key": "lemon"}},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "ingredient_name_taken"
    r = await admin_client.post(
        "/api/v1/products",
        json={"name": "Mystery", "ingredient": {"name": "x", "standard_key": "no-such-entry"}},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_standard_entry"
    r = await admin_client.post(
        "/api/v1/ingredients", json={"name": "x", "standard_key": "no-such-entry"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_standard_entry"
    assert len((await db_session.execute(select(Ingredient))).scalars().all()) == 1
    assert (await db_session.execute(select(IngredientRef))).scalars().all() == []
    products = await admin_client.get("/api/v1/products")
    assert products.json()["items"] == []


async def test_a_standard_entry_already_in_the_catalog_is_409(admin_client, db_session):
    await seed_units_via_service(db_session)
    body = {"name": "garlic", "standard_key": "garlic"}
    assert (await admin_client.post("/api/v1/ingredients", json=body)).status_code == 201
    await admin_client.patch(
        f"/api/v1/ingredients/{(await _search(admin_client, 'garlic'))[0]['id']}",
        json={"name": "Garlic, local"},
    )
    r = await admin_client.post("/api/v1/ingredients", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ingredient_name_taken"


async def test_the_palette_finds_an_ingredient_by_its_spelling(admin_client, db_session):
    await seed_units_via_service(db_session)
    scallion = await make_ingredient(admin_client, "Scallion")
    await add_spelling(db_session, scallion["id"], "spring onion")
    await db_session.commit()
    r = await admin_client.get("/api/v1/search", params={"q": "spring onion"})
    hits = r.json()["ingredients"]
    assert hits[0]["label"] == "Scallion" and hits[0]["detail"] == "matches spring onion"


async def test_search_under_100ms_with_5000_ingredients_and_spellings(
    admin_client, db_session, owner_conn: asyncpg.Connection
):
    await seed_units_via_service(db_session)
    words = ["red", "green", "sweet", "wild", "baby", "smoked", "dried", "fresh", "hot", "white"]
    foods = [
        "onion",
        "basil",
        "carrot",
        "fennel",
        "ginger",
        "kale",
        "lemon",
        "mango",
        "pea",
        "leek",
    ]
    ingredients, aliases = [], []
    for i in range(5000):
        name = f"{words[i % 10]} {foods[(i // 10) % 10]} {i}"
        ingredient_id = new_id()
        ingredients.append((ingredient_id, name, f"local.x{i}"))
        aliases.append(
            (new_id(), f"{foods[(i // 10) % 10]} {words[(i * 3) % 10]} {i}", ingredient_id)
        )
    await owner_conn.executemany(
        "INSERT INTO ingredient (id, name, canonical_unit, slug) VALUES ($1, $2, 'g', $3)",
        ingredients,
    )
    await owner_conn.executemany(
        "INSERT INTO ingredient_alias (id, name_norm, ingredient_id, kind, source) "
        "VALUES ($1, $2, $3, 'synonym', 'manual')",
        aliases,
    )
    await owner_conn.execute("ANALYZE ingredient; ANALYZE ingredient_alias")
    queries = ["red on", "basil", "kale sweet", "ging", "lemon 42", "fresh", "wild mango", "leek"]
    for q in queries:
        assert await _search(admin_client, q, include_standard="true"), q

    async def measured_p95() -> float:
        timings = []
        for _ in range(7):
            for q in queries:
                started = time.perf_counter()
                r = await admin_client.get(
                    "/api/v1/ingredients/search", params={"q": q, "include_standard": "true"}
                )
                timings.append((time.perf_counter() - started) * 1000)
                assert r.status_code == 200 and r.json()["items"], q
        return statistics.quantiles(timings, n=20)[18]

    rounds = [await measured_p95()]
    if rounds[0] >= 100:
        rounds.append(await measured_p95())
    assert min(rounds) < 100, f"p95 over {len(rounds)} round(s): {rounds}"
