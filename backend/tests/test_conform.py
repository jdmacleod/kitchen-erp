"""Ingredient names conformed to the 1G rules when created (#345).

Every name and brand here is invented.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.catalog.brand_names import name_key
from app.catalog.conform import conform
from app.catalog.standard import standard_list
from app.catalog.standard_match import match_entry
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service

BRANDS = {name_key("Harbor Light")}


def test_a_form_word_after_a_comma_goes_in_front():
    found = conform("Okra, frozen")
    assert found.name == "frozen okra"
    assert any("goes in front" in why for why in found.changes)


def test_a_usda_style_meat_name_becomes_its_standard_name_or_reads_meat_first():
    assert conform("Beef, flank steak").name == "flank steak"
    assert conform("goat, shank").name == "goat shank"


def test_a_known_brand_in_front_is_dropped():
    found = conform("Harbor Light Pepper Relish", brand_keys=BRANDS)
    assert found.name == "pepper relish"
    assert any("brand" in why for why in found.changes)
    assert conform("Harbor Light", brand_keys=BRANDS).name == "harbor light"  # never emptied


def test_a_word_that_describes_the_food_is_never_taken_for_a_brand():
    brands = {name_key("Stone"), name_key("Organic"), *BRANDS}
    assert conform("stone ground cornmeal", brand_keys=brands).name == "stone ground cornmeal"
    assert conform("organic lantern beans", brand_keys=brands).name == "organic lantern beans"


def test_names_are_lowercase_but_for_the_standard_lists_proper_nouns():
    assert conform("KALAMATA OLIVE PASTE").name == "Kalamata olive paste"
    assert conform("New potatoes").name == "new potatoes"
    assert conform("monterey jack").name == "Monterey Jack"


def test_every_standard_name_is_already_conformed():
    changed = [e.name for e in standard_list().ingredients if conform(e.name).name != e.name]
    assert changed == []


@given(st.text(alphabet=st.characters(codec="utf-8"), max_size=40))
def test_conforming_twice_changes_nothing(text):
    once = conform(text, brand_keys=BRANDS).name
    assert conform(once, brand_keys=BRANDS).name == once


def test_spelling_variants_meet_the_standard_entry():
    assert match_entry("fresno chilies").key == match_entry("fresno chile").key
    assert match_entry("greek yoghurt").key == match_entry("greek yogurt").key


async def test_a_created_name_is_conformed_unless_kept(admin_client, db_session):
    await seed_units_via_service(db_session)
    made = await make_ingredient(admin_client, "Okra, frozen", category="frozen", keep_name=False)
    assert made["name"] == "frozen okra"
    kept = await make_ingredient(admin_client, "Lantern Root", category="produce", keep_name=True)
    assert kept["name"] == "Lantern Root"
    r = await admin_client.patch(f"/api/v1/ingredients/{kept['id']}", json={"name": "LANTERN ROOT"})
    assert r.json()["name"] == "lantern root"


async def test_the_preview_knows_the_households_brands(admin_client, db_session):
    await seed_units_via_service(db_session)
    relish = await make_ingredient(admin_client, "pepper relish", category="pantry")
    await make_product(admin_client, relish["id"], "Pepper relish", brand="Harbor Light")
    r = await admin_client.get(
        "/api/v1/ingredients/conformed-name", params={"name": "Harbor Light Pepper Relish"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "pepper relish"
