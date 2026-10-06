"""The unified inbox (docs/spec/09, UI-2.11 and UI-2.15). Invented vendors and the
synthetic geography only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import update

from app.core.config import get_settings
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
    assert body == {
        "items": [],
        "reading": {
            "count": 0,
            "photos": 0,
            "pages": 0,
            "oldest_at": None,
            "stalled": False,
            "lookups_overdue": 0,
            "lookups_since": None,
        },
    }


async def test_a_draft_receipt_is_an_item_with_its_day_and_line_count(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    # The year as the household sees it, which is what decides whether it shows.
    this_year = datetime.now(ZoneInfo(get_settings().household_timezone)).year
    pid = await make_receipt_purchase(
        admin.id, loc["id"], LINES, purchased_at=datetime(this_year, 9, 24, 18, 0, tzinfo=UTC)
    )
    [item] = (await get_inbox(admin_client))["items"]
    assert item["kind"] == "receipt"
    # 18:00 UTC is still Sep 24 in the household timezone.
    assert item["title"] == "Finish the Sep 24 receipt"
    assert item["detail"] == "2 lines ready to review and commit."
    assert (item["action_label"], item["action_route"]) == ("Review", f"/shop/purchases/{pid}")


async def test_a_receipt_from_another_year_says_which(admin_client, admin):
    # Found by /devex-review: a receipt dated years back read "Finish the Jul 30
    # receipt", as if it were from this summer.
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    await make_receipt_purchase(
        admin.id, loc["id"], LINES, purchased_at=datetime(2020, 7, 30, 18, 0, tzinfo=UTC)
    )
    [item] = (await get_inbox(admin_client))["items"]
    assert item["title"] == "Finish the Jul 30, 2020 receipt"


async def test_a_draft_without_a_location_asks_for_one(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES[:1])
    await set_purchase(pid, vendor_location_id=None)
    [item] = (await get_inbox(admin_client))["items"]
    assert item["detail"] == "1 line read. Choose where you shopped to commit it."
    assert item["action_label"] == "Choose location"


async def test_a_receipt_with_no_lines_read_asks_for_the_lines(admin_client, admin):
    # Not "0 lines read. Choose where you shopped": the location is not the problem.
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], [], total="0")
    await set_purchase(pid, vendor_location_id=None)
    [item] = (await get_inbox(admin_client))["items"]
    assert item["detail"] == "No lines could be read. Add them from the receipt image."
    assert (item["action_label"], item["action_route"]) == ("Add lines", f"/shop/purchases/{pid}")


async def test_committing_a_read_receipt_finishes_its_job(admin_client, admin, receipts_dir: Path):
    # The Receipts page reads the job; a job left at needs_review kept saying
    # "ready to review" for a purchase already in the price book.
    job = await upload_job(admin_client, "committed receipt")
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES[:1])
    await set_job(job, purchase_id=uuid.UUID(pid), status="needs_review", stage="review")
    r = await admin_client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200, r.text
    listed = (await admin_client.get(f"/api/v1/ingest-jobs/{job}")).json()
    assert (listed["status"], listed["stage"]) == ("done", "committed")


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


async def test_a_long_queue_that_is_moving_has_not_stalled(
    admin_client, receipts_dir: Path, owner_conn
):
    # The last of a batch has waited 20 minutes, but a stage finished a minute ago.
    job = await upload_job(admin_client, "queued behind others")
    await set_job(job, created_at=datetime.now(UTC) - timedelta(minutes=20))
    await owner_conn.execute(
        "INSERT INTO ingest_stage_result (id, job_id, stage, adapter, adapter_version, output,"
        " duration_ms, created_at) VALUES ($1, $2::uuid, 'ocr', 'tesseract', '5', '{}', 900,"
        " now() - interval '1 minute')",
        uuid.uuid4(),
        job,
    )
    assert (await get_inbox(admin_client))["reading"]["stalled"] is False


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
    assert item["action_label"] == "Open receipt"
    assert item["action_route"] == f"/shop/receipts?job={job}"
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
    assert (item["kind"], item["action_route"]) == ("receipt", f"/shop/purchases/{pid}")


async def test_a_failed_read_without_a_purchase_is_an_item(admin_client, receipts_dir: Path):
    job = await upload_job(admin_client, "failed-no-draft")
    await set_job(job, status="failed", last_error="no_ocr_text", purchase_id=None)
    [item] = (await get_inbox(admin_client))["items"]
    assert (item["kind"], item["action_route"]) == ("receipt_failed", f"/shop/receipts?job={job}")


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
    assert item["action_route"] == "/shop/receipts/identify"
    assert item["created_at"].startswith("2026-09-10")


async def test_one_unmatched_line_reads_singular(admin_client, admin):
    loc = await make_location(admin_client, "Tideline Market", "Tideline Market")
    pid = await make_receipt_purchase(admin.id, loc["id"], LINES[:1])
    await set_purchase(pid, status="committed")
    [item] = (await get_inbox(admin_client))["items"]
    assert item["title"] == "1 receipt line to identify"


async def test_products_needing_a_bridge_are_one_aggregate_item(admin_client):
    loc = await make_location(admin_client, "Pellar Grocer", "Pellar Grocer")
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    meal = await make_product(admin_client, "Cornmeal", "Coarse cornmeal")
    await shelf(admin_client, flour["id"], loc["id"], "0.89", qty="1", unit="cup")
    await shelf(admin_client, flour["id"], loc["id"], "0.95", qty="2", unit="cup")
    await shelf(admin_client, meal["id"], loc["id"], "0.70", qty="1", unit="cup")
    [item] = (await get_inbox(admin_client))["items"]
    assert item["kind"] == "bridge"
    assert item["title"] == "2 products need a density before their prices compare"
    assert item["detail"] == "Each is added once, on its ingredient."
    assert item["action_label"] == "Add densities"
    assert item["action_route"] == "/catalog/bridges"


async def test_one_product_needing_a_pack_reads_singular(admin_client):
    loc = await make_location(admin_client, "Pellar Grocer", "Pellar Grocer")
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    await shelf(admin_client, flour["id"], loc["id"], "4.10")  # "each" with no pack size
    [item] = (await get_inbox(admin_client))["items"]
    assert item["title"] == "1 product needs a pack size before its prices compare"
    assert item["action_label"] == "Set packs"


async def test_a_product_failing_two_ways_counts_once(admin_client):
    loc = await make_location(admin_client, "Pellar Grocer", "Pellar Grocer")
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    await shelf(admin_client, flour["id"], loc["id"], "0.89", qty="1", unit="cup")
    await shelf(admin_client, flour["id"], loc["id"], "4.10")  # "each" with no pack size
    [item] = (await get_inbox(admin_client))["items"]
    assert item["title"] == "1 product needs a bridge before its prices compare"
    assert item["detail"] == "1 needs a pack size and 1 a density."
    assert item["action_label"] == "Review"


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
