"""Barcode lookups and "Photograph a product" (04, 2L): criteria 80 and 81 (model paths in S6).

Codes are invented with computed check digits; barcode photos are drawn at test time.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import asyncpg
import pytest
from PIL import Image

from app.catalog.identifiers import check_digit
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.services import product_jobs
from app.services.barcode_lookup import pack_from_text
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.pricebook_helpers import make_location, make_product
from tests.test_usda_branded import write as write_branded

no_network = gh.no_network


def with_check(body: str) -> str:
    digits = body.replace(" ", "")
    return digits + str(check_digit(digits))


KNOWN = with_check("0 4812 3000 03")  # UPC-A of a product we have
UNKNOWN = with_check("5 0123 4500 004")  # EAN-13 nobody has
LABEL = with_check("2 04123 00349")  # weighed item 04123 at 3.49


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


async def lookup(client, **body):
    r = await client.post("/api/v1/barcode-lookups", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_known_code_returns_its_product(admin_client, no_network):
    known = await make_product(admin_client, "Beans", "Green bean tin", barcode=KNOWN)
    found = await lookup(admin_client, code=KNOWN)
    assert found["result"] == "product" and found["product"]["id"] == known["id"]


async def test_a_weighed_label_at_a_store_with_that_item_returns_the_product_and_price(
    admin_client, owner_conn: asyncpg.Connection, no_network
):
    store = await make_location(admin_client, "Corner Deli", "Corner Deli")
    ham = await make_product(admin_client, "Ham", "Sliced ham")
    await owner_conn.execute(
        "INSERT INTO product_identifier (id, product_id, scheme, value, vendor_id, source) "
        "VALUES (gen_random_uuid(), $1, 'rw_item', '04123', $2, 'manual')",
        uuid.UUID(ham["id"]),
        uuid.UUID(store["vendor"]["id"]),
    )
    found = await lookup(admin_client, code=LABEL, vendor_location_id=store["id"])
    assert found["result"] == "product" and found["product"]["id"] == ham["id"]
    assert found["label"] == {"item": "04123", "price": "3.49", "weight": None}
    # Standing within 150 m of the store finds it too.
    near = await lookup(admin_client, code=LABEL, lat="33.500500", lon="-120.500000")
    assert near["product"]["id"] == ham["id"] and near["vendor_location_id"] == store["id"]


async def test_a_weighed_label_with_no_store_returns_only_what_it_says(admin_client, no_network):
    await make_location(admin_client, "Corner Deli", "Corner Deli")
    for body in ({}, {"lat": "33.700000", "lon": "-120.300000"}):  # nowhere near a store
        found = await lookup(admin_client, code=LABEL, **body)
        assert found["result"] == "label" and found["product"] is None
        assert found["needs_store"] is True
        assert found["label"] == {"item": "04123", "price": "3.49", "weight": None}


async def test_an_unknown_gtin_becomes_a_proposal(admin_client, no_network):
    found = await lookup(admin_client, code=UNKNOWN)
    assert found["result"] == "proposal"
    proposal = (await admin_client.get(f"/api/v1/product-proposals/{found['proposal_id']}")).json()
    assert proposal["fields"]["gtin"] == {
        "value": "0" + UNKNOWN,
        "source": "scan",
        "confidence": None,
        "alternatives": [],
        "conflict": False,
    }
    assert proposal["capture"]["channel"] == "barcode"
    # Scanning it again while that proposal is pending returns the same one.
    again = await lookup(admin_client, code=UNKNOWN)
    assert again["proposal_id"] == found["proposal_id"]


async def test_an_unknown_gtin_takes_usda_branded_fields_when_loaded(
    admin_client, db_session, tmp_path, no_network
):
    from app.services.usda_branded import import_branded

    await import_branded(
        db_session,
        write_branded(
            tmp_path / "branded",
            branded=[
                {
                    "fdc_id": "11",
                    "brand_name": "Larkfield",
                    "gtin_upc": UNKNOWN,
                    "branded_food_category": "Flours & Corn Meal",
                    "package_weight": "2.2 lb/1 kg",
                }
            ],
            foods=[{"fdc_id": "11", "description": "STRONG WHITE FLOUR"}],
        ),
    )
    found = await lookup(admin_client, code=UNKNOWN)
    fields = (await admin_client.get(f"/api/v1/product-proposals/{found['proposal_id']}")).json()[
        "fields"
    ]
    assert fields["title"]["value"] == "Strong White Flour"
    assert fields["title"]["source"] == "usda_branded"
    assert fields["brand"]["value"] == "Larkfield"
    assert fields["pack"]["value"] == {"qty": "1", "unit": "kg"}


def test_package_text_prefers_the_metric_size():
    assert pack_from_text("15.5 oz/439 g") == {"qty": "439", "unit": "g"}
    assert pack_from_text("12 fl oz") == {"qty": "12", "unit": "fl_oz"}
    assert pack_from_text("1 bar") is None
    assert pack_from_text(None) is None


async def test_an_invalid_check_digit_is_refused(admin_client):
    bad = UNKNOWN[:-1] + str((int(UNKNOWN[-1]) + 1) % 10)
    r = await admin_client.post("/api/v1/barcode-lookups", json={"code": bad})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_gtin"


# --- photographing a product -------------------------------------------------------------


def photo(colour=(200, 190, 170), barcode: str | None = None) -> bytes:
    canvas = Image.new("RGB", (900, 700), colour)
    if barcode:
        import zxingcpp

        drawn = zxingcpp.create_barcode(barcode, zxingcpp.BarcodeFormat.EAN13).to_image(scale=4)
        view = memoryview(drawn)
        bars = Image.frombytes("L", (view.shape[1], view.shape[0]), bytes(view))
        canvas.paste(bars.convert("RGB"), (200, 250))
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=92)
    return out.getvalue()


async def post_photos(client, *photos: bytes, roles=None):
    files = [("photos", (f"p{i}.jpg", data, "image/jpeg")) for i, data in enumerate(photos)]
    data = {"roles": roles} if roles else {}
    return await client.post("/api/v1/product-captures/photos", data=data, files=files)


async def work() -> None:
    async with get_sessionmaker()() as db:
        while await product_jobs.run_once(db, locked_by="test"):
            pass


async def test_four_photos_make_one_proposal(admin_client, no_network):
    """Criterion 81, first part."""
    r = await post_photos(
        admin_client,
        *(photo((40 * i, 120, 90)) for i in range(4)),
        roles=["product", "label_front", "label_nutrition", "label_ingredients"],
    )
    assert r.status_code == 201, r.text
    proposal = r.json()
    assert proposal["status"] == "pending" and proposal["capture"]["channel"] == "photo"
    assert [p["role"] for p in proposal["photos"]] == [
        "product",
        "label_front",
        "label_nutrition",
        "label_ingredients",
    ]
    assert {j["kind"] for j in proposal["jobs"]} == {"image_process", "identify"}
    again = await post_photos(
        admin_client,
        *(photo((40 * i, 120, 90)) for i in range(4)),
        roles=["product", "label_front", "label_nutrition", "label_ingredients"],
    )
    assert again.status_code == 200 and again.json()["id"] == proposal["id"]


async def test_a_photo_with_nothing_readable_still_makes_a_proposal(admin_client, no_network):
    """Criterion 81, second part."""
    r = await post_photos(admin_client, photo())
    assert r.status_code == 201
    await work()
    proposal = (await admin_client.get(f"/api/v1/product-proposals/{r.json()['id']}")).json()
    assert proposal["status"] == "pending" and proposal["fields"] == {}
    assert [p["status"] for p in proposal["photos"]] == ["candidate"]
    assert {j["status"] for j in proposal["jobs"]} == {"done"}


async def test_a_barcode_in_a_photo_is_treated_as_a_scan(admin_client, no_network):
    known = await make_product(admin_client, "Flour", "Bread flour", barcode=UNKNOWN)
    r = await post_photos(admin_client, photo(barcode=UNKNOWN))
    await work()
    proposal = (await admin_client.get(f"/api/v1/product-proposals/{r.json()['id']}")).json()
    assert proposal["fields"]["gtin"]["value"] == "0" + UNKNOWN
    assert proposal["fields"]["gtin"]["source"] == "scan"
    assert proposal["match"]["preselect"] == f"update:{known['id']}"


async def test_a_bad_photo_in_the_batch_stores_nothing(admin_client, owner_conn, media_root):
    r = await post_photos(admin_client, photo(), b"GIF89a not a photo")
    assert r.status_code == 415
    assert await owner_conn.fetchval("SELECT count(*) FROM product_capture") == 0
    assert not media_root.exists() or not any(media_root.rglob("*.*"))


# --- reading a photo with a model (S6; criterion 81, model paths) -------------------------

recorded = ih.recorded


@pytest.fixture
def ocr_text(monkeypatch):
    """Tesseract's reading of each photo, by its position; the binary is not needed."""
    from app.services import identify

    texts: list[str] = []

    async def fake(data: bytes) -> str:
        return texts.pop(0) if texts else ""

    monkeypatch.setattr(identify, "_ocr_text", fake)
    return texts


