import httpx


async def test_same_key_same_body_replays(admin_client: httpx.AsyncClient):
    headers = {"Idempotency-Key": "abc-123"}
    first = await admin_client.post("/api/v1/api-tokens", json={"name": "phone"}, headers=headers)
    second = await admin_client.post("/api/v1/api-tokens", json={"name": "phone"}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers.get("Idempotent-Replayed") == "true"
    listed = await admin_client.get("/api/v1/api-tokens")
    assert len(listed.json()["items"]) == 1


async def test_same_key_different_body_is_422(admin_client: httpx.AsyncClient):
    headers = {"Idempotency-Key": "abc-456"}
    first = await admin_client.post("/api/v1/api-tokens", json={"name": "phone"}, headers=headers)
    assert first.status_code == 201
    second = await admin_client.post("/api/v1/api-tokens", json={"name": "tablet"}, headers=headers)
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "idempotency_key_reused"


async def test_no_key_creates_each_time(admin_client: httpx.AsyncClient):
    for _ in range(2):
        r = await admin_client.post("/api/v1/api-tokens", json={"name": "phone"})
        assert r.status_code == 201
    listed = await admin_client.get("/api/v1/api-tokens")
    assert len(listed.json()["items"]) == 2
