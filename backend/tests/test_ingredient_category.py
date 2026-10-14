"""A new ingredient always has a category, prefilled from what is known (#344).

Every ingredient and USDA food here is invented.
"""

from __future__ import annotations

import pytest

from app.catalog.categories import key_from_text, stored
from app.catalog.proposals import Candidate
from app.core.db import get_sessionmaker
from app.services import proposals
from app.services.proposals import Evidence
from tests.catalog_helpers import make_ingredient, seed_units_via_service


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Frozen Vegetables", "frozen"),
        ("Pantry > Canned goods > Beans", "pantry"),
        ("Dairy & Eggs", "dairy"),
        ("canned beans", "pantry"),
        ("snack foods", None),
        ("", None),
    ],
)
def test_a_key_from_a_page_or_model_wording(text, expected):
    assert key_from_text(text) == expected


def test_a_category_is_kept_as_its_key_or_as_typed():
    assert stored(" Vegetables ") == "produce"
    assert stored("sea   vegetables") == "sea vegetables"
    assert stored("  ") is None and stored(None) is None


async def test_a_new_ingredient_without_a_category_is_refused(admin_client):
    r = await admin_client.post("/api/v1/ingredients", json={"name": "lantern root"})
    assert r.status_code == 422
    [error] = r.json()["error"]["details"]["errors"]
    assert "category_required" in error["msg"]
    r = await admin_client.post(
        "/api/v1/products", json={"name": "Lantern root", "ingredient": {"name": "lantern root"}}
    )
    assert r.status_code == 422


async def test_a_standard_entry_brings_its_own_category(admin_client, db_session):
    await seed_units_via_service(db_session)
    r = await admin_client.post(
        "/api/v1/ingredients", json={"name": "chayote", "standard_key": "chayote"}
    )
    assert r.status_code == 201 and r.json()["category_key"] == "produce"


async def test_a_category_is_stored_as_its_key(admin_client, db_session):
    await seed_units_via_service(db_session)
    made = await make_ingredient(admin_client, "lantern root", category="Vegetables")
    assert made["category"] == "produce"
    r = await admin_client.patch(f"/api/v1/ingredients/{made['id']}", json={"category": "Fish"})
    assert r.json()["category"] == "seafood"


async def suggestion(client, name: str, **params) -> dict:
    r = await client.get("/api/v1/ingredients/category-suggestion", params={"name": name, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_standard_list_suggests_first(admin_client):
    assert await suggestion(admin_client, "chayotes") == {
        "category": "produce",
        "source": "standard",
    }


async def test_a_chosen_usda_food_suggests_its_food_group(admin_client, owner_conn):
    await owner_conn.execute(
        "INSERT INTO fdc_food (fdc_id, data_type, description, category) "
        "VALUES (900031, 'sr_legacy_food', 'Lanternfish, raw', 'Finfish and Shellfish Products')"
    )
    found = await suggestion(admin_client, "lanternfish fillet", fdc_id=900031)
    assert found == {"category": "seafood", "source": "usda"}
    # Without a chosen food, the best fitting one is searched for.
    assert await suggestion(admin_client, "lanternfish") == {
        "category": "seafood",
        "source": "usda",
    }


async def test_a_captured_page_category_suggests(admin_client, admin):
    async with get_sessionmaker()() as db:
        result = await proposals.create_capture(
            db,
            user=admin,
            channel="clip",
            payload={"page": "https://harbor-light.test/p/1", "dom_text": "Lantern peas"},
            evidence=Evidence(
                candidates=[
                    Candidate("title", "Lantern peas", "page_data"),
                    Candidate("category", "Frozen Vegetables", "page_data"),
                ]
            ),
            source_url="https://harbor-light.test/p/1",
        )
        proposal_id = str(result.proposal.id)
    found = await suggestion(admin_client, "lantern peas", proposal_id=proposal_id)
    assert found == {"category": "frozen", "source": "clip"}


async def test_the_households_ingredients_with_the_same_head_suggest(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "smoked lanternseed", category="spices")
    await make_ingredient(admin_client, "lanternseed", category="spices")
    found = await suggestion(admin_client, "hot lanternseed")
    assert found == {"category": "spices", "source": "catalog"}


async def test_nothing_known_suggests_nothing(admin_client):
    assert await suggestion(admin_client, "zzyzx") == {"category": None, "source": None}
