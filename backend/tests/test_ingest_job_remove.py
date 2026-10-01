"""Removing a receipt that couldn't be read, and uploading it again (#74).

Criteria 58 and 59: POST /ingest-jobs/{id}/remove discards a failed job and
its draft, and deletes the photo; a discarded job is hidden; the same file
uploaded again is read again.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.db import get_sessionmaker
from app.models import IngestJob
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.ingest_helpers import png_bytes, upload
from tests.removal_helpers import receipt_draft, set_job_status, stored_photo, upload_receipt

no_network = gh.no_network
receipts_dir = ih.receipts_dir


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


async def _remove(client, job_id: str):
    return await client.post(f"/api/v1/ingest-jobs/{job_id}/remove")


async def test_a_failed_read_with_no_purchase_is_discarded(admin_client, receipts_dir: Path):
    body = await upload_receipt(admin_client, "failed-alone")
    await set_job_status(body["job"]["id"], "failed")
    r = await _remove(admin_client, body["job"]["id"])
    assert r.status_code == 200, r.text
    assert r.json() == {"photo_deleted": True}
    assert not stored_photo(receipts_dir, body["document"]).exists()


async def test_a_failed_read_with_a_draft_takes_the_draft_too(admin_client, receipts_dir: Path):
    body, pid = await receipt_draft(admin_client, "failed-draft", job_status="failed")
    r = await _remove(admin_client, body["job"]["id"])
    assert r.status_code == 200, r.text
    assert (await admin_client.get(f"/api/v1/purchases/{pid}")).status_code == 404
    async with get_sessionmaker()() as db:
        job = await db.get(IngestJob, uuid.UUID(body["job"]["id"]))
        assert job.status == "discarded" and job.purchase_id is None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("pending", "still_reading"),
        ("running", "still_reading"),
        ("done", "not_failed"),
        ("needs_review", "not_failed"),
    ],
)
async def test_only_a_failed_read_is_removed_here(
    admin_client, receipts_dir: Path, status: str, code: str
):
    body = await upload_receipt(admin_client, f"not-failed-{status}")
    await set_job_status(body["job"]["id"], status)
    r = await _remove(admin_client, body["job"]["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == code
    assert stored_photo(receipts_dir, body["document"]).is_file()


async def test_a_removed_receipt_is_hidden_and_refuses_retry(admin_client, receipts_dir: Path):
    body = await upload_receipt(admin_client, "hidden")
    job_id = body["job"]["id"]
    await set_job_status(job_id, "failed")
    await _remove(admin_client, job_id)

    listed = (await admin_client.get("/api/v1/ingest-jobs")).json()["items"]
    assert job_id not in {j["id"] for j in listed}
    r = await admin_client.get(f"/api/v1/ingest-jobs/{job_id}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "receipt_removed"
    for action in ("retry", "to-manual"):
        r = await admin_client.post(f"/api/v1/ingest-jobs/{job_id}/{action}")
        assert r.status_code == 409 and r.json()["error"]["code"] == "receipt_removed", action
    # Removing it again is answered as already done.
    assert (await _remove(admin_client, job_id)).status_code == 404


async def test_uploading_a_removed_receipt_again_reads_it_again(admin_client, receipts_dir: Path):
    body, pid = await receipt_draft(admin_client, "revive", job_status="needs_review")
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/remove")).status_code == 200
    photo = stored_photo(receipts_dir, body["document"])
    assert not photo.exists()

    r = await upload(admin_client, png_bytes("revive"))
    assert r.status_code == 200, r.text
    again = r.json()
    assert again["revived"] is True
    assert again["job"]["id"] == body["job"]["id"]
    assert again["job"]["status"] == "pending" and again["job"]["stage"] == "captured"
    assert again["job"]["purchase_id"] is None
    assert photo.is_file()

    # A plain duplicate is not a revival.
    r = await upload(admin_client, png_bytes("revive"))
    assert r.json()["revived"] is False


async def test_uploading_a_receipt_whose_purchase_was_removed_reads_it_again(
    admin_client, receipts_dir: Path
):
    # Found in a dogfood pass: removing a committed receipt voids its purchase and
    # left its read "done", so the same file uploaded again was answered "uploaded
    # before" with a link to the voided purchase, and could never be read again.
    from tests.pricebook_helpers import make_location, make_product

    loc = await make_location(admin_client, "Quayside Grocer", "Quayside Grocer North")
    product = await make_product(admin_client, "Pear", "Orchard pears")
    body, pid = await receipt_draft(
        admin_client, "voided", location_id=loc["id"], product_id=product["id"]
    )
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).status_code == 200
    job_id = body["job"]["id"]
    async with get_sessionmaker()() as db:
        job = await db.get(IngestJob, uuid.UUID(job_id))
        job.status, job.stage = "done", "committed"
        await db.commit()
    r = await admin_client.post(f"/api/v1/purchases/{pid}/remove")
    assert r.json()["outcome"] == "void"

    r = await upload(admin_client, png_bytes("voided"))
    assert r.status_code == 200, r.text
    again = r.json()
    assert again["revived"] is True and again["job"]["id"] == job_id
    assert (again["job"]["status"], again["job"]["stage"]) == ("pending", "captured")
    assert again["job"]["purchase_id"] is None
    # The voided purchase stays, as the record of the prices it voided.
    voided = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    assert voided["status"] == "voided"
