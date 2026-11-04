"""USDA's portion offered on a recipe line that can't convert, and "Use it".

A line without a bridge carries the one USDA offer that would convert it: a
density for a volume, the measure its unit names, or for a bare count the
measure for one. Taking it from the line stores it unconfirmed, as the review
on Needs a bridge does, and leaves the ingredient's other offers on that review.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import httpx

from app.services.usda import import_usda
from tests.catalog_helpers import make_ingredient, write_usda_fixture
from tests.pricebook_helpers import make_location, make_product, shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_recipe_costing import cost, indexed, lines_by_text
from tests.test_usda_review import FDC_DIR, _ref

LINES = """\
Whisk @flour{1%cup} and @flour{2%tbsp} with @garlic{2%cloves}.

Add @sorrel{1%cup}.
"""


async def _setup(admin_client, db_session, owner_conn, tmp_path: Path) -> dict:
    write_usda_fixture(tmp_path / FDC_DIR)
    await import_usda(db_session, tmp_path / FDC_DIR)
    flour = await make_ingredient(admin_client, "Flour")
    garlic = await make_ingredient(admin_client, "Garlic")
    await make_ingredient(admin_client, "Sorrel")  # no USDA reference: no offer
    await _ref(owner_conn, flour["id"], 1001)
    await _ref(owner_conn, garlic["id"], 1002)
    return {"flour": flour, "garlic": garlic}


async def test_an_unconvertible_line_carries_the_offer_that_would_convert_it(
    admin_client: httpx.AsyncClient,
    db_session,
    owner_conn: asyncpg.Connection,
    tmp_path: Path,
    recipes_repo: TempRepo,  # noqa: F811
):
    made = await _setup(admin_client, db_session, owner_conn, tmp_path)
    recipe = await indexed(admin_client, recipes_repo, "harbor-dough.cook", LINES)
    got = lines_by_text(await cost(admin_client, recipe["id"]))

    cup = got[("flour", "cup")]
    assert cup["failure_code"] == "no_density"
    assert cup["usda_offer"]["kind"] == "density"
    assert cup["usda_offer"]["ingredient_id"] == made["flour"]["id"]
    assert cup["usda_offer"]["text"].startswith("1 cup = ")
    # A tablespoon line is offered USDA's tablespoon portion, not the cup.
    assert got[("flour", "tbsp")]["usda_offer"]["text"].startswith("1 tablespoon = ")

    clove = got[("garlic", "cloves")]
    assert clove["failure_code"] == "unknown_measure"
    assert clove["usda_offer"] == {
        "ingredient_id": made["garlic"]["id"],
        "kind": "measure",
        "density_portion_id": None,
        "measure_label": "clove",
        "text": "1 clove = 3 g",
    }
    assert got[("sorrel", "cup")]["usda_offer"] is None


async def test_use_it_converts_the_line_and_keeps_the_rest_on_the_review(
    admin_client: httpx.AsyncClient,
    db_session,
    owner_conn: asyncpg.Connection,
    tmp_path: Path,
    recipes_repo: TempRepo,  # noqa: F811
):
    made = await _setup(admin_client, db_session, owner_conn, tmp_path)
    recipe = await indexed(admin_client, recipes_repo, "harbor-dough.cook", LINES)
    offer = lines_by_text(await cost(admin_client, recipe["id"]))[("garlic", "cloves")][
        "usda_offer"
    ]

    r = await admin_client.post(
        f"/api/v1/usda/review/{offer['ingredient_id']}",
        json={"measures": [offer["measure_label"]], "mark_reviewed": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == 1

    after = lines_by_text(await cost(admin_client, recipe["id"]))[("garlic", "cloves")]
    assert after["status"] == "unpriced", "converted; nothing prices garlic yet"
    assert after["quantity"]["canonical_qty"] == "6"
    assert after["bridge_confirmed"] is False
    assert after["usda_offer"] is None
    reviewed = await owner_conn.fetchval(
        "SELECT usda_reviewed_fdc_id FROM ingredient WHERE id = $1",
        uuid.UUID(made["garlic"]["id"]),
    )
    assert reviewed is None, "taking one offer from a recipe leaves the review open"


async def test_a_bare_count_is_offered_the_weight_of_one_and_then_converts(
    admin_client: httpx.AsyncClient,
    db_session,
    owner_conn: asyncpg.Connection,
    tmp_path: Path,
    recipes_repo: TempRepo,  # noqa: F811
):
    await _setup(admin_client, db_session, owner_conn, tmp_path)
    onion = await make_ingredient(admin_client, "Onion")
    await owner_conn.execute(
        "INSERT INTO ref_usda_portion (id, fdc_id, food_description, portion_label,"
        " portion_amount, portion_unit, gram_weight, data_type)"
        " VALUES ($1, 1005, 'Onions, raw', '1 medium', 1, 'medium', 110, 'sr_legacy_food')",
        uuid.uuid4(),
    )
    await _ref(owner_conn, onion["id"], 1005)
    recipe = await indexed(admin_client, recipes_repo, "harbor-onions.cook", "Slice @onion{2}.\n")
    line = lines_by_text(await cost(admin_client, recipe["id"]))[("onion", None)]
    assert line["failure_code"] == "unknown_measure"
    assert line["usda_offer"]["text"] == "1 medium = 110 g"

    r = await admin_client.post(
        f"/api/v1/usda/review/{onion['id']}",
        json={"measures": [line["usda_offer"]["measure_label"]], "mark_reviewed": False},
    )
    assert r.status_code == 200, r.text
    after = lines_by_text(await cost(admin_client, recipe["id"]))[("onion", None)]
    assert after["quantity"]["canonical_qty"] == "220"


async def test_an_unpinned_count_line_asks_for_a_measure_not_a_pack_size(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    """The pack is set aside for an unpinned line ("2 each" is two pieces, never two
    packs), so the product's pack size can't help it; one piece's weight can."""
    pantry = await make_location(admin_client, "Harbor Market", "Harbor Market")
    leek = await make_product(admin_client, "Leek", "Leek, loose", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, leek["id"], pantry["id"], "3.00")
    recipe = await indexed(admin_client, recipes_repo, "harbor-leeks.cook", "Trim @leek{2}.\n")
    line = lines_by_text(await cost(admin_client, recipe["id"]))[("leek", None)]
    assert (line["status"], line["failure_code"]) == ("unconvertible", "unknown_measure")


