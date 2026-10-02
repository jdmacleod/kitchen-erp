"""Product captures and proposals (04, 2L): criteria 73–78.

Vendors, pages and products are invented; codes get computed check digits.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from PIL import Image

from app.catalog.identifiers import check_digit
from app.catalog.proposals import Candidate
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import ApiError
from app.models import AppUser
from app.services import pricebook, product_photos, proposals
from app.services.product_photos import PhotoUpload
from app.services.proposals import AcceptInput, Evidence
from tests.pricebook_helpers import make_location, make_product


def gtin(body: str) -> str:
    digits = body.replace(" ", "")
    return digits + str(check_digit(digits))


PAGE_GTIN = gtin("000 4812 3000 01")
SCAN_GTIN = gtin("000 4812 3000 02")
PAGE = "https://shop.example.test/p/oat-tin-500"


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


@pytest.fixture
async def user(admin) -> AppUser:
    return admin


@pytest.fixture
async def store(admin_client) -> dict:
    return await make_location(admin_client, "Juniper Market", "Juniper Market")


def page_evidence(store: dict, *, url: str = PAGE, gtin_value: str | None = PAGE_GTIN, **extra):
    candidates = [
        Candidate("title", "Rolled oats tin", "page_data"),
        Candidate("title", "Oats | Juniper Market", "page_meta"),
        Candidate("brand", "Hollow Creek", "page_data"),
        Candidate("pack", {"qty": "500", "unit": "g"}, "page_data"),
        Candidate("price", "3.49", "page_data"),
        Candidate("item_number", "77123", "address"),
    ]
    if gtin_value:
        candidates.append(Candidate("gtin", gtin_value, "page_data"))
    return Evidence(
        candidates=candidates + extra.pop("more", []),
        listing={"vendor_id": store["vendor"]["id"], "canonical_url": url, "title": "Oats"},
        price={"amount": "3.49", "qty": "1", "unit": "each", "is_promo": False},
        **extra,
    )


async def capture(user, evidence, *, payload=None, channel="clip", url=PAGE, when=None):
    async with get_sessionmaker()() as db:
        result = await proposals.create_capture(
            db,
            user=user,
            channel=channel,
            payload=payload or {"page": url, "dom_text": "Rolled oats tin. 500 g. Price 3.49."},
            evidence=evidence,
            source_url=url,
            captured_at=when,
        )
        return result


async def status_of(proposal_id) -> str:
    async with get_sessionmaker()() as db:
        return (await proposals.get_proposal(db, proposal_id)).status


def jpeg(colour=(30, 60, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (300, 300), colour).save(out, format="JPEG")
    return out.getvalue()


async def work() -> None:
    async with get_sessionmaker()() as db:
        while await product_photos.run_once(db, locked_by="test"):
            pass


# --- 73 -------------------------------------------------------------------------------


async def test_a_proposal_lists_each_field_with_source_and_alternatives(admin_client, user, store):
    """Criterion 73."""
    scanned = Candidate("gtin", SCAN_GTIN, "scan")
    result = await capture(user, page_evidence(store, more=[scanned]))
    r = await admin_client.get(f"/api/v1/product-proposals/{result.proposal.id}")
    assert r.status_code == 200, r.text
    fields = r.json()["fields"]
    assert fields["title"]["value"] == "Rolled oats tin"
    assert fields["title"]["source"] == "page_data"
    assert fields["title"]["alternatives"] == [
        {"value": "Oats | Juniper Market", "source": "page_meta", "confidence": None}
    ]
    assert fields["pack"]["value"] == {"qty": "500", "unit": "g"}
    # The scanned code outranks the page's, and the two are flagged, not settled.
    assert fields["gtin"]["value"] == SCAN_GTIN and fields["gtin"]["conflict"] is True
    assert r.json()["capture"]["channel"] == "clip"

    # Accepting with the conflict open is refused; choosing a value settles it.
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()
    body = {"action": "new", "ingredient_id": ing["id"]}
    r = await admin_client.post(f"/api/v1/product-proposals/{result.proposal.id}/accept", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "unresolved_conflict"
    assert await status_of(result.proposal.id) == "pending"


# --- 74 -------------------------------------------------------------------------------


async def test_a_gtin_match_preselects_update_and_fuzzy_preselects_nothing(
    admin_client, user, store
):
    """Criterion 74."""
    known = await make_product(admin_client, "Oats", "Rolled oats tin", barcode=PAGE_GTIN)
    strong = await capture(user, page_evidence(store))
    assert strong.proposal.match["strong"] == {"product_id": known["id"], "reason": "identifier"}
    assert strong.proposal.match["preselect"] == f"update:{known['id']}"

    fuzzy = await capture(
        user,
        page_evidence(store, url="https://shop.example.test/p/other", gtin_value=None),
        url="https://shop.example.test/p/other",
    )
    assert fuzzy.proposal.match["strong"] is None
    assert fuzzy.proposal.match["preselect"] is None
    assert known["id"] in [c["product_id"] for c in fuzzy.proposal.match["candidates"]]

    picked = proposals.apply_model_pick(fuzzy.proposal.match, known["id"])
    assert picked["model"] == {"pick": known["id"]}
    outside = proposals.apply_model_pick(fuzzy.proposal.match, str(uuid.uuid4()))
    assert "pick" not in outside["model"] and "rejected" in outside["model"]
    assert proposals.apply_model_pick(fuzzy.proposal.match, "new")["model"] == {"pick": "new"}


# --- 75 -------------------------------------------------------------------------------


async def test_accepting_a_new_product_creates_everything_together(
    admin_client, user, store, owner_conn: asyncpg.Connection
):
    """Criterion 75, first part: product, codes, listing, photos, main photo, one price."""
    result = await capture(user, page_evidence(store))
    async with get_sessionmaker()() as db:
        await product_photos.add_photos(
            db,
            None,
            [PhotoUpload(jpeg())],
            proposal_id=result.proposal.id,
            vendor_id=uuid.UUID(store["vendor"]["id"]),
            source_kind="vendor_listing",
        )
    await work()
    r = await admin_client.get(f"/api/v1/product-proposals/{result.proposal.id}")
    [photo] = r.json()["photos"]
    assert photo["status"] == "candidate" and photo["product_id"] is None

    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{result.proposal.id}/accept",
        json={
            "action": "new",
            "ingredient_id": ing["id"],
            "record_price": True,
            "vendor_location_id": store["id"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted"
    product_id = body["result"]["product_id"]
    product = (await admin_client.get(f"/api/v1/products/{product_id}")).json()
    assert (product["name"], product["brand"], product["pack_qty"], product["pack_unit"]) == (
        "Rolled oats tin",
        "Hollow Creek",
        "500",
        "g",
    )
    assert product["kind"] == "branded"
    assert product["photo"]["id"] == photo["id"]
    codes = await owner_conn.fetch(
        "SELECT scheme, value, source FROM product_identifier "
        "WHERE product_id = $1 ORDER BY scheme",
        uuid.UUID(product_id),
    )
    assert [tuple(c) for c in codes] == [
        ("gtin", PAGE_GTIN, "listing"),
        ("vendor_sku", "77123", "listing"),
    ]
    listing = await owner_conn.fetchrow(
        "SELECT product_id, canonical_url FROM vendor_listing WHERE id = $1",
        uuid.UUID(body["result"]["listing_id"]),
    )
    assert listing["product_id"] == uuid.UUID(product_id) and listing["canonical_url"] == PAGE
    posted = await owner_conn.fetch(
        "SELECT price, source FROM price_observation WHERE product_id = $1", uuid.UUID(product_id)
    )
    assert [(p["price"], p["source"]) for p in posted] == [(Decimal("3.4900"), "listing")]


async def test_a_failure_mid_accept_leaves_nothing_and_the_proposal_pending(
    admin_client, user, store, owner_conn: asyncpg.Connection, monkeypatch
):
    """Criterion 75, second part."""
    result = await capture(user, page_evidence(store))
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()
    before = {
        t: await owner_conn.fetchval(f"SELECT count(*) FROM {t}")
        for t in ("product", "product_identifier", "vendor_listing", "price_observation")
    }

    async def broken(*args, **kwargs):
        raise RuntimeError("the disk filled up")

    monkeypatch.setattr(pricebook, "observe", broken)
    async with get_sessionmaker()() as db:
        with pytest.raises(RuntimeError):
            await proposals.accept(
                db,
                user,
                result.proposal.id,
                AcceptInput(
                    action="new",
                    ingredient_id=uuid.UUID(ing["id"]),
                    record_price=True,
                    vendor_location_id=uuid.UUID(store["id"]),
                ),
            )
    after = {t: await owner_conn.fetchval(f"SELECT count(*) FROM {t}") for t in before}
    assert after == before
    assert await status_of(result.proposal.id) == "pending"
    payload = await owner_conn.fetchval(
        "SELECT payload FROM product_capture WHERE id = $1", result.capture.id
    )
    assert "dom_text" in payload


async def test_a_code_another_product_holds_answers_409_naming_it(admin_client, user, store):
    """Criterion 75, last part (PR6)."""
    holder = await make_product(admin_client, "Oats", "Porridge oats", barcode=PAGE_GTIN)
    result = await capture(user, page_evidence(store))
    other = (await admin_client.post("/api/v1/ingredients", json={"name": "Barley"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{result.proposal.id}/accept",
        json={"action": "new", "ingredient_id": other["id"]},
    )
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "identifier_taken"
    assert error["details"] == {"product_id": holder["id"], "name": "Porridge oats"}
    assert await status_of(result.proposal.id) == "pending"
    # "Update {product} instead" works.
    r = await admin_client.post(
        f"/api/v1/product-proposals/{result.proposal.id}/accept",
        json={"action": "update", "product_id": holder["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["product_id"] == holder["id"]
    updated = (await admin_client.get(f"/api/v1/products/{holder['id']}")).json()
    assert updated["name"] == "Porridge oats"  # the household's own name is kept
    assert (updated["pack_qty"], updated["pack_unit"]) == ("500", "g")  # an empty field is filled


async def test_a_decided_proposal_answers_409(admin_client, user, store):
    result = await capture(user, page_evidence(store, gtin_value=None))
    r = await admin_client.post(f"/api/v1/product-proposals/{result.proposal.id}/reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    r = await admin_client.post(f"/api/v1/product-proposals/{result.proposal.id}/reject")
    assert r.status_code == 409 and r.json()["error"]["code"] == "proposal_not_pending"


# --- 76 -------------------------------------------------------------------------------


async def test_two_captures_of_one_page_at_once_leave_one_pending(user, store, owner_conn):
    """Criterion 76, first part."""
    first, second = await asyncio.gather(
        capture(user, page_evidence(store), payload={"page": PAGE, "variant": 1}),
        capture(user, page_evidence(store), payload={"page": PAGE, "variant": 2}),
    )
    pending = await owner_conn.fetchval(
        "SELECT count(*) FROM product_proposal WHERE status = 'pending'"
    )
    assert pending == 1
    statuses = {await status_of(first.proposal.id), await status_of(second.proposal.id)}
    assert statuses == {"pending", "superseded"}


async def test_adding_a_gtin_supersedes_the_pending_proposal_with_it(admin_client, user):
    """Criterion 76, second part: an edit goes through the same helper."""
    scan = Evidence(candidates=[Candidate("gtin", SCAN_GTIN, "scan")])
    by_scan = await capture(user, scan, channel="barcode", url=None, payload={"code": SCAN_GTIN})
    photo = Evidence(candidates=[Candidate("title", "Oat tin", "model", Decimal("0.4"))])
    by_photo = await capture(user, photo, channel="photo", url=None, payload={"photos": 1})
    r = await admin_client.patch(
        f"/api/v1/product-proposals/{by_photo.proposal.id}", json={"edits": {"gtin": SCAN_GTIN}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["fields"]["gtin"]["source"] == "person"
    assert await status_of(by_scan.proposal.id) == "superseded"
    assert await status_of(by_photo.proposal.id) == "pending"


async def test_the_same_gtin_supersedes_only_when_neither_has_a_page(user, store):
    """Criterion 76, last part."""
    scan = Evidence(candidates=[Candidate("gtin", PAGE_GTIN, "scan")])
    by_scan = await capture(user, scan, channel="barcode", url=None, payload={"code": PAGE_GTIN})
    by_page = await capture(user, page_evidence(store))
    assert await status_of(by_scan.proposal.id) == "pending"
    assert await status_of(by_page.proposal.id) == "pending"
    again = await capture(
        user, scan, channel="barcode", url=None, payload={"code": PAGE_GTIN, "n": 2}
    )
    assert await status_of(by_scan.proposal.id) == "superseded"
    assert await status_of(again.proposal.id) == "pending"
    assert await status_of(by_page.proposal.id) == "pending"


class Gate:
    """Hold one named pause point until released."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.used = False

    async def __call__(self, name: str) -> None:
        if name == self.name and not self.used:
            self.used = True
            self.reached.set()
            await self.release.wait()


