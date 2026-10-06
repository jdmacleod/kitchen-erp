"""An address whose id is not an id is not found, not a validation error (2026-10-05).

A purchase page opened at /shop/purchases/receipts said "Request failed validation."
Found by /devex-review on 2026-10-05.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "path", ["/api/v1/purchases/receipts", "/api/v1/products/12", "/api/v1/vendors/not-an-id"]
)
async def test_a_malformed_id_is_not_found(admin_client, path):
    r = await admin_client.get(path)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_a_bad_query_is_still_a_validation_error(admin_client):
    r = await admin_client.get("/api/v1/products", params={"limit": "lots"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
