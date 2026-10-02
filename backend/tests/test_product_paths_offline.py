"""Criterion 83: every product path makes no network request (04, 2L).

One flow through all of them under the network guard, which fails the test on
any connection beyond loopback and the database: a barcode lookup with the USDA
table loaded, a photographed product identified by barcode and by the text
model, accept with a main-photo choice, the photo pipeline and media serving.
The model server is replayed, so the only "model" traffic is to the recorded
transport, never a socket.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.usda_branded import import_branded
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.test_captures import UNKNOWN, photo, post_photos, work
from tests.test_usda_branded import write as write_branded

no_network = gh.no_network
recorded = ih.recorded


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


async def test_every_product_path_stays_on_the_machine(
    admin_client, db_session, tmp_path, recorded, monkeypatch, no_network
):
    from app.services import identify

    async def ocr(data: bytes) -> str:
        return "LARKFIELD\nRye flour\n1 kg"

    monkeypatch.setattr(identify, "_ocr_text", ocr)
    recorded({"ProductReading": {"name": "Rye flour", "brand": "Larkfield", "confidence": 0.4}})
    await import_branded(
        db_session,
        write_branded(
            tmp_path / "branded",
            branded=[{"fdc_id": "21", "brand_name": "Larkfield", "gtin_upc": UNKNOWN}],
            foods=[{"fdc_id": "21", "description": "STRONG WHITE FLOUR"}],
        ),
    )
    # A scanned barcode: catalog, then the local USDA table, then a proposal.
    found = (await admin_client.post("/api/v1/barcode-lookups", json={"code": UNKNOWN})).json()
    assert found["result"] == "proposal"

    # A photographed product with a barcode in it, and one read by the text model.
    with_code = (await post_photos(admin_client, photo(barcode=UNKNOWN))).json()
    plain = (await post_photos(admin_client, photo((60, 60, 60)))).json()
    await work()
    read = (await admin_client.get(f"/api/v1/product-proposals/{plain['id']}")).json()
    assert read["reading"]["path"] == "ocr_text" and read["fields"]["title"]["value"] == "Rye flour"

    # Review and accept, with a photo and a main-photo choice.
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Rye flour"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{plain['id']}/accept",
        json={
            "action": "new",
            "ingredient_id": ing["id"],
            "main_photo_id": read["photos"][0]["id"],
        },
    )
    assert r.status_code == 200, r.text
    product_id = r.json()["result"]["product_id"]
    photos = (await admin_client.get(f"/api/v1/products/{product_id}/photos")).json()["items"]
    assert (await admin_client.get(photos[0]["urls"]["medium"])).status_code == 200

    # The barcode proposals: the photo's scan superseded the lookup's (same GTIN, no page).
    assert (await admin_client.get(f"/api/v1/product-proposals/{found['proposal_id']}")).json()[
        "status"
    ] == "superseded"
    r = await admin_client.post(f"/api/v1/product-proposals/{with_code['id']}/reject")
    assert r.status_code == 200
