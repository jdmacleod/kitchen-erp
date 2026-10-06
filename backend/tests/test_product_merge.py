"""Merging duplicate products (#179). Invented vendors, products and prices only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.models import PriceObservation, ProductImage, ReceiptAlias, VendorListing
from app.services import product_merge, product_photos, resolution
from app.services.resolution import set_ranker
from tests.pricebook_helpers import make_location, make_product, shelf
from tests.resolution_helpers import make_receipt_purchase


@pytest.fixture(autouse=True)
def _no_ranker():
    set_ranker(None)
    yield
    set_ranker(None)


async def _add(*rows) -> None:
    async with get_sessionmaker()() as db:
        db.add_all(rows)
        await db.commit()


async def _get(client: httpx.AsyncClient, product_id: str) -> dict:
    r = await client.get(f"/api/v1/products/{product_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def _merge(client: httpx.AsyncClient, loser: str, survivor: str, *, preview=False):
    path = f"/api/v1/products/{loser}/merge" + ("/preview" if preview else "")
    return await client.post(path, json={"survivor_id": survivor})


async def _also(client: httpx.AsyncClient, first: dict, name: str, **extra) -> dict:
    """Another product of ``first``'s ingredient."""
    r = await client.post(
        "/api/v1/products",
        json={"ingredient_id": first["ingredient"]["id"], "name": name, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _image(product_id: str, sha: str, **extra) -> ProductImage:
    return ProductImage(
        product_id=uuid.UUID(product_id),
        upload_sha256=sha * 64,
        sha256=sha * 64,
        source_kind="user_photo",
        status="active",
        width=800,
        height=800,
        **extra,
    )


async def _two_relishes(client: httpx.AsyncClient) -> tuple[dict, dict, dict]:
    loc = await make_location(client, "Quillmoor Grocer", "Quillmoor Grocer")
    keep = await make_product(
        client, "Relish", "Bramblecot sweet relish", pack_qty="30", pack_unit="oz"
    )
    dupe = await _also(
        client, keep, "Bramblecot sweet relish jar", pack_qty="30", pack_unit="fl_oz"
    )
    return loc, keep, dupe


async def test_merge_moves_prices_codes_listings_photos_and_aliases(
    admin_client, admin, db_session
):
    loc, keep, dupe = await _two_relishes(admin_client)
    await shelf(admin_client, dupe["id"], loc["id"], "4.10")
    await shelf(admin_client, dupe["id"], loc["id"], "3.95", qty="30", unit="fl_oz")
    await _add(
        VendorListing(
            vendor_id=uuid.UUID(loc["vendor"]["id"]),
            product_id=uuid.UUID(dupe["id"]),
            canonical_url="https://quillmoor.example/p/relish",
            title="Sweet relish",
            last_captured_at=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        _image(dupe["id"], "a"),
    )
    # A receipt line chosen as the duplicate teaches an alias.
    p1 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "BRMBL SWT RLSH", "line_total": "4.10"}]
    )
    await resolution.resolve_purchase(db_session, uuid.UUID(p1))
    line = (await admin_client.get(f"/api/v1/purchases/{p1}")).json()["lines"][0]
    r = await admin_client.post(
        f"/api/v1/purchases/{p1}/lines/{line['id']}/resolve", json={"product_id": dupe["id"]}
    )
    assert r.status_code == 200, r.text

    r = await _merge(admin_client, dupe["id"], keep["id"])
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["prices"], done["listings"], done["photos"], done["aliases"]) == (2, 1, 1, 1)
    assert done["lines"] == 1

    merged = await _get(admin_client, dupe["id"])
    assert merged["active"] is False and merged["merged_into"] == keep["id"]
    survivor = await _get(admin_client, keep["id"])
    assert survivor["active"] is True and survivor["merged_into"] is None
    # The survivor's own fields are unchanged; the photo now leads its page.
    assert (survivor["name"], survivor["pack_unit"]) == ("Bramblecot sweet relish", "oz")
    assert survivor["photo"] is not None

    history = (await admin_client.get(f"/api/v1/products/{keep['id']}/prices")).json()
    assert len(history["points"]) == 2
    records = (
        await admin_client.get("/api/v1/price-observations", params={"product_id": keep["id"]})
    ).json()["items"]
    assert len(records) == 2

    async with get_sessionmaker()() as db:
        # Facts are untouched: the observations still name the product they were taken for.
        observed = set((await db.execute(select(PriceObservation.product_id))).scalars().all())
        assert observed == {uuid.UUID(dupe["id"])}
        listing = (await db.execute(select(VendorListing))).scalar_one()
        assert str(listing.product_id) == keep["id"]
        alias = (await db.execute(select(ReceiptAlias))).scalar_one()
        assert str(alias.product_id) == keep["id"]

    # A later receipt line in the merged product's wording resolves to the survivor.
    p2 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "BRMBL SWT RLSH", "line_total": "4.25"}]
    )
    await resolution.resolve_purchase(db_session, uuid.UUID(p2))
    line = (await admin_client.get(f"/api/v1/purchases/{p2}")).json()["lines"][0]
    assert line["resolution"] == "alias" and line["product"]["id"] == keep["id"]


