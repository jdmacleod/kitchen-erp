"""The current kitchen (17, 4A criterion 2): a per-user server setting the
client sends on kitchen-scoped calls. Kitchens are invented, on the synthetic grid."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from app.main import app
from tests import geo_helpers as gh
from tests.conftest import ADMIN
from tests.geo_helpers import NEAR_A, NEAR_B, make_home_base
from tests.test_scopes import token

clean_geo = gh.clean_geo


@pytest.fixture
async def second_browser(admin) -> AsyncIterator[httpx.AsyncClient]:
    """The same person signed in elsewhere: a second cookie jar."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/api/v1/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]}
        )
        assert r.status_code == 200, r.text
        yield c


async def test_settings_need_a_session_and_no_token_scope_reaches_them(
    admin_client: httpx.AsyncClient,
):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as nobody:
        assert (await nobody.get("/api/v1/me/settings")).status_code == 401
    headers = await token(admin_client, "products:read")
    admin_client.cookies.clear()
    r = await admin_client.get("/api/v1/me/settings", headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_scope"


async def test_empty_settings_suggest_the_only_kitchen(admin_client: httpx.AsyncClient):
    assert (await admin_client.get("/api/v1/me/settings")).json() == {
        "current_home_base_id": None,
        "suggested_home_base_id": None,
    }
    only = await make_home_base(admin_client, "Harbor kitchen", NEAR_A)
    body = (await admin_client.get("/api/v1/me/settings")).json()
    assert body == {"current_home_base_id": None, "suggested_home_base_id": only["id"]}
    # With two kitchens there is nothing to suggest; the client falls back by name.
    await make_home_base(admin_client, "Ridge kitchen", NEAR_B)
    body = (await admin_client.get("/api/v1/me/settings")).json()
    assert body["suggested_home_base_id"] is None


async def test_put_persists_the_current_kitchen_and_a_second_browser_reads_it(
    admin_client: httpx.AsyncClient, second_browser: httpx.AsyncClient
):
    harbor = await make_home_base(admin_client, "Harbor kitchen", NEAR_A)
    ridge = await make_home_base(admin_client, "Ridge kitchen", NEAR_B)
    r = await admin_client.put("/api/v1/me/settings", json={"current_home_base_id": ridge["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["current_home_base_id"] == ridge["id"]
    assert (await second_browser.get("/api/v1/me/settings")).json()["current_home_base_id"] == (
        ridge["id"]
    )
    # Switching replaces; null clears.
    r = await second_browser.put("/api/v1/me/settings", json={"current_home_base_id": harbor["id"]})
    assert r.json()["current_home_base_id"] == harbor["id"]
    assert (await admin_client.get("/api/v1/me/settings")).json()["current_home_base_id"] == (
        harbor["id"]
    )
    r = await admin_client.put("/api/v1/me/settings", json={"current_home_base_id": None})
    assert r.status_code == 200 and r.json()["current_home_base_id"] is None


async def test_put_refuses_a_kitchen_that_does_not_exist(admin_client: httpx.AsyncClient):
    missing = str(uuid.uuid4())
    r = await admin_client.put("/api/v1/me/settings", json={"current_home_base_id": missing})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "unknown_home_base"
    assert r.json()["error"]["details"] == {"home_base_id": missing}
    assert (await admin_client.put("/api/v1/me/settings", json={})).status_code == 422
    assert (await admin_client.get("/api/v1/me/settings")).json()["current_home_base_id"] is None


async def test_settings_are_per_user(admin_client: httpx.AsyncClient):
    from tests.conftest import MEMBER, make_user

    kitchen = await make_home_base(admin_client, "Harbor kitchen", NEAR_A)
    await admin_client.put("/api/v1/me/settings", json={"current_home_base_id": kitchen["id"]})
    await make_user("member")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as member:
        r = await member.post(
            "/api/v1/auth/login", json={"email": MEMBER["email"], "password": MEMBER["password"]}
        )
        assert r.status_code == 200
        body = (await member.get("/api/v1/me/settings")).json()
    assert body["current_home_base_id"] is None
    assert body["suggested_home_base_id"] == kitchen["id"]
