"""A naming row offers the products its ingredient already has, its own brand first (#386)."""

from __future__ import annotations

import uuid

from app.models import Product
from app.models.brands import Brand
from app.services import resolution
from app.services.naming_candidates import rank
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase


async def test_a_row_lists_its_ingredients_products_closest_first(admin_client, admin, db_session):
    await seed_units_via_service(db_session)
    broccoli = await make_ingredient(admin_client, "broccoli")
    oats = await make_ingredient(admin_client, "oats")
    for name in ["Broccoli florets", "Broccoli crowns", "Frozen broccoli", "Broccolini bunch"]:
        await make_product(admin_client, broccoli["id"], name)
    await make_product(admin_client, oats["id"], "Rolled oats")
    loc = await make_location(admin_client, "Quayside Grocer", "Quayside Grocer North")
    lines = [
        {"raw_text": "WT BROCCOLI CROWNS 4.12 F", "line_total": "4.12"},
        {"raw_text": "QPRT BAR 2.10", "line_total": "2.10"},
    ]
    pid = await make_receipt_purchase(admin.id, loc["id"], lines)
    await resolution.resolve_purchase(db_session, uuid.UUID(pid))
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).status_code == 200

    r = await admin_client.get("/api/v1/to-identify/naming")
    assert r.status_code == 200, r.text
    rows = {row["raw_text_norm"]: row for row in r.json()["items"]}
    found = rows["WT BROCCOLI CROWNS"]["candidates"]
    assert [c["name"] for c in found][:1] == ["Broccoli crowns"]
    assert len(found) == 3 and all(
        set(c) == {"id", "name", "brand", "pack_qty", "pack_unit"} for c in found
    )
    # No ingredient in the wording: nothing to offer.
    assert rows["QPRT BAR"]["candidates"] == []


def _product(name: str, brand: str | None = None, family: uuid.UUID | None = None) -> Product:
    p = Product(id=uuid.uuid4(), name=name, brand=brand)
    if family is not None:
        p.house_brand = Brand(id=uuid.uuid4(), family_id=family, key="k", name=brand or "")
    return p


def test_the_lines_own_brand_or_brand_family_comes_first():
    family = uuid.uuid4()
    hilltop = _product("Whole milk", "Hilltop")
    quayside = _product("Vitamin D whole milk", "Quayside Farms")
    house = _product("Whole milk half gallon", "Quayside Select", family)
    # By spelling alone, another store's whole milk leads.
    assert rank("Whole milk", [quayside, house, hilltop])[0] is hilltop
    # The line's brand, as printed, puts its own first.
    assert rank("Whole milk", [hilltop, quayside], brand="Quayside Farms")[0] is quayside
    # A store brand of the same family as the printed one counts as its own.
    assert rank("Milk", [hilltop, house], brand="Quayside", family_id=family)[0] is house