async def test_a_barcode_moves_with_the_merge(admin_client):
    keep = await make_product(admin_client, "Relish", "Bramblecot relish")
    dupe = await _also(admin_client, keep, "Bramblecot relish 2", barcode="96385074")
    r = await _merge(admin_client, dupe["id"], keep["id"])
    assert r.status_code == 200, r.text
    assert r.json()["codes"] == 1
    assert (await _get(admin_client, keep["id"]))["barcode"] == "96385074"
    assert (await _get(admin_client, dupe["id"]))["barcode"] is None


async def test_the_preview_counts_prices_in_another_dimension_and_writes_nothing(admin_client):
    loc, keep, dupe = await _two_relishes(admin_client)
    for price in ("3.95", "4.05"):
        await shelf(admin_client, dupe["id"], loc["id"], price, qty="30", unit="fl_oz")
    await shelf(admin_client, dupe["id"], loc["id"], "4.10")  # "each": priced through the pack

    r = await _merge(admin_client, dupe["id"], keep["id"], preview=True)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert (preview["survivor_pack_unit"], preview["loser_pack_unit"]) == ("oz", "fl_oz")
    # The fl oz prices wait on a density to compare as grams, before and after;
    # the "each" price compared through the fl oz pack only with a density, and
    # compares through the survivor's oz pack without one.
    assert preview["compare_unit"] == "g"
    assert preview["other_dimension_prices"] == 2
    assert preview["other_dimension_units"] == ["fl_oz"]
    assert preview["prices_needing_bridge"] == 0

    still = await _get(admin_client, dupe["id"])
    assert still["active"] is True and still["merged_into"] is None


async def test_merged_prices_are_compared_as_the_survivors(admin_client):
    loc = await make_location(admin_client, "Quillmoor Grocer", "Quillmoor Grocer")
    keep = await make_product(
        admin_client, "Rolled oats", "Larkfield oats", pack_qty="1", pack_unit="kg"
    )
    dupe = await _also(admin_client, keep, "Larkfield oats 1kg")
    obs = await shelf(admin_client, dupe["id"], loc["id"], "5.00")  # "each", no pack yet
    bridges = (await admin_client.get("/api/v1/price-book/needs-bridge")).json()
    assert any(b["product"]["id"] == dupe["id"] for b in bridges["items"])

    assert (await _merge(admin_client, dupe["id"], keep["id"])).status_code == 200
    # Renormalized through the survivor's 1 kg pack in the same transaction.
    record = (await admin_client.get(f"/api/v1/price-observations/{obs['id']}")).json()
    assert record["norm"]["status"] == "ok"
    bridges = (await admin_client.get("/api/v1/price-book/needs-bridge")).json()
    assert bridges["items"] == []


async def test_merges_stay_one_level_deep(admin_client):
    a = await make_product(admin_client, "Relish", "Relish A")
    b = await _also(admin_client, a, "Relish B")
    c = await _also(admin_client, a, "Relish C")
    assert (await _merge(admin_client, c["id"], a["id"])).status_code == 200
    assert (await _merge(admin_client, a["id"], b["id"])).status_code == 200
    assert (await _get(admin_client, c["id"]))["merged_into"] == b["id"]


