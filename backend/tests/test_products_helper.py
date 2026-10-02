"""The products helper contract (04, 2N): criteria 89–92.

A stub helper speaks for the private ``kitchen-erp-products``; every product,
vendor and code is invented.
"""

from __future__ import annotations

import base64
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
import pytest
from PIL import Image

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.main import app
from app.schemas.products_interchange import FORMAT, contract_schema
from app.services import proposals
from app.services.proposals import AcceptInput
from tests.pricebook_helpers import make_location, make_product
from tests.test_captures import UNKNOWN, photo, post_photos, with_check, work
from tests.test_proposals import page_evidence
from tests.test_scopes import token

CONTRACT = (
    Path(__file__).resolve().parents[2] / "docs" / "api" / "kitchen-erp-products-1.schema.json"
)


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


@pytest.fixture
async def helper(admin_client) -> httpx.AsyncClient:
    """The stub helper: a client holding only products:read and products:suggest."""
    read = await token(admin_client, "products:read")
    suggest = await token(admin_client, "products:suggest")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        c.read_headers, c.suggest_headers = read, suggest  # type: ignore[attr-defined]
        yield c


ANOTHER = with_check("5 0123 4500 011")  # a second code nobody has


async def proposal_from_scan(admin_client, code: str = UNKNOWN) -> str:
    found = (await admin_client.post("/api/v1/barcode-lookups", json={"code": code})).json()
    return found["proposal_id"]


def answer(request_id: str, **extra) -> str:
    """A JSON body as the helper writes it, numbers as JSON numbers."""
    body = {
        "format": FORMAT,
        "request_id": request_id,
        "candidates": [
            {
                "field": "title",
                "value": "Strong White Flour",
                "source": "manufacturer",
                "source_url": "https://facts.example.test/p/1",
            },
            {"field": "brand", "value": "Larkfield", "source": "manufacturer"},
            {"field": "pack", "value": {"qty": 1.5, "unit": "kg"}, "source": "manufacturer"},
        ],
        **extra,
    }
    return json.dumps(body)


async def ask(admin_client, proposal_id: str) -> dict:
    r = await admin_client.post(f"/api/v1/product-proposals/{proposal_id}/look-up")
    assert r.status_code == 200, r.text
    return r.json()


async def post_answer(helper, body: str) -> httpx.Response:
    return await helper.post(
        "/api/v1/lookup-answers",
        content=body,
        headers={**helper.suggest_headers, "Content-Type": "application/json"},
    )


# --- 89 -------------------------------------------------------------------------------------


async def test_the_read_token_reads_only_the_queue_and_suggest_only_answers(admin_client, helper):
    """Criterion 89; the route walk in test_scopes.py covers every other route."""
    proposal_id = await proposal_from_scan(admin_client)
    asked = await ask(admin_client, proposal_id)
    queue = await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)
    assert queue.status_code == 200
    assert [(r["id"], r["kind"], r["value"]) for r in queue.json()["items"]] == [
        (asked["id"], "gtin", "0" + UNKNOWN)
    ]
    assert (
        await helper.get("/api/v1/lookup-requests", headers=helper.suggest_headers)
    ).status_code == 403
    r = await helper.post(
        "/api/v1/lookup-answers", content=answer(asked["id"]), headers=helper.read_headers
    )
    assert r.status_code == 403
    assert (
        await helper.get(f"/api/v1/product-proposals/{proposal_id}", headers=helper.read_headers)
    ).status_code == 403


async def test_look_this_up_is_offered_only_with_a_helper(admin_client):
    assert (await admin_client.get("/api/v1/products-helper")).json() == {"configured": False}
    await token(admin_client, "products:read")
    assert (await admin_client.get("/api/v1/products-helper")).json() == {"configured": True}


# --- 90 -------------------------------------------------------------------------------------