async def proposal_of(client, proposal_id: str) -> dict:
    return (await client.get(f"/api/v1/product-proposals/{proposal_id}")).json()


async def test_without_a_vision_model_tesseract_then_the_text_model_reads_it(
    admin_client, recorded, ocr_text, no_network
):
    ocr_text.extend(
        ["HOLLOW CREEK\nCut green beans\nNET WT 14.5 OZ", "Ingredients: green beans, water, salt"]
    )
    transport = recorded(
        {
            "ProductReading": {
                "name": "Cut green beans",
                "brand": "Hollow Creek",
                "pack_qty": 14.5,
                "pack_unit": "oz",
                "category": "canned beans",
                "ingredients_text": "green beans, water, salt",
                "confidence": 0.9,
            }
        }
    )
    r = await post_photos(
        admin_client, photo(), photo((90, 90, 90)), roles=["product", "label_ingredients"]
    )
    await work()
    proposal = await proposal_of(admin_client, r.json()["id"])
    assert proposal["reading"] == {"path": "ocr_text", "error": None}
    fields = proposal["fields"]
    assert fields["title"]["value"] == "Cut green beans" and fields["title"]["source"] == "model"
    assert fields["title"]["confidence"] == "0.6"  # capped for text
    assert fields["pack"]["value"] == {"qty": "14.5", "unit": "oz"}
    label = [p for p in proposal["photos"] if p["role"] == "label_ingredients"][0]
    assert label["ocr_text"] == "Ingredients: green beans, water, salt"
    [request] = transport.requests
    assert "images" not in request["body"]["messages"][1]
    assert "BEGIN RECEIPT TEXT" in request["body"]["messages"][1]["content"]
    assert "grocery products" in request["body"]["messages"][0]["content"]


