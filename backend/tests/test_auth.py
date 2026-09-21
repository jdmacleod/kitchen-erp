import httpx

from tests.conftest import ADMIN


async def _me_with(client: httpx.AsyncClient, secret: str) -> int:
    client.cookies.clear()
    client.cookies.set("kerp_session", secret)
    return (await client.get("/api/v1/auth/me")).status_code


async def test_login_sets_cookie_and_me_works(client: httpx.AsyncClient, admin):
    r = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]}
    )
    assert r.status_code == 200
    assert r.json()["user"]["email"] == ADMIN["email"]
    assert "kerp_session" in r.cookies
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


async def test_bad_password_is_401_with_envelope(client: httpx.AsyncClient, admin):
    r = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN["email"], "password": "nope-nope-nope"}
    )
    assert r.status_code == 401
    assert r.json() == {
        "error": {"code": "invalid_credentials", "message": "Email or password is incorrect."}
    }


async def test_unauthenticated_me(client: httpx.AsyncClient):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


async def test_logout_revokes_session(admin_client: httpx.AsyncClient):
    cookie = admin_client.cookies["kerp_session"]
    r = await admin_client.post("/api/v1/auth/logout")
    assert r.status_code == 204
    admin_client.cookies.set("kerp_session", cookie)
    r = await admin_client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_logout_everywhere(client: httpx.AsyncClient, admin):
    creds = {"email": ADMIN["email"], "password": ADMIN["password"]}
    first = (await client.post("/api/v1/auth/login", json=creds)).cookies["kerp_session"]
    client.cookies.clear()
    second = (await client.post("/api/v1/auth/login", json=creds)).cookies["kerp_session"]
    client.cookies.clear()
    assert await _me_with(client, first) == 200
    client.cookies.clear()
    client.cookies.set("kerp_session", second)
    r = await client.post("/api/v1/auth/logout-all")
    assert r.status_code == 204
    assert await _me_with(client, first) == 401
    assert (
        await client.get("/api/v1/auth/me", cookies={"kerp_session": second})
    ).status_code == 401
