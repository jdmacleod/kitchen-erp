"""The unified inbox (docs/spec/09, UI-2.11 and UI-2.15). Invented vendors and the
synthetic geography only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import update

from app.core.db import get_sessionmaker
from app.models import IngestJob, Purchase
from app.services import inbox as inbox_service
from tests import ingest_helpers as ih
from tests.pricebook_helpers import make_location, make_product, shelf
from tests.resolution_helpers import make_receipt_purchase

receipts_dir = ih.receipts_dir


async def get_inbox(client: httpx.AsyncClient) -> dict:
    r = await client.get("/api/v1/inbox")
    assert r.status_code == 200, r.text
    return r.json()


async def set_purchase(purchase_id: str, **values) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Purchase).where(Purchase.id == uuid.UUID(purchase_id)).values(**values)
        )
        await db.commit()


async def set_job(job_id: str, **values) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(
            update(IngestJob).where(IngestJob.id == uuid.UUID(job_id)).values(**values)
        )
        await db.commit()


async def upload_job(client: httpx.AsyncClient, seed: str) -> str:
    r = await ih.upload(
        client, ih.png_bytes(seed), ocr_text="TIDELINE MARKET\nMILK 3.49\nTOTAL 3.49"
    )
    assert r.status_code == 201, r.text
    return r.json()["job"]["id"]


LINES = [
    {"raw_text": "OAT BEV 1L", "line_total": "3.49"},
    {"raw_text": "BAG FEE", "line_total": "0.10"},
]


async def test_requires_a_session(client: httpx.AsyncClient):
    assert (await client.get("/api/v1/inbox")).status_code == 401


async def test_empty_inbox_is_empty_not_an_error(admin_client: httpx.AsyncClient):
    body = await get_inbox(admin_client)
    assert body == {"items": [], "reading": {"count": 0, "oldest_at": None, "stalled": False}}


async def test_a_draft_receipt_is_an_item_with_its_day_and_line_count(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(
        admin.id, loc["id"], LINES, purchased_at=datetime(2026, 9, 24, 18, 0, tzinfo=UTC)
    )
    [item] = (await get_inbox(admin_client))["items"]
    assert item["kind"] == "receipt"
    # 18:00 UTC is still Sep 24 in the household timezone.
    assert item["title"] == "Finish the Sep 24 receipt"
    assert item["detail"] == "2 lines ready to review and commit."
    assert (item["action_label"], item["action_route"]) == ("Review", f"/purchases/{pid}")


async def test_a_draft_without_a_location_asks_for_one(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES[:1])
    await set_purchase(pid, vendor_location_id=None)
    [item] = (await get_inbox(admin_client))["items"]
    assert item["detail"] == "1 line read. Choose where you shopped to commit it."
    assert item["action_label"] == "Choose location"


async def test_committed_purchases_are_not_items(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], [{**LINES[0], "line_kind": "fee"}])
    await set_purchase(pid, status="committed")
    assert (await get_inbox(admin_client))["items"] == []


async def test_a_receipt_being_read_is_reading_not_an_item(admin_client, admin, receipts_dir: Path):
    job = await upload_job(admin_client, "reading")
    body = await get_inbox(admin_client)
    assert body["items"] == []
    assert body["reading"]["count"] == 1
    assert body["reading"]["stalled"] is False
    # The pipeline creates the draft while it is still reading; that draft is not
    # yet something to finish.
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES)
    await set_job(job, purchase_id=uuid.UUID(pid))
    assert (await get_inbox(admin_client))["items"] == []


async def test_reading_says_when_it_has_stalled(admin_client, receipts_dir: Path):
    job = await upload_job(admin_client, "stalled")
    await set_job(job, created_at=datetime.now(UTC) - timedelta(minutes=20))
    reading = (await get_inbox(admin_client))["reading"]
    assert reading["count"] == 1
    assert reading["stalled"] is True


async def test_a_failed_read_is_one_item_and_its_draft_is_not_repeated(
    admin_client, admin, receipts_dir: Path
):
    job = await upload_job(admin_client, "failed")
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES)
    await set_job(job, status="failed", last_error="no_ocr_text", purchase_id=uuid.UUID(pid))
    body = await get_inbox(admin_client)
    [item] = body["items"]
    assert item["kind"] == "receipt_failed"
    assert item["error_code"] == "no_ocr_text"
    assert (item["action_label"], item["action_route"]) == ("Open receipt", f"/receipts?job={job}")
    assert body["reading"]["count"] == 0


async def test_a_failed_read_leaves_once_its_draft_is_committed(
    admin_client, admin, receipts_dir: Path
):
    job = await upload_job(admin_client, "failed-then-committed")
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES)
    await set_job(job, status="failed", last_error="no_ocr_text", purchase_id=uuid.UUID(pid))
    await set_purchase(pid, status="committed")
    kinds = [i["kind"] for i in (await get_inbox(admin_client))["items"]]
    assert kinds == ["identify"]  # its unmatched lines, not the old failure

    # Reopened, it is a purchase to finish again, not a failed read.
    await set_purchase(pid, status="reviewed")
    items = (await get_inbox(admin_client))["items"]
    [item] = [i for i in items if i["kind"] != "identify"]
    assert (item["kind"], item["action_route"]) == ("receipt", f"/purchases/{pid}")


async def test_a_failed_read_without_a_purchase_is_an_item(admin_client, receipts_dir: Path):
    job = await upload_job(admin_client, "failed-no-draft")
    await set_job(job, status="failed", last_error="no_ocr_text", purchase_id=None)
    [item] = (await get_inbox(admin_client))["items"]
    assert (item["kind"], item["action_route"]) == ("receipt_failed", f"/receipts?job={job}")


async def test_unmatched_lines_are_one_aggregate_item(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    first = await make_receipt_purchase(
        admin.id, loc["id"], LINES, purchased_at=datetime(2026, 9, 10, 17, tzinfo=UTC)
    )
    second = await make_receipt_purchase(
        admin.id, loc["id"], LINES[:1], purchased_at=datetime(2026, 9, 20, 17, tzinfo=UTC)
    )
    for pid in (first, second):
        await set_purchase(pid, status="committed")
    [item] = (await get_inbox(admin_client))["items"]
    assert item["kind"] == "identify"
    assert item["title"] == "3 receipt lines to identify"
    assert item["action_route"] == "/to-identify"
    assert item["created_at"].startswith("2026-09-10")


async def test_one_unmatched_line_reads_singular(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES[:1])
    await set_purchase(pid, status="committed")
    [item] = (await get_inbox(admin_client))["items"]
    assert item["title"] == "1 receipt line to identify"


async def test_a_product_needing_a_bridge_is_one_item(admin_client):
    loc = await make_location(admin_client, "Pellar Grocer", "Pellar Grocer")
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    await shelf(admin_client, flour["id"], loc["id"], "0.89", qty="1", unit="cup")
    await shelf(admin_client, flour["id"], loc["id"], "0.95", qty="2", unit="cup")
    [item] = (await get_inbox(admin_client))["items"]
    assert item["kind"] == "bridge"
    assert item["title"] == "Bulk flour"
    assert item["detail"] == "Its prices can't be compared until it has a density."
    assert item["action_label"] == "Add density"
    assert item["action_route"] == f"/ingredients/{flour['ingredient']['id']}#density-heading"


async def test_a_product_failing_two_ways_is_merged(admin_client):
    loc = await make_location(admin_client, "Pellar Grocer", "Pellar Grocer")
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    await shelf(admin_client, flour["id"], loc["id"], "0.89", qty="1", unit="cup")
    await shelf(admin_client, flour["id"], loc["id"], "4.10")  # "each" with no pack size
    items = (await get_inbox(admin_client))["items"]
    assert [i["kind"] for i in items] == ["bridge"]
    assert "a density and" in items[0]["detail"]


async def test_items_are_oldest_first_across_kinds(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    identify = await make_receipt_purchase(
        admin.id, loc["id"], LINES[:1], purchased_at=datetime(2020, 1, 5, tzinfo=UTC)
    )
    await set_purchase(identify, status="committed")
    await make_receipt_purchase(admin.id, loc["id"], LINES)  # created now
    kinds = [i["kind"] for i in (await get_inbox(admin_client))["items"]]
    assert kinds == ["identify", "receipt"]


async def test_a_failing_kind_fails_the_request_rather_than_hiding(
    admin_client, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    async def broken(db):
        raise RuntimeError("bridge query failed")

    monkeypatch.setattr(inbox_service, "_KINDS", [*inbox_service._KINDS[:3], ("bridge", broken)])
    with pytest.raises(RuntimeError):
        # ASGITransport re-raises unhandled errors; in production this is a 500.
        await admin_client.get("/api/v1/inbox")
    assert any(r.message == "inbox.kind_failed" and r.kind == "bridge" for r in caplog.records)