async def test_an_answer_merges_into_its_pending_proposal_with_its_source(admin_client, helper):
    """Criterion 90, first part."""
    proposal_id = await proposal_from_scan(admin_client)
    asked = await ask(admin_client, proposal_id)
    r = await post_answer(helper, answer(asked["id"]))
    assert r.status_code == 200, r.text
    assert r.json() == {"outcome": "merged", "proposal_id": proposal_id}
    body = (await admin_client.get(f"/api/v1/product-proposals/{proposal_id}")).json()
    title = body["fields"]["title"]
    assert (title["value"], title["source"], title["via"]) == (
        "Strong White Flour",
        "manufacturer",
        "helper",
    )
    assert body["fields"]["pack"]["value"] == {"qty": "1.5", "unit": "kg"}  # never a float
    assert body["fields"]["gtin"]["source"] == "scan"  # the household's own scan still ranks first
    assert body["lookup"]["status"] == "answered"


@pytest.mark.parametrize(
    "bad",
    [
        {"field": "colour", "value": "red", "source": "manufacturer"},
        {"field": "title", "value": "Flour", "source": "model", "confidence": 0.9},
        {"field": "title", "value": "Flour", "source": "person"},
    ],
)
async def test_an_unknown_field_or_an_over_cap_confidence_is_refused_and_recorded(
    admin_client, helper, owner_conn: asyncpg.Connection, bad
):
    """Criterion 90, second part."""
    proposal_id = await proposal_from_scan(admin_client)
    asked = await ask(admin_client, proposal_id)
    body = json.dumps({"format": FORMAT, "request_id": asked["id"], "candidates": [bad]})
    r = await post_answer(helper, body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "answer_refused"
    recorded = await owner_conn.fetchrow(
        "SELECT outcome, request_id FROM lookup_answer ORDER BY created_at DESC LIMIT 1"
    )
    assert recorded["outcome"] == "refused" and str(recorded["request_id"]) == asked["id"]
    fields = (await admin_client.get(f"/api/v1/product-proposals/{proposal_id}")).json()["fields"]
    assert "title" not in fields


async def test_a_late_answer_opens_a_product_update_or_is_closed(admin_client, helper, admin):
    """Criterion 90, last part (PR7)."""
    accepted_id = await proposal_from_scan(admin_client)
    asked = await ask(admin_client, accepted_id)
    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Flour"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{accepted_id}/accept",
        json={"action": "new", "ingredient_id": ing["id"], "edits": {"title": "Bread flour"}},
    )
    assert r.status_code == 200, r.text
    product_id = r.json()["result"]["product_id"]
    r = await post_answer(helper, answer(asked["id"]))
    assert r.json()["outcome"] == "update_opened"
    update = (await admin_client.get(f"/api/v1/product-proposals/{r.json()['proposal_id']}")).json()
    assert update["kind"] == "product_update" and update["product_id"] == product_id
    assert update["match"]["preselect"] == f"update:{product_id}"
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert [i["title"] for i in items if i["kind"] == "product_update"] == [
        "1 product update to review"
    ]
    # Accepting the update fills what the product lacked, and keeps the household's name.
    r = await admin_client.post(
        f"/api/v1/product-proposals/{update['id']}/accept", json={"action": "update"}
    )
    assert r.status_code == 200, r.text
    product = (await admin_client.get(f"/api/v1/products/{product_id}")).json()
    assert (product["name"], product["brand"], product["pack_qty"]) == (
        "Bread flour",
        "Larkfield",
        "1.5",
    )

    # An answer to a rejected proposal is recorded and closed.
    rejected_id = await proposal_from_scan(admin_client, ANOTHER)
    asked = await ask(admin_client, rejected_id)
    await admin_client.post(f"/api/v1/product-proposals/{rejected_id}/reject")
    r = await post_answer(helper, answer(asked["id"]))
    assert r.json()["outcome"] == "closed"


# --- 91 -------------------------------------------------------------------------------------


