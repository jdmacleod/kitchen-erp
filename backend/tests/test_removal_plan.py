"""Removing a purchase (#74; spec 04, 2H, criteria 54-57).

Never recorded: deleted, with its receipt's photo, and its job discarded.
Recorded: voided, prices and all, and kept. Still being read: refused. The
preview on the purchase always says what removing will then do.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.core.db import get_sessionmaker
from app.models import IngestJob
from app.services import removal
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.pricebook_helpers import make_location, make_product
from tests.removal_helpers import receipt_draft, set_job_status, stored_photo

no_network = gh.no_network
receipts_dir = ih.receipts_dir


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


async def _manual(client: httpx.AsyncClient, n: int = 2) -> dict:
    loc = await make_location(client, "Willow Grocer", "Willow Grocer")
    lines = []
    for name in ("Willow oats", "Willow honey")[:n]:
        product = await make_product(client, name, name)
        line = {"product_id": product["id"], "qty": "1", "unit": "each", "line_total": "3.00"}
        lines.append(line)
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC).isoformat(),
        "lines": lines,
    }
    r = await client.post("/api/v1/purchases", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _remove(client: httpx.AsyncClient, purchase_id: str) -> httpx.Response:
    return await client.post(f"/api/v1/purchases/{purchase_id}/remove")


# --- deleting ------------------------------------------------------------------


async def test_a_never_recorded_receipt_is_deleted_with_its_photo(admin_client, receipts_dir: Path):
    upload, pid = await receipt_draft(admin_client, "never-recorded")
    photo = stored_photo(receipts_dir, upload["document"])
    assert photo.is_file()

    preview = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    assert preview["removal"] == {"outcome": "delete", "prices": 0, "photo": True, "blocked": None}
    assert preview["lines"][0]["recorded"] is False

    r = await _remove(admin_client, pid)
    assert r.status_code == 200, r.text
    assert r.json() == {"outcome": "delete", "photo_deleted": True, "purchase": None}
    assert not photo.exists()
    assert (await admin_client.get(f"/api/v1/purchases/{pid}")).status_code == 404
    image = await admin_client.get(f"/api/v1/receipts/{upload['document']['id']}/image")
    assert image.status_code == 404
    async with get_sessionmaker()() as db:
        job = await db.get(IngestJob, uuid.UUID(upload["job"]["id"]))
        assert job.status == "discarded" and job.purchase_id is None


# --- voiding -------------------------------------------------------------------


async def test_a_recorded_purchase_is_voided_and_kept(admin_client):
    p = await _manual(admin_client)
    obs = {ln["observation_id"] for ln in p["lines"]}
    preview = (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()
    assert preview["removal"] == {"outcome": "void", "prices": 2, "photo": False, "blocked": None}
    assert all(ln["recorded"] for ln in preview["lines"])

    r = await _remove(admin_client, p["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "void" and body["photo_deleted"] is False
    voided = body["purchase"]
    assert voided["status"] == "voided"
    assert voided["voided_at"] is not None and voided["voided_by_name"] == "Admin"
    assert voided["removal"] is None

    everything = (
        await admin_client.get("/api/v1/price-observations", params={"include_voided": "true"})
    ).json()["items"]
    for o in everything:
        if o["id"] in obs:
            assert o["voided"] and o["void_reason"] == "purchase removed"

    # Gone from the list unless asked for.
    listed = (await admin_client.get("/api/v1/purchases")).json()["items"]
    assert p["id"] not in {x["id"] for x in listed}
    only = (await admin_client.get("/api/v1/purchases", params={"status": "voided"})).json()
    assert [x["id"] for x in only["items"]] == [p["id"]]

    # Read-only from now on, and removing it again says so.
    assert (await _remove(admin_client, p["id"])).json()["error"]["code"] == "already_removed"
    r = await admin_client.post(f"/api/v1/purchases/{p['id']}/commit")
    assert r.status_code == 409 and r.json()["error"]["code"] == "voided"


async def test_a_reopened_purchase_with_every_price_voided_still_voids(admin_client):
    p = await _manual(admin_client, 1)
    await admin_client.post(f"/api/v1/purchases/{p['id']}/reopen")
    # Ignoring its only line voids its only price; the purchase has still been
    # in the price book, so it can't be deleted.
    await admin_client.delete(f"/api/v1/purchases/{p['id']}/lines/{p['lines'][0]['id']}")
    got = (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()
    assert got["removal"] == {"outcome": "void", "prices": 0, "photo": False, "blocked": None}
    assert got["removed_line_count"] == 1
    r = await _remove(admin_client, p["id"])
    assert r.status_code == 200 and r.json()["outcome"] == "void"


async def test_a_voided_receipt_keeps_its_photo(admin_client, receipts_dir: Path):
    loc = await make_location(admin_client, "Birch Market", "Birch Market")
    pears = await make_product(admin_client, "Birch pears", "Birch pears")
    upload, pid = await receipt_draft(
        admin_client, "voided-keeps", location_id=loc["id"], product_id=pears["id"]
    )
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).status_code == 200
    r = await _remove(admin_client, pid)
    assert r.status_code == 200 and r.json()["outcome"] == "void"
    assert stored_photo(receipts_dir, upload["document"]).is_file()


# --- refusing ------------------------------------------------------------------


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_a_receipt_still_being_read_is_refused(admin_client, receipts_dir: Path, status: str):
    upload, pid = await receipt_draft(admin_client, f"reading-{status}")
    await set_job_status(upload["job"]["id"], status)
    got = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    assert got["removal"]["blocked"] == "still_reading"
    r = await _remove(admin_client, pid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "still_reading"
    assert (await admin_client.get(f"/api/v1/purchases/{pid}")).status_code == 200


async def test_a_failure_part_way_leaves_every_price_live(admin_client, monkeypatch):
    p = await _manual(admin_client)
    calls = 0
    real = removal.pricebook.void

    async def second_void_fails(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic failure")
        return await real(*args, **kwargs)

    monkeypatch.setattr(removal.pricebook, "void", second_void_fails)
    with pytest.raises(RuntimeError):
        await _remove(admin_client, p["id"])
    monkeypatch.undo()
    live = (await admin_client.get("/api/v1/price-observations")).json()["items"]
    assert {o["id"] for o in live} == {ln["observation_id"] for ln in p["lines"]}
    assert (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()["status"] == "committed"


# --- the list stays cheap --------------------------------------------------------


async def test_list_items_leave_the_detail_fields_empty(admin_client):
    await _manual(admin_client)
    item = (await admin_client.get("/api/v1/purchases")).json()["items"][0]
    assert item["removal"] is None and item["removed_line_count"] is None
    assert all(ln["recorded"] is None for ln in item["lines"])


# --- races (review of #83) -------------------------------------------------------


async def test_a_removal_waits_for_an_edit_and_voids_the_price_it_recorded(admin_client, admin):
    """An edit recording a price while the purchase is being removed must not
    leave that price live: the removal waits for it, then voids it too."""
    from decimal import Decimal

    from sqlalchemy import select

    from app.models import PriceObservation, PurchaseLine
    from app.services import pricebook
    from app.services.purchases import get_purchase

    p = await _manual(admin_client, 1)
    maker = get_sessionmaker()
    async with maker() as editing, maker() as removing:
        purchase = await get_purchase(editing, uuid.UUID(p["id"]), lock=True)
        line = purchase.lines[0]
        racing = asyncio.create_task(removal.remove_purchase(removing, admin, purchase.id))
        await asyncio.sleep(0.3)
        assert not racing.done(), "the removal should wait on the purchase row"
        # What re-pointing the line does: void its price, record a new one.
        await pricebook.void(
            editing, uuid.UUID(p["lines"][0]["observation_id"]), "re-pointed", admin
        )
        await pricebook.observe(
            editing,
            product_id=line.product_id,
            vendor_location_id=purchase.vendor_location_id,
            price=Decimal("2.50"),
            qty=Decimal("1"),
            unit="each",
            source="manual",
            entered_by=admin,
            purchase_line_id=line.id,
        )
        await editing.commit()
        done = await racing
        assert done.outcome == "void"

    async with maker() as check:
        stmt = (
            select(PriceObservation)
            .join(PurchaseLine, PurchaseLine.id == PriceObservation.purchase_line_id)
            .where(PurchaseLine.purchase_id == uuid.UUID(p["id"]))
        )
        observations = (await check.execute(stmt)).unique().scalars().all()
        assert len(observations) == 2
        assert all(o.void is not None for o in observations)
        # The one it voided itself says so.
        assert {o.void.reason for o in observations} == {"re-pointed", "purchase removed"}


async def test_a_receipt_uploaded_again_before_its_photo_goes_keeps_it(
    admin_client, receipts_dir: Path
):
    """The photo is deleted after the commit; an upload in between revives the
    job, and the deletion must then leave the photo it wrote alone."""
    from app.models import ReceiptDocument

    upload, pid = await receipt_draft(admin_client, "revive-race")
    photo = stored_photo(receipts_dir, upload["document"])
    async with get_sessionmaker()() as db:
        purchase = await removal.get_purchase(db, uuid.UUID(pid), lock=True)
        plan = await removal.removal_plan(db, purchase, lock=True)
        await removal._delete(db, purchase, plan.job)
        await db.commit()
        # The same file arrives again before the photo is deleted.
        again = await ih.upload(admin_client, ih.png_bytes("revive-race"))
        assert again.json()["revived"] is True
        document = await db.get(ReceiptDocument, uuid.UUID(upload["document"]["id"]))
        assert await removal.delete_photo(db, document) is False
    assert photo.is_file()


async def test_a_removed_receipt_image_is_not_served_even_if_its_file_remains(
    admin_client, receipts_dir: Path
):
    upload, pid = await receipt_draft(admin_client, "left-behind")
    photo = stored_photo(receipts_dir, upload["document"])
    data = photo.read_bytes()
    assert (await _remove(admin_client, pid)).status_code == 200
    # As if deleting the file had failed.
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(data)
    r = await admin_client.get(f"/api/v1/receipts/{upload['document']['id']}/image")
    assert r.status_code == 404
