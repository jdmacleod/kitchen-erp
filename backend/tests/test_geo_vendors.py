"""Vendors: kind, price scope (criterion 28), case-insensitive names, search, deactivation."""

import asyncpg
import httpx

from tests import geo_helpers as gh
from tests.geo_helpers import NEAR_A, make_location, make_vendor

clean_geo = gh.clean_geo


async def test_create_get_patch(admin_client: httpx.AsyncClient):
    created = await make_vendor(admin_client, "Harbour Greens", website="https://example.com")
    assert created["kind"] == "independent"
    assert created["price_scope"] == "location"
    assert created["active"] is True
    assert set(created) == {
        "id",
        "name",
        "kind",
        "price_scope",
        "website",
        "notes",
        "active",
        "created_at",
    }
    patched = await admin_client.patch(
        f"/api/v1/vendors/{created['id']}", json={"kind": "market", "notes": "Saturdays"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["kind"] == "market" and patched.json()["notes"] == "Saturdays"


async def test_name_unique_case_insensitively(admin_client: httpx.AsyncClient):
    await make_vendor(admin_client, "Tidepool Market")
    dup = await admin_client.post(
        "/api/v1/vendors", json={"name": "tidepool MARKET", "kind": "chain"}
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "vendor_name_taken"


async def test_price_scope_chain_is_reflected_everywhere(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection
):
    """Criterion 28: Phase 2's offer_latest view will read vendor.price_scope = 'chain'."""
    vendor = await make_vendor(admin_client, "Kelp & Co", kind="chain")
    assert vendor["price_scope"] == "location"  # the default is per-location pricing
    patched = await admin_client.patch(
        f"/api/v1/vendors/{vendor['id']}", json={"price_scope": "chain"}
    )
    assert patched.status_code == 200 and patched.json()["price_scope"] == "chain"
    assert (await admin_client.get(f"/api/v1/vendors/{vendor['id']}")).json()[
        "price_scope"
    ] == "chain"

    location = await make_location(admin_client, "Kelp & Co Pier", NEAR_A, vendor_id=vendor["id"])
    assert location["vendor"] == {
        "id": vendor["id"],
        "name": "Kelp & Co",
        "kind": "chain",
        "price_scope": "chain",
    }
    stored = await owner_conn.fetchval(
        "SELECT price_scope FROM vendor WHERE id = $1::uuid", vendor["id"]
    )
    assert stored == "chain"
    # Only the two documented values are storable; the view can rely on that.
    try:
        await owner_conn.execute(
            "UPDATE vendor SET price_scope = 'regional' WHERE id = $1::uuid", vendor["id"]
        )
    except asyncpg.CheckViolationError as exc:
        assert "ck_vendor_price_scope" in str(exc)
    else:
        raise AssertionError("CHECK constraint on price_scope missing")


async def test_search_and_inactive_filter(admin_client: httpx.AsyncClient):
    greens = await make_vendor(admin_client, "Harbour Greens")
    await make_vendor(admin_client, "Driftwood Bakery", kind="independent")
    await make_vendor(admin_client, "Seagrass Stand", kind="stand")

    found = await admin_client.get("/api/v1/vendors", params={"q": "HARBOUR"})
    assert [v["name"] for v in found.json()["items"]] == ["Harbour Greens"]
    fuzzy = await admin_client.get("/api/v1/vendors", params={"q": "driftwod bakery"})
    assert [v["name"] for v in fuzzy.json()["items"]] == ["Driftwood Bakery"]

    off = await admin_client.post(f"/api/v1/vendors/{greens['id']}/deactivate")
    assert off.status_code == 200 and off.json()["active"] is False
    default = await admin_client.get("/api/v1/vendors")
    assert [v["name"] for v in default.json()["items"]] == ["Driftwood Bakery", "Seagrass Stand"]
    everything = await admin_client.get("/api/v1/vendors", params={"include_inactive": "true"})
    assert len(everything.json()["items"]) == 3
    # Still resolvable by identifier while inactive.
    assert (await admin_client.get(f"/api/v1/vendors/{greens['id']}")).status_code == 200

    on = await admin_client.post(f"/api/v1/vendors/{greens['id']}/activate")
    assert on.json()["active"] is True


async def test_invalid_kind_is_422(admin_client: httpx.AsyncClient):
    bad = await admin_client.post("/api/v1/vendors", json={"name": "x", "kind": "franchise"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"
