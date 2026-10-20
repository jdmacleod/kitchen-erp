"""A page clipped for waiting receipt lines identifies them once accepted (04, 2D)."""

from __future__ import annotations

import uuid

from app.services import resolution
from tests import geo_helpers as gh
from tests import test_page_captures as captures
from tests.catalog_helpers import seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase
from tests.test_page_captures import SHOP, page

no_network = gh.no_network
media_root = captures.media_root

OATS = "HC ROLLED OATS"


async def _committed(client, admin, db_session, loc, lines) -> None:
    pid = await make_receipt_purchase(admin.id, loc["id"], lines)
    await resolution.resolve_purchase(db_session, uuid.UUID(pid))
    r = await client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200, r.text


async def _setup(client, admin, db_session) -> tuple[dict, dict]:
    await seed_units_via_service(db_session)
    store = await make_location(client, "Juniper Market", "Juniper Market", price_scope="chain")
    r = await client.patch(f"/api/v1/vendors/{store['vendor']['id']}", json={"website": SHOP})
    assert r.status_code == 200, r.text
    other = await make_location(client, "Millpond Market", "Millpond Market East")
    await _committed(client, admin, db_session, store, [
        {"raw_text": f"{OATS} 3.49", "line_total": "3.49"},
        {"raw_text": "PURPLE YAMS 2.10", "line_total": "2.10"},
    ])  # fmt: skip
    await _committed(client, admin, db_session, store, [
        {"raw_text": f"{OATS} 3.29", "line_total": "3.29"},
    ])  # fmt: skip
    await _committed(client, admin, db_session, other, [
        {"raw_text": f"{OATS} 3.99", "line_total": "3.99"},
    ])  # fmt: skip
    return store, other


async def _groups(client) -> dict:
    r = await client.get("/api/v1/to-identify")
    assert r.status_code == 200, r.text
    return {(g["vendor"]["name"], g["raw_text_norm"]): g for g in r.json()["items"]}


async def test_accepting_a_clip_identifies_the_lines_it_was_clipped_for_and_no_others(
    admin_client, admin, db_session, no_network
):
    store, _ = await _setup(admin_client, admin, db_session)
    for_lines = {"vendor_id": store["vendor"]["id"], "raw_text_norm": OATS}
    r = await admin_client.post("/api/v1/product-captures", json=page(for_lines=for_lines))
    assert r.status_code == 201, r.text
    proposal = r.json()
    assert proposal["for_lines"] == {**for_lines, "vendor_name": "Juniper Market", "waiting": 2}

    waiting = (await _groups(admin_client))[("Juniper Market", OATS)]["waiting_proposal"]
    assert waiting["id"] == proposal["id"]
    assert (await _groups(admin_client))[("Millpond Market", OATS)]["waiting_proposal"] is None

    oats = await admin_client.post(
        "/api/v1/ingredients", json={"name": "Oats", "category": "pantry"}
    )
    r = await admin_client.post(
        f"/api/v1/product-proposals/{proposal['id']}/accept",
        json={"action": "new", "ingredient_id": oats.json()["id"]},
    )
    assert r.status_code == 200, r.text
    accepted = r.json()
    assert accepted["result"]["identified_lines"] == 2
    assert accepted["for_lines"]["waiting"] == 0

    groups = await _groups(admin_client)
    assert ("Juniper Market", OATS) not in groups
    # The same wording at another store keeps waiting: each store learns its own.
    assert groups[("Millpond Market", OATS)]["line_count"] == 1
    assert ("Juniper Market", "PURPLE YAMS") in groups


async def test_a_clip_for_lines_answered_meanwhile_identifies_nothing(
    admin_client, admin, db_session, no_network
):
    store, _ = await _setup(admin_client, admin, db_session)
    for_lines = {"vendor_id": store["vendor"]["id"], "raw_text_norm": OATS}
    proposal = (
        await admin_client.post("/api/v1/product-captures", json=page(for_lines=for_lines))
    ).json()
    r = await admin_client.post("/api/v1/to-identify/apply", json={**for_lines, "ignore": True})
    assert r.json()["applied"] == 2
    oats = await admin_client.post(
        "/api/v1/ingredients", json={"name": "Oats", "category": "pantry"}
    )
    r = await admin_client.post(
        f"/api/v1/product-proposals/{proposal['id']}/accept",
        json={"action": "new", "ingredient_id": oats.json()["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["identified_lines"] == 0


async def test_a_clip_without_lines_is_unchanged(admin_client, admin, db_session, no_network):
    await _setup(admin_client, admin, db_session)
    proposal = (await admin_client.post("/api/v1/product-captures", json=page())).json()
    assert proposal["for_lines"] is None
    oats = await admin_client.post(
        "/api/v1/ingredients", json={"name": "Oats", "category": "pantry"}
    )
    r = await admin_client.post(
        f"/api/v1/product-proposals/{proposal['id']}/accept",
        json={"action": "new", "ingredient_id": oats.json()["id"]},
    )
    assert "identified_lines" not in (r.json()["result"] or {})
    assert len(await _groups(admin_client)) == 3


async def test_lines_must_name_a_vendor_and_wording(admin_client, no_network):
    bad = {"vendor_id": str(uuid.uuid4()), "raw_text_norm": ""}
    r = await admin_client.post("/api/v1/product-captures", json=page(for_lines=bad))
    assert r.status_code == 422