async def _accept(user, proposal_id, ingredient_id):
    async with get_sessionmaker()() as db:
        return await proposals.accept(
            db, user, proposal_id, AcceptInput(action="new", ingredient_id=ingredient_id)
        )


async def test_accept_then_supersede(admin_client, user, store, monkeypatch):
    """Criterion 76: accept holds the lock first; the newer capture waits and then stays pending."""
    old = await capture(user, page_evidence(store, gtin_value=None))
    ing = uuid.UUID(
        (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()["id"]
    )
    gate = Gate("accept:locked")
    monkeypatch.setattr(proposals, "pause", gate)
    accepting = asyncio.create_task(_accept(user, old.proposal.id, ing))
    await gate.reached.wait()
    newer = asyncio.create_task(
        capture(user, page_evidence(store, gtin_value=None), payload={"page": PAGE, "n": 2})
    )
    await asyncio.sleep(0.3)  # the newer capture is now waiting on the proposal's lock
    assert not newer.done()
    gate.release.set()
    accepted = await accepting
    later = await newer
    assert accepted.status == "accepted"
    assert await status_of(old.proposal.id) == "accepted"
    assert await status_of(later.proposal.id) == "pending"


async def test_supersede_then_accept(admin_client, user, store, monkeypatch):
    """Criterion 76: the newer capture locks first; the accept then finds it superseded."""
    old = await capture(user, page_evidence(store, gtin_value=None))
    ing = uuid.UUID(
        (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()["id"]
    )
    gate = Gate("supersede:locked")
    monkeypatch.setattr(proposals, "pause", gate)
    newer = asyncio.create_task(
        capture(user, page_evidence(store, gtin_value=None), payload={"page": PAGE, "n": 2})
    )
    await gate.reached.wait()
    accepting = asyncio.create_task(_accept(user, old.proposal.id, ing))
    await asyncio.sleep(0.3)  # the accept is now waiting on the proposal's lock
    assert not accepting.done()
    gate.release.set()
    later = await newer
    with pytest.raises(ApiError) as refused:
        await asyncio.wait_for(accepting, timeout=10)
    assert refused.value.code == "proposal_not_pending"
    assert await status_of(old.proposal.id) == "superseded"
    assert await status_of(later.proposal.id) == "pending"


# --- 77 -------------------------------------------------------------------------------


async def test_a_retry_returns_the_same_capture_and_a_decided_one_starts_afresh(
    admin_client, user, store
):
    """Criterion 77."""
    first = await capture(user, page_evidence(store), when=datetime.now(UTC) - timedelta(minutes=5))
    retry = await capture(user, page_evidence(store), when=datetime.now(UTC))
    assert not retry.created
    assert (retry.capture.id, retry.proposal.id) == (first.capture.id, first.proposal.id)

    await admin_client.post(f"/api/v1/product-proposals/{first.proposal.id}/reject")
    again = await capture(user, page_evidence(store))
    assert again.created and again.capture.id != first.capture.id
    assert again.proposal.status == "pending"


# --- 78 -------------------------------------------------------------------------------


async def test_page_text_is_purged_on_decision_and_nothing_else_can_change(
    admin_client, user, store, app_conn: asyncpg.Connection, owner_conn: asyncpg.Connection
):
    """Criterion 78."""
    result = await capture(user, page_evidence(store, gtin_value=None))
    await admin_client.post(f"/api/v1/product-proposals/{result.proposal.id}/reject")
    payload = await owner_conn.fetchval(
        "SELECT payload FROM product_capture WHERE id = $1", result.capture.id
    )
    payload = json.loads(payload)
    assert "dom_text" not in payload and payload["page"] == PAGE

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute(
            "UPDATE product_capture SET source_url = 'x' WHERE id = $1", result.capture.id
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("DELETE FROM product_capture WHERE id = $1", result.capture.id)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
        await owner_conn.execute(
            "UPDATE product_capture SET source_url = 'x' WHERE id = $1", result.capture.id
        )
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
        await app_conn.execute(
            "UPDATE product_capture SET payload = payload || '{\"added\": 1}' WHERE id = $1",
            result.capture.id,
        )
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
        await owner_conn.execute("DELETE FROM product_capture WHERE id = $1", result.capture.id)


async def test_a_gtin_given_at_accept_supersedes_the_pending_scan_with_it(admin_client, user):
    scan = Evidence(candidates=[Candidate("gtin", SCAN_GTIN, "scan")])
    by_scan = await capture(user, scan, channel="barcode", url=None, payload={"code": SCAN_GTIN})
    photo = Evidence(candidates=[Candidate("title", "Oat tin", "model", Decimal("0.4"))])
    by_photo = await capture(user, photo, channel="photo", url=None, payload={"photos": 2})
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{by_photo.proposal.id}/accept",
        json={"action": "new", "ingredient_id": ing["id"], "edits": {"gtin": SCAN_GTIN}},
    )
    assert r.status_code == 200, r.text
    assert await status_of(by_scan.proposal.id) == "superseded"


# --- review support (S5) ---------------------------------------------------------------


async def test_pending_proposals_are_one_inbox_row_that_leaves_on_decision(
    admin_client, user, store
):
    """Criterion 82, first part."""
    first = await capture(user, page_evidence(store, gtin_value=None))
    await capture(
        user,
        page_evidence(store, url="https://shop.example.test/p/2", gtin_value=None),
        url="https://shop.example.test/p/2",
    )
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    rows = [i for i in items if i["kind"] == "new_product"]
    assert [r["title"] for r in rows] == ["2 products to review"]
    assert rows[0]["action_route"] == f"/catalog/products/review/{first.proposal.id}"
    for p in (await admin_client.get("/api/v1/product-proposals")).json()["items"]:
        await admin_client.post(f"/api/v1/product-proposals/{p['id']}/reject")
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in items if i["kind"] == "new_product"]


async def test_the_reading_line_counts_photos_being_identified(admin_client, user):
    """Criterion 82, last part: counted, and stalled past INGEST_STALL_MINUTES."""
    async with get_sessionmaker()() as db:
        await proposals.capture_photos(db, user, [PhotoUpload(jpeg())])
    reading = (await admin_client.get("/api/v1/inbox")).json()["reading"]
    assert (reading["count"], reading["photos"], reading["pages"]) == (0, 1, 0)
    assert reading["stalled"] is False
    async with get_sessionmaker()() as db:
        from sqlalchemy import text as sql

        await db.execute(sql("UPDATE product_job SET created_at = now() - interval '1 hour'"))
        await db.commit()
    reading = (await admin_client.get("/api/v1/inbox")).json()["reading"]
    assert reading["stalled"] is True
    await work()
    reading = (await admin_client.get("/api/v1/inbox")).json()["reading"]
    assert reading["photos"] == 0


async def test_the_proposal_names_its_vendor_and_stores(admin_client, user, store):
    result = await capture(user, page_evidence(store, gtin_value=None))
    body = (await admin_client.get(f"/api/v1/product-proposals/{result.proposal.id}")).json()
    assert body["vendor"]["name"] == "Juniper Market"
    assert body["vendor"]["locations"] == [{"id": store["id"], "name": "Juniper Market"}]
    assert body["vendor"]["suggested_location_id"] is None  # never bought there yet


async def test_accept_applies_the_reviewers_photo_choices(admin_client, user, store):
    result = await capture(user, page_evidence(store, gtin_value=None))
    async with get_sessionmaker()() as db:
        images = await product_photos.add_photos(
            db,
            None,
            [
                PhotoUpload(jpeg((200, 30, 30))),
                PhotoUpload(jpeg((30, 200, 30))),
                PhotoUpload(jpeg((30, 30, 200))),
            ],
            proposal_id=result.proposal.id,
        )
    await work()
    first, second, third = (str(i.id) for i in images)
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Oats"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{result.proposal.id}/accept",
        json={
            "action": "new",
            "ingredient_id": ing["id"],
            "main_photo_id": second,
            "photo_roles": {third: "label_nutrition"},
            "hidden_photo_ids": [first],
        },
    )
    assert r.status_code == 200, r.text
    product_id = r.json()["result"]["product_id"]
    photos = {
        p["id"]: p
        for p in (await admin_client.get(f"/api/v1/products/{product_id}/photos")).json()["items"]
    }
    assert photos[second]["is_main"] and photos[second]["pinned"]
    assert photos[first]["status"] == "hidden"
    assert photos[third]["role"] == "label_nutrition"