async def test_refreshed_posted_prices_wait_for_a_person(
    admin_client, helper, admin, owner_conn: asyncpg.Connection
):
    """Criterion 91."""
    store = await make_location(
        admin_client, "Juniper Market", "Juniper Market", price_scope="chain"
    )
    oats = await make_product(admin_client, "Oats", "Rolled oats tin")
    async with get_sessionmaker()() as db:
        result = await proposals.create_capture(
            db,
            user=admin,
            channel="clip",
            payload={"page": 1},
            evidence=page_evidence(store, gtin_value=None),
            source_url="https://shop.example.test/p/oat-tin-500",
        )
        await proposals.accept(
            db,
            admin,
            result.proposal.id,
            AcceptInput(action="update", product_id=uuid.UUID(oats["id"])),
        )
    listing_id = await owner_conn.fetchval("SELECT id FROM vendor_listing")
    seen = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    report = json.dumps(
        {
            "format": FORMAT,
            "prices": [
                {"listing_id": str(listing_id), "amount": 3.19, "seen_at": seen},
                {"listing_id": str(listing_id), "amount": 2.99, "is_promo": True, "seen_at": seen},
            ],
        }
    )
    r = await helper.post(
        "/api/v1/listing-price-changes",
        content=report,
        headers={**helper.suggest_headers, "Content-Type": "application/json"},
    )
    assert r.status_code == 200 and r.json() == {"added": 2}
    before = await owner_conn.fetchval("SELECT count(*) FROM price_observation")
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert [i["title"] for i in items if i["kind"] == "posted_prices"] == [
        "2 posted prices changed"
    ]
    changes = (await admin_client.get("/api/v1/listing-price-changes")).json()["items"]
    assert [c["amount"] for c in changes] == ["3.1900", "2.9900"]
    assert await owner_conn.fetchval("SELECT count(*) FROM price_observation") == before

    r = await admin_client.post(
        f"/api/v1/listing-price-changes/{changes[0]['id']}/accept",
        json={"vendor_location_id": store["id"]},
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    r = await admin_client.post(f"/api/v1/listing-price-changes/{changes[1]['id']}/reject")
    assert r.json()["status"] == "rejected"
    rows = await owner_conn.fetch(
        "SELECT price, source FROM price_observation WHERE source = 'listing' ORDER BY created_at"
    )
    assert [(r["price"], r["source"]) for r in rows][-1] == (Decimal("3.1900"), "listing")
    assert await owner_conn.fetchval("SELECT count(*) FROM price_observation") == before + 1
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in items if i["kind"] == "posted_prices"]

    # A weekly refresh re-reports what it sees: only a change reaches a person.
    async def report_again(*prices: dict) -> int:
        body = json.dumps({"format": FORMAT, "prices": list(prices)})
        r = await helper.post(
            "/api/v1/listing-price-changes",
            content=body,
            headers={**helper.suggest_headers, "Content-Type": "application/json"},
        )
        return r.json()["added"]

    later = datetime.now(UTC).isoformat()
    accepted = {"listing_id": str(listing_id), "amount": "3.1900", "seen_at": later}
    rejected = {"listing_id": str(listing_id), "amount": 2.99, "is_promo": True, "seen_at": later}
    assert await report_again(accepted) == 0  # the posted price, unchanged
    assert await report_again({**accepted, "amount": 3.29}) == 1
    assert await report_again({**accepted, "amount": 3.29}) == 0  # already waiting
    assert await report_again(rejected) == 1  # differs from the latest change (3.29)
    assert await report_again(rejected) == 0


# --- 92 -------------------------------------------------------------------------------------


def test_the_contract_schema_is_the_checked_in_one():
    """Criterion 92, first part: the helper repository checks against the same file."""
    assert json.loads(CONTRACT.read_text()) == contract_schema(), (
        "run `kerp export-openapi` and commit docs/api/kitchen-erp-products-1.schema.json"
    )


