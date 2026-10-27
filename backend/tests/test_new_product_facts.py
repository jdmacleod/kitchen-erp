"""A new product starts with the facts its words give (03, 1H amendment 2026-10-27).

Ingredients, brands and products are invented.
"""

from __future__ import annotations

from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service


async def test_counter_meat_with_no_brand_is_weighed_and_keeps_its_facts(admin_client, db_session):
    await seed_units_via_service(db_session)
    thigh = await make_ingredient(admin_client, "tarn hen thigh", category="meat")
    made = await make_product(admin_client, thigh["id"], "Boneless skinless hen thighs, chicken")
    assert made["kind"] == "random_weight"
    assert made["attributes"] == {"species": "chicken", "bone": "boneless", "skin": "skinless"}

    # A brand makes it a packaged product; the facts still come from the words.
    packed = await make_product(
        admin_client, thigh["id"], "Organic bone-in hen thighs, chicken", brand="Fernhill Farms"
    )
    assert packed["kind"] == "branded"
    assert packed["attributes"] == {"species": "chicken", "bone": "bone_in", "organic": True}


async def test_the_persons_attributes_win_over_the_words(admin_client, db_session):
    await seed_units_via_service(db_session)
    beef = await make_ingredient(admin_client, "tarn ground beef", category="meat")
    made = await make_product(
        admin_client,
        beef["id"],
        "Tarn 80/20 beef, ground",
        attributes={"species": "beef", "lean_pct": 85},
    )
    assert made["attributes"] == {"species": "beef", "form": "ground", "lean_pct": 85}


async def test_a_cultivar_is_the_variety_of_a_product_of_its_commodity(admin_client, db_session):
    await seed_units_via_service(db_session)
    apple = await make_ingredient(admin_client, "apple", category="produce")
    made = await make_product(admin_client, apple["id"], "Organic Honeycrisp apples")
    assert made["attributes"] == {"organic": True, "variety": "Honeycrisp"}


async def test_an_organic_pantry_product_is_marked_organic(admin_client, db_session):
    await seed_units_via_service(db_session)
    beans = await make_ingredient(admin_client, "tarn beans", category="pantry")
    made = await make_product(admin_client, beans["id"], "Organic tarn beans", brand="Fernhill")
    assert made["attributes"] == {"organic": True}
