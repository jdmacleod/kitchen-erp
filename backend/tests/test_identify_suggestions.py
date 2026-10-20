"""The to-identify queue suggests what bulk naming would, card by card (04, 2D, 2I)."""

from __future__ import annotations

import uuid

from app.services import identify_queue, resolution
from tests.catalog_helpers import make_ingredient, seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase


async def _committed(client, admin, db_session, loc, lines) -> None:
    pid = await make_receipt_purchase(admin.id, loc["id"], lines)
    await resolution.resolve_purchase(db_session, uuid.UUID(pid))
    r = await client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200, r.text


async def test_each_group_carries_a_name_its_candidates_and_where_else_it_waits(
    admin_client, admin, db_session
):
    await seed_units_via_service(db_session)
    quay = await make_location(admin_client, "Quayside Grocer", "Quayside Grocer North")
    mill = await make_location(admin_client, "Millpond Market", "Millpond Market East")
    broccoli = await make_ingredient(admin_client, "broccoli")
    made = await admin_client.post(
        "/api/v1/products", json={"ingredient_id": broccoli["id"], "name": "Broccoli crowns"}
    )
    assert made.status_code == 201, made.text
    await _committed(admin_client, admin, db_session, quay, [
        {"raw_text": "WT BROCCOLI CROWNS 4.12 F", "line_total": "4.12"},
        {"raw_text": "PURPLE YAMS 2.10", "line_total": "2.10"},
    ])  # fmt: skip
    await _committed(admin_client, admin, db_session, mill, [
        {"raw_text": "WT BROCCOLI CROWNS 3.90", "line_total": "3.90"},
        {"raw_text": "WT BROCCOLI CROWNS 3.70", "line_total": "3.70"},
    ])  # fmt: skip

    r = await admin_client.get("/api/v1/to-identify")
    assert r.status_code == 200, r.text
    groups = {(g["vendor"]["name"], g["raw_text_norm"]): g for g in r.json()["items"]}

    at_quay = groups[("Quayside Grocer", "WT BROCCOLI CROWNS")]
    assert at_quay["suggested_name"] == "Wt broccoli crowns"
    assert [c["id"] for c in at_quay["candidates"]] == [made.json()["id"]]
    assert at_quay["elsewhere"] == [
        {"vendor_id": mill["vendor"]["id"], "vendor_name": "Millpond Market", "line_count": 2}
    ]
    at_mill = groups[("Millpond Market", "WT BROCCOLI CROWNS")]
    assert [e["vendor_name"] for e in at_mill["elsewhere"]] == ["Quayside Grocer"]

    yams = groups[("Quayside Grocer", "PURPLE YAMS")]
    assert (yams["candidates"], yams["elsewhere"]) == ([], [])


def test_elsewhere_lists_other_vendors_most_lines_first_and_skips_empty_wording():
    def g(vendor: str, text: str | None, n: int) -> dict:
        return {"vendor_id": vendor, "vendor_name": vendor.title(), "raw_text_norm": text,
                "line_count": n}  # fmt: skip

    groups = [g("a", "OAT BREAD", 1), g("b", "OAT BREAD", 2), g("c", "OAT BREAD", 5),
              g("a", None, 3), g("b", None, 1), g("c", "RYE", 1)]  # fmt: skip
    found = identify_queue.elsewhere(groups)
    assert [o["vendor_id"] for o in found[("a", "OAT BREAD")]] == ["c", "b"]
    assert [o["vendor_id"] for o in found[("c", "OAT BREAD")]] == ["b", "a"]
    assert ("a", None) not in found and ("c", "RYE") not in found