async def _portion(owner_conn: asyncpg.Connection, fdc_id: int, unit: str, grams: int) -> None:
    await owner_conn.execute(
        "INSERT INTO ref_usda_portion (id, fdc_id, food_description, portion_label,"
        " portion_amount, portion_unit, gram_weight, data_type)"
        " VALUES ($1, $2, 'Bread, rye', $3, 1, $4, $5, 'sr_legacy_food')",
        uuid.uuid4(),
        fdc_id,
        f"1 {unit}",
        unit,
        grams,
    )


async def test_a_qualified_offer_serves_its_unit_when_it_is_the_only_one(
    admin_client: httpx.AsyncClient,
    db_session,
    owner_conn: asyncpg.Connection,
    tmp_path: Path,
    recipes_repo: TempRepo,  # noqa: F811
):
    await _setup(admin_client, db_session, owner_conn, tmp_path)
    rye = await make_ingredient(admin_client, "Rye bread")
    toast = await make_ingredient(admin_client, "Toast")
    await _portion(owner_conn, 1006, "slice, thin", 25)
    await _portion(owner_conn, 1007, "slice, thin", 20)
    await _portion(owner_conn, 1007, "slice, thick", 40)
    await _ref(owner_conn, rye["id"], 1006)
    await _ref(owner_conn, toast["id"], 1007)
    recipe = await indexed(
        admin_client,
        recipes_repo,
        "harbor-toast.cook",
        "Lay out @rye bread{2%slices} and @toast{2%slices}.\n",
    )
    got = lines_by_text(await cost(admin_client, recipe["id"]))
    assert got[("rye bread", "slices")]["usda_offer"]["text"] == "1 slice, thin = 25 g"
    assert got[("toast", "slices")]["usda_offer"] is None, "two fit: neither would convert"

    offer = got[("rye bread", "slices")]["usda_offer"]
    r = await admin_client.post(
        f"/api/v1/usda/review/{rye['id']}",
        json={"measures": [offer["measure_label"]], "mark_reviewed": False},
    )
    assert r.status_code == 200, r.text
    after = lines_by_text(await cost(admin_client, recipe["id"]))[("rye bread", "slices")]
    assert after["quantity"]["canonical_qty"] == "50"
