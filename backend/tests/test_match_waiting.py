"""Queued lines resolve when a product gains their code (2G with 2M).

Every store, product and figure here is invented.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.core.db import get_sessionmaker
from app.services.importer import import_export, load_export
from tests.pricebook_helpers import make_location, make_product

UPC = "036000291452"
GTIN = UPC.zfill(14)


async def _imported(admin_client, admin, tmp_path: Path) -> dict:
    """One committed trip whose barcode lines wait in the queue: no product has the code yet."""
    loc = await make_location(
        admin_client, "Invented Mart", "Invented Mart #0417", kind="chain", price_scope="chain"
    )
    r = await admin_client.patch(
        f"/api/v1/vendor-locations/{loc['id']}", json={"receipt_identifiers": ["0417"]}
    )
    assert r.status_code == 200, r.text
    doc = {
        "format": "kitchen-erp-purchase-export/1",
        "retailer": "Invented Mart",
        "transactions": [
            {
                "ref": "T-0200",
                "store_code": "0417",
                "occurred_at": "2026-03-20T10:00:00",
                "lines": [
                    {"description": "RIGATONI 16OZ", "upc": GTIN, "amount": "2.49"},
                    {
                        "description": "RIGATONI 16OZ",
                        "upc": GTIN,
                        "amount": "4.98",
                        "observe": False,
                    },
                    {"description": "SPARKLING WTR 1L", "upc": None, "amount": "1.19"},
                ],
            }
        ],
    }
    path = tmp_path / "export.json"
    path.write_text(json.dumps(doc))
    async with get_sessionmaker()() as db:
        await import_export(db, admin, load_export(path))
    [purchase] = (await admin_client.get("/api/v1/purchases")).json()["items"]
    assert all(ln["resolution"] == "unmatched" for ln in purchase["lines"])
    return purchase


async def test_a_new_product_with_the_barcode_resolves_and_prices_waiting_lines(
    admin_client, admin, tmp_path
):
    purchase = await _imported(admin_client, admin, tmp_path)
    rig = await make_product(
        admin_client, "Rigatoni", "Rigatoni 16 oz", barcode=UPC, pack_qty="16", pack_unit="oz"
    )
    after = (await admin_client.get(f"/api/v1/purchases/{purchase['id']}")).json()
    priced, unpriced, water = sorted(after["lines"], key=lambda ln: ln["seq"])
    assert priced["resolution"] == "identifier" and priced["product"]["id"] == rig["id"]
    obs = (await admin_client.get(f"/api/v1/price-observations/{priced['observation_id']}")).json()
    assert Decimal(obs["price"]) == Decimal("2.49") and obs["source"] == "import"
    # The line whose quantity the export never gave resolves too, but records no price.
    assert unpriced["product"]["id"] == rig["id"] and unpriced["observation_id"] is None
    # A line with no code waits, as before.
    assert water["resolution"] == "unmatched"
    queue = (await admin_client.get("/api/v1/to-identify")).json()["items"]
    assert {g["raw_text_norm"] for g in queue} == {"SPARKLING WTR 1L"}


async def test_giving_a_product_its_barcode_later_resolves_waiting_lines(
    admin_client, admin, tmp_path
):
    purchase = await _imported(admin_client, admin, tmp_path)
    rig = await make_product(admin_client, "Rigatoni", "Rigatoni 16 oz")
    still = (await admin_client.get(f"/api/v1/purchases/{purchase['id']}")).json()
    assert still["lines"][0]["resolution"] == "unmatched"
    r = await admin_client.patch(f"/api/v1/products/{rig['id']}", json={"barcode": UPC})
    assert r.status_code == 200, r.text
    after = (await admin_client.get(f"/api/v1/purchases/{purchase['id']}")).json()
    first = min(after["lines"], key=lambda ln: ln["seq"])
    assert first["product"]["id"] == rig["id"] and first["observation_id"]
