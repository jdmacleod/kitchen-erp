import uuid

import httpx

from tests.conftest import MEMBER


async def test_admin_creates_member_and_member_cannot_manage_users(
    admin_client: httpx.AsyncClient, client
):
    r = await admin_client.post("/api/v1/users", json={**MEMBER})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "member"
    dup = await admin_client.post("/api/v1/users", json={**MEMBER})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "email_taken"

    admin_client.cookies.clear()
    r = await client.post(
        "/api/v1/auth/login", json={"email": MEMBER["email"], "password": MEMBER["password"]}
    )
    assert r.status_code == 200
    r = await client.get("/api/v1/users")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


async def test_token_lifecycle(admin_client: httpx.AsyncClient, client: httpx.AsyncClient):
    r = await admin_client.post("/api/v1/api-tokens", json={"name": "phone"})
    assert r.status_code == 201, r.text
    created = r.json()
    plaintext = created["plaintext"]
    assert plaintext.startswith("kerp_")
    token_id = created["token"]["id"]

    listed = await admin_client.get("/api/v1/api-tokens")
    assert listed.status_code == 200
    assert [t["id"] for t in listed.json()["items"]] == [token_id]
    assert "plaintext" not in listed.json()["items"][0]  # shown exactly once

    admin_client.cookies.clear()
    bearer = {"Authorization": f"Bearer {plaintext}"}
    me = await client.get("/api/v1/auth/me", headers=bearer)
    assert me.status_code == 200

    revoke = await client.post(f"/api/v1/api-tokens/{token_id}/revoke", headers=bearer)
    assert revoke.status_code == 200
    assert revoke.json()["revoked_at"] is not None

    after = await client.get("/api/v1/auth/me", headers=bearer)
    assert after.status_code == 401


async def test_revoking_someone_elses_token_is_404(admin_client: httpx.AsyncClient):
    r = await admin_client.post(f"/api/v1/api-tokens/{uuid.uuid4()}/revoke")
    assert r.status_code == 404
