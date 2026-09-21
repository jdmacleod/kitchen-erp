import httpx


async def test_health_public_is_status_only(client: httpx.AsyncClient):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status"}
    # The model server is unreachable in tests: healthy overall, marked degraded.
    assert body["status"] == "degraded"


async def test_health_details_when_authenticated(admin_client: httpx.AsyncClient):
    r = await admin_client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    checks = body["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["migrations"]["status"] == "ok"
    assert checks["migrations"]["detail"]["current"] == checks["migrations"]["detail"]["expected"]
    assert checks["model_server"]["status"] == "degraded"
    assert checks["ingest_queue"]["status"] == "ok"
    assert checks["ingest_queue"]["detail"]["depth"] == 0
    assert body["status"] == "degraded"