async def test_a_photo_original_is_readable_only_through_an_open_cutout_request(
    admin_client, helper, owner_conn: asyncpg.Connection
):
    """Criterion 92, last part."""
    product = await make_product(admin_client, "Beans", "Bean tin")
    out = io.BytesIO()
    Image.new("RGB", (300, 300), (40, 90, 160)).save(out, format="JPEG")
    await admin_client.post(
        "/api/v1/product-photos",
        data={"product_id": product["id"]},
        files=[("photos", ("p.jpg", out.getvalue(), "image/jpeg"))],
    )
    await work()
    [request] = (await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)).json()[
        "items"
    ]
    assert request["kind"] == "cutout" and request["value"] is None
    r = await helper.get(
        f"/api/v1/lookup-requests/{request['id']}/original", headers=helper.read_headers
    )
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    # No other way to reach a photo: media needs a full session or token.
    [ph] = (await admin_client.get(f"/api/v1/products/{product['id']}/photos")).json()["items"]
    assert (await helper.get(ph["urls"]["medium"], headers=helper.read_headers)).status_code == 403

    mask = Image.new("L", (300, 300), 0)
    mask.paste(255, (60, 60, 240, 240))
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    r = await helper.post(
        f"/api/v1/lookup-requests/{request['id']}/mask",
        files={"mask": ("m.png", buf.getvalue(), "image/png")},
        headers=helper.suggest_headers,
    )
    assert r.status_code == 200, r.text
    await work()
    [ph] = (await admin_client.get(f"/api/v1/products/{product['id']}/photos")).json()["items"]
    assert ph["has_cutout"] and ph["cutout_source"] == "tool"
    # Answered: the original is no longer handed out.
    r = await helper.get(
        f"/api/v1/lookup-requests/{request['id']}/original", headers=helper.read_headers
    )
    assert r.status_code == 404


async def test_an_answer_with_photos_adds_candidates_with_attribution(admin_client, helper):
    proposal_id = await proposal_from_scan(admin_client)
    asked = await ask(admin_client, proposal_id)
    out = io.BytesIO()
    Image.new("RGB", (200, 200), (200, 120, 40)).save(out, format="JPEG")
    body = answer(
        asked["id"],
        photos=[
            {
                "data_base64": base64.b64encode(out.getvalue()).decode(),
                "source_kind": "open_food_facts",
                "attribution": "Open Food Facts, CC BY-SA",
                "source_url": "https://facts.example.test/p/1",
            }
        ],
    )
    assert (await post_answer(helper, body)).status_code == 200
    photos = (await admin_client.get(f"/api/v1/product-proposals/{proposal_id}")).json()["photos"]
    assert [(p["source_kind"], p["attribution"]) for p in photos] == [
        ("open_food_facts", "Open Food Facts, CC BY-SA")
    ]


async def test_an_overdue_lookup_gets_its_own_reading_line(admin_client, owner_conn):
    proposal_id = await proposal_from_scan(admin_client)
    await ask(admin_client, proposal_id)
    reading = (await admin_client.get("/api/v1/inbox")).json()["reading"]
    assert reading["lookups_overdue"] == 0
    await owner_conn.execute("UPDATE lookup_request SET created_at = now() - interval '2 hours'")
    reading = (await admin_client.get("/api/v1/inbox")).json()["reading"]
    assert reading["lookups_overdue"] == 1 and reading["lookups_since"] is not None


async def test_photographed_products_queue_nothing_by_themselves(admin_client, owner_conn):
    """Only what a person asks about goes to the helper, unless the setting is on."""
    await post_photos(admin_client, photo(barcode=UNKNOWN))
    await work()
    await proposal_from_scan(admin_client)
    assert await owner_conn.fetchval("SELECT count(*) FROM lookup_request WHERE kind = 'gtin'") == 0


async def test_with_the_setting_on_an_unknown_scan_is_queued(admin_client, owner_conn, monkeypatch):
    monkeypatch.setattr(get_settings(), "products_autoqueue_gtins", True)
    await proposal_from_scan(admin_client)
    rows = await owner_conn.fetch("SELECT kind, value, requested_by FROM lookup_request")
    assert [(r["kind"], r["value"], r["requested_by"]) for r in rows] == [
        ("gtin", "0" + UNKNOWN, None)
    ]
