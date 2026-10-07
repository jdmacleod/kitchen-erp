"""Restoring a removed purchase (#210; spec 04, 2H).

A voided purchase goes back to reviewed. Its voided prices stay voided, since
observations are append-only, and committing it again records new ones. Only a
voided purchase can be restored, and not one whose receipt was uploaded again.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.core.db import get_sessionmaker
from app.services import removal
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.pricebook_helpers import make_location, make_product
from tests.removal_helpers import receipt_draft

no_network = gh.no_network
receipts_dir = ih.receipts_dir


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


async def _manual(client: httpx.AsyncClient) -> dict:
    loc = await make_location(client, "Fernbrook Grocer", "Fernbrook Grocer")
    lines = []
    for name, total in (("Fernbrook oats", "3.40"), ("Fernbrook honey", "6.15")):
        product = await make_product(client, name, name)
        lines.append({"product_id": product["id"], "qty": "1", "unit": "each", "line_total": total})
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime(2026, 7, 3, 12, 0, tzinfo=UTC).isoformat(),
        "lines": lines,
    }
    r = await client.post("/api/v1/purchases", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _observations(client: httpx.AsyncClient, purchase: dict) -> list[dict]:
    line_ids = {line["id"] for line in purchase["lines"]}
    r = await client.get("/api/v1/price-observations", params={"include_voided": "true"})
    assert r.status_code == 200, r.text
    return [o for o in r.json()["items"] if o["purchase_line_id"] in line_ids]


async def _restore(client: httpx.AsyncClient, purchase_id: str) -> httpx.Response:
    return await client.post(f"/api/v1/purchases/{purchase_id}/restore")


async def test_a_restored_purchase_is_reviewed_and_commits_new_prices(admin_client):
    purchase = await _manual(admin_client)
    pid = purchase["id"]
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/remove")).status_code == 200
    voided = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    assert voided["status"] == "voided" and voided["restore_blocked"] is None

    r = await _restore(admin_client, pid)
    assert r.status_code == 200, r.text
    restored = r.json()
    assert restored["status"] == "reviewed"
    assert restored["voided_at"] is None and restored["voided_by_name"] is None
    assert restored["removal"] is not None  # it can be removed again
    assert [line["id"] for line in restored["lines"]] == [line["id"] for line in purchase["lines"]]
    # Restoring records nothing; the voided prices stay voided.
    before = await _observations(admin_client, purchase)
    assert len(before) == 2 and all(o["voided"] for o in before)

    r = await admin_client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200, r.text
    after = await _observations(admin_client, purchase)
    live = [o for o in after if not o["voided"]]
    assert len(after) == 4
    assert sorted(Decimal(o["price"]) for o in live) == [Decimal("3.40"), Decimal("6.15")]
    assert {o["id"] for o in before} <= {o["id"] for o in after if o["voided"]}


async def test_a_restored_receipt_purchase_commits_again(admin_client, receipts_dir):
    loc = await make_location(admin_client, "Fernbrook Market", "Fernbrook Market")
    pears = await make_product(admin_client, "Fernbrook pears", "Fernbrook pears")
    _, pid = await receipt_draft(
        admin_client, "restore-receipt", location_id=loc["id"], product_id=pears["id"]
    )
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).status_code == 200
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/remove")).status_code == 200
    assert (await admin_client.get(f"/api/v1/purchases/{pid}")).json()["restore_blocked"] is None

    r = await _restore(admin_client, pid)
    assert r.status_code == 200 and r.json()["status"] == "reviewed"
    r = await admin_client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200 and r.json()["status"] == "committed"
    live = [o for o in await _observations(admin_client, r.json()) if not o["voided"]]
    assert len(live) == 1


@pytest.mark.parametrize("status", ["draft", "reviewed", "committed"])
async def test_only_a_voided_purchase_can_be_restored(admin_client, receipts_dir, status: str):
    purchase = await _manual(admin_client)
    pid = purchase["id"]
    if status != "committed":
        assert (await admin_client.post(f"/api/v1/purchases/{pid}/reopen")).status_code == 200
    if status == "draft":
        _, pid = await receipt_draft(admin_client, "restore-draft")
    r = await _restore(admin_client, pid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_voided"


async def test_restoring_twice_is_refused_the_second_time(admin_client):
    pid = (await _manual(admin_client))["id"]
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/remove")).status_code == 200
    assert (await _restore(admin_client, pid)).status_code == 200
    r = await _restore(admin_client, pid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_voided"


async def test_two_restores_at_once_restore_it_once(admin_client):
    pid = (await _manual(admin_client))["id"]
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/remove")).status_code == 200

    async def attempt() -> str:
        async with get_sessionmaker()() as db:
            try:
                await removal.restore_purchase(db, uuid.UUID(pid))
            except removal.ApiError as e:
                return e.code
            return "ok"

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == ["not_voided", "ok"]


async def test_a_receipt_uploaded_again_blocks_restoring_its_old_purchase(
    admin_client, receipts_dir
):
    loc = await make_location(admin_client, "Fernbrook Corner", "Fernbrook Corner")
    pears = await make_product(admin_client, "Fernbrook quinces", "Fernbrook quinces")
    _, pid = await receipt_draft(
        admin_client, "restore-read-again", location_id=loc["id"], product_id=pears["id"]
    )
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).status_code == 200
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/remove")).status_code == 200
    again = await ih.upload(admin_client, ih.png_bytes("restore-read-again"))
    assert again.json()["revived"] is True

    got = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    assert got["restore_blocked"] == "read_again"
    r = await _restore(admin_client, pid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "read_again"
    assert (await admin_client.get(f"/api/v1/purchases/{pid}")).json()["status"] == "voided"


async def test_restore_needs_a_signed_in_user(client):
    r = await client.post(f"/api/v1/purchases/{uuid.uuid4()}/restore")
    assert r.status_code == 401


async def test_restoring_an_unknown_purchase_is_not_found(admin_client):
    r = await _restore(admin_client, str(uuid.uuid4()))
    assert r.status_code == 404
