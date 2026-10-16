"""A naming row's Brand from the wording's leading words (#390)."""

from __future__ import annotations

import uuid

from app.services import line_words, naming_brands, resolution
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase

_BRANDS = {"harbor light": "Harbor Light", "hrbr lt": "Harbor Light", "tern": "Tern"}


def test_the_longest_known_brand_at_the_front_is_taken_and_one_word_is_left():
    printed = ["hrbr", "lt", "grk", "ygrt"]
    assert naming_brands.leading(printed, printed, _BRANDS) == ("Harbor Light", 2)
    # Read words count too: an expansion can spell the brand out.
    shown = ["harbor", "light", "greek", "yogurt"]
    assert naming_brands.leading(["hbr", "lgt", "grk", "ygrt"], shown, _BRANDS) == (
        "Harbor Light",
        2,
    )
    assert naming_brands.leading(["tern"], ["tern"], _BRANDS) == (None, 0)  # nothing left to name
    assert naming_brands.leading(["oat", "tern"], ["oat", "tern"], _BRANDS) == (None, 0)


def test_brand_completions_start_with_what_was_typed():
    assert naming_brands.complete(_BRANDS, "ha") == ["Harbor Light"]
    assert naming_brands.complete(_BRANDS, "t") == []


async def test_a_row_takes_its_brand_off_the_name_and_create_saves_it(
    admin_client, admin, db_session
):
    await seed_units_via_service(db_session)
    yogurt = await make_ingredient(admin_client, "greek yogurt")
    await make_product(admin_client, yogurt["id"], "Greek yogurt plain", brand="Harbor Light")
    loc = await make_location(admin_client, "Quayside Grocer", "Quayside Grocer North")
    vendor = uuid.UUID(loc["vendor"]["id"])
    lines = [{"raw_text": "HARBOR LIGHT GRK QPRT 3.10", "line_total": "3.10"}]
    pid = await make_receipt_purchase(admin.id, loc["id"], lines)
    await resolution.resolve_purchase(db_session, uuid.UUID(pid))
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).status_code == 200
    await line_words.learn(db_session, vendor, "QPRT", "Quaport")
    await db_session.commit()

    r = await admin_client.get("/api/v1/to-identify/naming")
    row = r.json()["items"][0]
    assert (row["brand"], row["name"], row["name_words"]) == (
        "Harbor Light", "Greek quaport", ["grk", "qprt"]
    )  # fmt: skip
    # The changes are indexed within the name's own words.
    assert [(c["index"], c["printed"]) for c in row["name_changes"]] == [(0, "grk"), (1, "qprt")]

    body = {
        "rows": [
            {"vendor_id": str(vendor), "raw_text_norm": row["raw_text_norm"], "name": row["name"],
             "brand": " Harbor Light ", "ingredient_id": yogurt["id"]},
        ]
    }  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json=body)
    result = r.json()["results"][0]
    assert result["error"] is None, result
    product = (await admin_client.get(f"/api/v1/products/{result['product_id']}")).json()
    assert (product["name"], product["brand"]) == ("Greek quaport", "Harbor Light")