async def test_refusals(admin_client):
    a = await make_product(admin_client, "Relish", "Relish A")
    b = await _also(admin_client, a, "Relish B")
    c = await _also(admin_client, a, "Relish C")
    r = await _merge(admin_client, a["id"], a["id"])
    assert (r.status_code, r.json()["error"]["code"]) == (422, "merge_self")
    assert (await _merge(admin_client, a["id"], b["id"])).status_code == 200
    r = await _merge(admin_client, c["id"], a["id"])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "merge_target_merged")
    r = await _merge(admin_client, a["id"], c["id"])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "already_merged")
    await admin_client.post(f"/api/v1/products/{c['id']}/deactivate")
    r = await _merge(admin_client, b["id"], c["id"])
    assert (r.status_code, r.json()["error"]["code"]) == (409, "merge_target_inactive")
    r = await admin_client.post(f"/api/v1/products/{a['id']}/activate")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "product_merged")
    r = await _merge(admin_client, a["id"], str(uuid.uuid4()))
    assert r.status_code == 404


async def test_a_failure_partway_changes_nothing(admin_client, monkeypatch):
    loc, keep, dupe = await _two_relishes(admin_client)
    await shelf(admin_client, dupe["id"], loc["id"], "4.10")
    await _add(
        VendorListing(
            vendor_id=uuid.UUID(loc["vendor"]["id"]),
            product_id=uuid.UUID(dupe["id"]),
            canonical_url="https://quillmoor.example/p/relish-jar",
            title="Sweet relish jar",
            last_captured_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )

    async def boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(product_merge, "_move_photos", boom)
    async with get_sessionmaker()() as db:
        with pytest.raises(RuntimeError):
            await product_merge.merge(db, uuid.UUID(keep["id"]), uuid.UUID(dupe["id"]))

    still = await _get(admin_client, dupe["id"])
    assert still["active"] is True and still["merged_into"] is None
    async with get_sessionmaker()() as db:
        listing = (await db.execute(select(VendorListing))).scalar_one()
        assert str(listing.product_id) == dupe["id"]


async def test_the_survivor_keeps_its_main_photo(admin_client):
    keep = await make_product(admin_client, "Relish", "Relish keep")
    dupe = await _also(admin_client, keep, "Relish dupe")
    await _add(_image(keep["id"], "b"), _image(dupe["id"], "c", pinned=True))
    async with get_sessionmaker()() as db:
        await product_photos.reselect_all(db)
        await db.commit()
    before = (await _get(admin_client, keep["id"]))["photo"]
    # A photo both already hold stays with the duplicate.
    await _add(_image(dupe["id"], "b"))
    r = await _merge(admin_client, dupe["id"], keep["id"])
    assert r.status_code == 200, r.text
    assert r.json()["photos"] == 1
    after = (await _get(admin_client, keep["id"]))["photo"]
    assert before is not None and after["id"] == before["id"]


async def test_recommitting_after_a_merge_keeps_the_observation(admin_client, admin, db_session):
    loc, keep, dupe = await _two_relishes(admin_client)
    p = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "BRMBL RLSH JAR", "line_total": "4.10"}]
    )
    await resolution.resolve_purchase(db_session, uuid.UUID(p))
    line = (await admin_client.get(f"/api/v1/purchases/{p}")).json()["lines"][0]
    await admin_client.post(
        f"/api/v1/purchases/{p}/lines/{line['id']}/resolve", json={"product_id": dupe["id"]}
    )
    assert (await admin_client.post(f"/api/v1/purchases/{p}/commit")).status_code == 200
    assert (await _merge(admin_client, dupe["id"], keep["id"])).status_code == 200
    line = (await admin_client.get(f"/api/v1/purchases/{p}")).json()["lines"][0]
    assert line["product"]["id"] == keep["id"]

    assert (await admin_client.post(f"/api/v1/purchases/{p}/reopen")).status_code == 200
    assert (await admin_client.post(f"/api/v1/purchases/{p}/commit")).status_code == 200
    records = (
        await admin_client.get(
            "/api/v1/price-observations", params={"product_id": keep["id"], "include_voided": True}
        )
    ).json()["items"]
    assert len(records) == 1 and records[0]["voided"] is False
