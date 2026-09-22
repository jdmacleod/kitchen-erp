from decimal import Decimal
from pathlib import Path

import pytest

from app.core.db import get_sessionmaker
from app.services.importer import import_export, load_export
from tests.pricebook_helpers import make_location, make_product

FIXTURE = Path(__file__).parent / "fixtures" / "exports" / "synthetic_export.json"
REALDATA = Path(__file__).resolve().parents[2] / "data" / "imports"
UPC = "036000291452"


async def _mart(admin_client):
    loc = await make_location(
        admin_client, "Invented Mart", "Invented Mart #0417", kind="chain", price_scope="chain"
    )
    r = await admin_client.patch(
        f"/api/v1/vendor-locations/{loc['id']}", json={"receipt_identifiers": ["0417"]}
    )
    assert r.status_code == 200, r.text
    return loc


async def test_import_creates_committed_purchases_and_resolves_barcodes(admin_client, admin):
    await _mart(admin_client)
    rig = await make_product(
        admin_client, "Rigatoni", "Rigatoni 16 oz", barcode=UPC, pack_qty="16", pack_unit="oz"
    )
    export = load_export(FIXTURE)
    async with get_sessionmaker()() as db:
        result = await import_export(db, admin, export)
    assert result == {"created": 2, "skipped": 0, "unlocated": 0}
    listed = (await admin_client.get("/api/v1/purchases", params={"source": "import"})).json()
    purchases = listed["items"]
    assert len(purchases) == 2 and all(p["status"] == "committed" for p in purchases)
    first = next(p for p in purchases if p["total"] == "8.2100")
    # 18:22 Pacific in March (PST) is 02:22 UTC the next day.
    assert first["purchased_at"].startswith("2026-03-05T02:22")
    by_text = {ln["raw_text"]: ln for ln in first["lines"]}
    rig_line = by_text[f"{UPC} RIGATONI 16OZ"]
    assert rig_line["resolution"] == "barcode" and rig_line["product"]["id"] == rig["id"]
    assert rig_line["observation_id"]
    bananas = by_text["ORG BANANAS"]
    assert bananas["resolution"] == "unmatched" and bananas["observation_id"] is None
    loyalty = by_text["LOYALTY"]
    assert loyalty["line_kind"] == "discount" and loyalty["parent_line_id"] == rig_line["id"]
    obs = (
        await admin_client.get(f"/api/v1/price-observations/{rig_line['observation_id']}")
    ).json()
    assert obs["price"] == "1.9900" and obs["is_promo"] is True and obs["source"] == "import"
    grams = Decimal("16") * Decimal("28.349523125")
    assert Decimal(obs["norm"]["norm_unit_price"]) == (Decimal("1.99") / grams).quantize(
        Decimal("0.000001")
    )
    queue = (await admin_client.get("/api/v1/to-identify")).json()["items"]
    assert {g["raw_text_norm"] for g in queue} == {"ORG BANANAS", "SPARKLING WTR 1L"}


async def test_import_is_idempotent(admin_client, admin):
    await _mart(admin_client)
    export = load_export(FIXTURE)
    async with get_sessionmaker()() as db:
        assert (await import_export(db, admin, export))["created"] == 2
    async with get_sessionmaker()() as db:
        assert await import_export(db, admin, export) == {
            "created": 0,
            "skipped": 2,
            "unlocated": 0,
        }
    assert len((await admin_client.get("/api/v1/purchases")).json()["items"]) == 2


async def test_amounts_come_from_strings_exactly(tmp_path: Path):
    good = load_export(FIXTURE)
    assert good.transactions[0].lines[0].amount == Decimal("1.83")
    bare = tmp_path / "bare.json"
    bare.write_text(FIXTURE.read_text().replace('"amount": "1.83"', '"amount": 1.83'))
    # A bare JSON number is parsed as its digits, never through a float.
    assert load_export(bare).transactions[0].lines[0].amount == Decimal("1.83")
    wrong = tmp_path / "wrong.json"
    wrong.write_text(FIXTURE.read_text().replace("kitchen-erp-purchase-export/1", "other/9"))
    with pytest.raises(Exception, match="unsupported format"):
        load_export(wrong)


async def test_unlocated_transactions_are_reported_not_guessed(admin_client, admin):
    export = load_export(FIXTURE)
    async with get_sessionmaker()() as db:
        result = await import_export(db, admin, export)
    assert result == {"created": 0, "skipped": 0, "unlocated": 2}


@pytest.mark.realdata
async def test_realdata_exports_resolve(admin_client, admin):
    files = sorted(REALDATA.glob("*.json")) if REALDATA.is_dir() else []
    if not files:
        pytest.skip("no exports under data/imports/")
    async with get_sessionmaker()() as db:
        for f in files:
            result = await import_export(db, admin, load_export(f))
            assert result["created"] + result["skipped"] > 0
