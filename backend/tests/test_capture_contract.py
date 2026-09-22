"""Phase 2F: the capture API, exercised the way a mobile client would, with a bearer token.

The checked-in OpenAPI document (docs/api/openapi.json) must match the running
application; `kerp export-openapi` regenerates it after an intended change.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.main import app
from tests.pricebook_helpers import SYNTH

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"

CONTRACT_PATHS = {
    ("post", "/api/v1/receipts"),
    ("get", "/api/v1/ingest-jobs/{job_id}"),
    ("post", "/api/v1/purchases"),
    ("post", "/api/v1/price-observations"),
    ("get", "/api/v1/products"),
    ("post", "/api/v1/products"),
    ("get", "/api/v1/vendor-locations"),
    ("post", "/api/v1/vendor-locations"),
}


@pytest.fixture
async def bearer(admin_client: httpx.AsyncClient, client: httpx.AsyncClient) -> dict[str, str]:
    r = await admin_client.post("/api/v1/api-tokens", json={"name": "phone"})
    assert r.status_code == 201
    admin_client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['plaintext']}"}


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def test_openapi_document_matches_the_application():
    assert OPENAPI_PATH.is_file(), "run `kerp export-openapi` and commit docs/api/openapi.json"
    checked_in = json.loads(OPENAPI_PATH.read_text())
    assert checked_in == app.openapi(), (
        "OpenAPI drift: run `kerp export-openapi` and review the diff"
    )
    spec_paths = {(m, p) for p, ops in checked_in["paths"].items() for m in ops}
    missing = {(m, p) for m, p in CONTRACT_PATHS if (m, p) not in spec_paths}
    assert not missing, f"capture contract endpoints missing from the document: {missing}"


async def test_bearer_only_flow_with_retries(client: httpx.AsyncClient, bearer):
    # A revoked token is rejected on its next use; a fresh one works. (Covered in 1A; smoke here.)
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    # Location from coordinates and a name (a roadside stand), retried.
    body = {
        "vendor": {"name": "Roadside Stand", "kind": "stand"},
        "name": "Roadside Stand",
        "lat": SYNTH[0][0],
        "lon": SYNTH[0][1],
    }
    key = _key()
    first = await client.post("/api/v1/vendor-locations", json=body, headers={**bearer, **key})
    again = await client.post("/api/v1/vendor-locations", json=body, headers={**bearer, **key})
    assert first.status_code == 201 and again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    location = first.json()
    listed = await client.get(
        "/api/v1/vendor-locations", params={"near": f"{SYNTH[1][0]},{SYNTH[1][1]}"}, headers=bearer
    )
    assert listed.status_code == 200 and listed.json()["items"][0]["distance_m"] is not None

    # Product with inline ingredient, retried; then found by barcode and by typeahead.
    body = {
        "ingredient": {"name": "Peaches"},
        "name": "Stand peaches",
        "barcode": "036000291452",
    }
    key = _key()
    p1 = await client.post("/api/v1/products", json=body, headers={**bearer, **key})
    p2 = await client.post("/api/v1/products", json=body, headers={**bearer, **key})
    assert p1.status_code == p2.status_code == 201 and p1.json()["id"] == p2.json()["id"]
    product = p1.json()
    by_barcode = await client.get(
        "/api/v1/products", params={"barcode": "036000291452"}, headers=bearer
    )
    assert [x["id"] for x in by_barcode.json()["items"]] == [product["id"]]
    by_text = await client.get("/api/v1/products", params={"q": "peach"}, headers=bearer)
    assert product["id"] in [x["id"] for x in by_text.json()["items"]]

    # Shelf price, retried.
    body = {
        "product_id": product["id"],
        "vendor_location_id": location["id"],
        "price": "3.50",
        "qty": "1",
        "unit": "lb",
    }
    key = _key()
    o1 = await client.post("/api/v1/price-observations", json=body, headers={**bearer, **key})
    o2 = await client.post("/api/v1/price-observations", json=body, headers={**bearer, **key})
    assert o1.status_code == o2.status_code == 201 and o1.json()["id"] == o2.json()["id"]
    assert (
        len((await client.get("/api/v1/price-observations", headers=bearer)).json()["items"]) == 1
    )

    # Complete manual purchase, retried.
    body = {
        "vendor_location_id": location["id"],
        "purchased_at": datetime.now(UTC).isoformat(),
        "lines": [{"product_id": product["id"], "qty": "2", "unit": "lb", "unit_price": "3.50"}],
    }
    key = _key()
    m1 = await client.post("/api/v1/purchases", json=body, headers={**bearer, **key})
    m2 = await client.post("/api/v1/purchases", json=body, headers={**bearer, **key})
    assert m1.status_code == m2.status_code == 201 and m1.json()["id"] == m2.json()["id"]
    assert len((await client.get("/api/v1/purchases", headers=bearer)).json()["items"]) == 1


async def test_revoked_token_is_rejected_by_a_contract_endpoint(admin_client, client):
    r = await admin_client.post("/api/v1/api-tokens", json={"name": "old phone"})
    token = r.json()
    await admin_client.post(f"/api/v1/api-tokens/{token['token']['id']}/revoke")
    admin_client.cookies.clear()
    headers = {"Authorization": f"Bearer {token['plaintext']}"}
    assert (await client.get("/api/v1/vendor-locations", headers=headers)).status_code == 401