async def test_with_a_vision_model_it_reads_the_photos(
    admin_client, recorded, monkeypatch, no_network
):
    monkeypatch.setattr(get_settings(), "vision_model", "test-vl:8b")
    transport = recorded(
        {"ProductReading": {"name": "Oat drink", "brand": "Brightfield", "confidence": 0.95}}
    )
    r = await post_photos(admin_client, photo())
    await work()
    proposal = await proposal_of(admin_client, r.json()["id"])
    assert proposal["reading"] == {"path": "vision", "error": None}
    assert proposal["fields"]["title"]["value"] == "Oat drink"
    assert proposal["fields"]["title"]["confidence"] == "0.5"  # capped for a photo
    [request] = transport.requests
    assert request["model"] == "test-vl:8b"
    assert len(request["body"]["messages"][1]["images"]) == 1
    assert request["body"]["think"] is False


async def test_an_answer_that_does_not_validate_fills_nothing(
    admin_client, recorded, ocr_text, no_network
):
    ocr_text.append("SOME LABEL TEXT")
    recorded({"ProductReading": {"name": "x", "colour": "red"}})
    r = await post_photos(admin_client, photo())
    await work()
    proposal = await proposal_of(admin_client, r.json()["id"])
    assert proposal["status"] == "pending" and proposal["fields"] == {}
    assert proposal["reading"] == {"path": "ocr_text", "error": "invalid_model_output"}


async def test_with_no_model_server_the_proposal_still_waits_for_a_person(
    admin_client, ocr_text, no_network
):
    ocr_text.extend(["LABEL", "LABEL", "LABEL"])
    r = await post_photos(admin_client, photo())
    await work()
    proposal = await proposal_of(admin_client, r.json()["id"])
    assert proposal["status"] == "pending" and proposal["fields"] == {}
    assert proposal["reading"]["error"] == "model_unavailable"
    assert {j["status"] for j in proposal["jobs"]} == {"done"}
