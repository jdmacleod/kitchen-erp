"""Ingredients a receipt line names outright, offered to a new product (#88)."""

from __future__ import annotations

from tests.catalog_helpers import make_ingredient, seed_units_via_service


async def _in_text(client, text: str) -> list[dict]:
    r = await client.get("/api/v1/ingredients/in-text", params={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_a_catalog_ingredient_named_in_the_line_is_offered(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Broccoli")
    items = await _in_text(admin_client, "WT BROCCOLI CROWNS 10.81 F")
    assert [(i["kind"], i["name"]) for i in items][0] == ("ingredient", "Broccoli")
    assert items[0]["exact"] is True


async def test_a_plural_on_the_line_finds_its_ingredient(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Avocado")
    items = await _in_text(admin_client, "AVOCADOS HASS")
    assert [i["name"] for i in items if i["kind"] == "ingredient"] == ["Avocado"]


async def test_the_longest_phrase_comes_first_and_standard_names_follow(admin_client, db_session):
    # Nothing in the catalog: the standard list's scallion, by its spelling
    # "green onion", is offered before anything a single word would find.
    await seed_units_via_service(db_session)
    items = await _in_text(admin_client, "GREEN ONION BNCH")
    assert items[0]["kind"] == "standard" and items[0]["key"] == "scallion"
    assert items[0]["matched_spelling"] == "green onion"


async def test_abbreviations_and_codes_offer_nothing(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Chicken breast")
    assert await _in_text(admin_client, "BNLS CHKN BRST 4011") == []


async def test_a_catalog_ingredient_hides_its_standard_twin(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "broccoli")
    items = await _in_text(admin_client, "BROCCOLI")
    assert [i["kind"] for i in items] == ["ingredient"]


async def test_a_plural_finds_its_singular_standard_name(admin_client, db_session):
    # Found dogfooding: the model answers in plurals, and none of them matched.
    await seed_units_via_service(db_session)
    r = await admin_client.get(
        "/api/v1/ingredients/search", params={"q": "parsnips", "include_standard": "true"}
    )
    items = r.json()["items"]
    assert (items[0]["kind"], items[0]["key"], items[0]["exact"]) == ("standard", "parsnip", True)
    found = await _in_text(admin_client, "PARSNIPS LOOSE")
    assert found[0]["key"] == "parsnip"


async def test_a_longer_standard_name_beats_a_shorter_catalog_one(admin_client, db_session):
    # "garlic" is in the catalog; the line is garlic powder, which is not yet.
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "garlic")
    items = await _in_text(admin_client, "GARLIC POWDER JAR")
    assert [(i["kind"], i["name"]) for i in items][:2] == [
        ("standard", "garlic powder"),
        ("ingredient", "garlic"),
    ]


async def test_a_pair_printed_noun_first_is_read_the_other_way(admin_client, db_session):
    await seed_units_via_service(db_session)
    items = await _in_text(admin_client, "SQUASH BUTTERNUT EA")
    assert items[0]["key"] == "butternut-squash"
