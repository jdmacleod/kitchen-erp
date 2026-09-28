"""Managing household members (#75): edit, deactivate, and passwords.

Deactivating a member must cut their access at once, sessions and API tokens
alike, and the household must never lose its last active admin.
"""

import asyncio

import httpx
import pytest

from app.core.db import get_sessionmaker
from app.core.errors import ApiError
from app.main import app
from app.models import AppUser
from app.services import identity
from tests.conftest import ADMIN, MEMBER, make_user


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _login(c: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await c.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_admin_edits_a_members_name_email_and_role(admin_client):
    member = await make_user("member")
    r = await admin_client.patch(
        f"/api/v1/users/{member.id}",
        json={"display_name": "Sam", "email": "Sam@Example.com", "role": "admin"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Sam" and body["role"] == "admin"
    assert body["email"] == "sam@example.com"


async def test_email_already_taken_is_409(admin_client):
    member = await make_user("member")
    r = await admin_client.patch(f"/api/v1/users/{member.id}", json={"email": ADMIN["email"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


async def test_deactivating_ends_sessions_and_tokens_at_once(admin_client):
    member = await make_user("member")
    async with _client() as m:
        assert (await _login(m, MEMBER["email"], MEMBER["password"])).status_code == 200
        r = await m.post("/api/v1/api-tokens", json={"name": "phone"})
        assert r.status_code == 201, r.text
        token = r.json()["plaintext"]

        r = await admin_client.patch(f"/api/v1/users/{member.id}", json={"active": False})
        assert r.status_code == 200 and r.json()["active"] is False

        assert (await m.get("/api/v1/auth/me")).status_code == 401
    async with _client() as t:
        headers = {"Authorization": f"Bearer {token}"}
        assert (await t.get("/api/v1/auth/me", headers=headers)).status_code == 401
        assert (await _login(t, MEMBER["email"], MEMBER["password"])).status_code == 401

    # Reactivated, they sign in again; the revoked token stays revoked.
    r = await admin_client.patch(f"/api/v1/users/{member.id}", json={"active": True})
    assert r.status_code == 200
    async with _client() as m2:
        assert (await _login(m2, MEMBER["email"], MEMBER["password"])).status_code == 200
    async with _client() as t2:
        headers = {"Authorization": f"Bearer {token}"}
        assert (await t2.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_an_admin_cannot_lock_themselves_out(admin_client, admin):
    for change in ({"active": False}, {"role": "member"}):
        r = await admin_client.patch(f"/api/v1/users/{admin.id}", json=change)
        assert r.status_code == 409 and r.json()["error"]["code"] == "self_lockout", change


async def test_the_only_active_admin_cannot_be_removed(admin_client, admin):
    other = await make_user("admin", email="second@example.com")
    # Two admins: the other can be demoted.
    r = await admin_client.patch(f"/api/v1/users/{other.id}", json={"role": "member"})
    assert r.status_code == 200
    # Promote back and deactivate the signed-in admin from the other's session.
    await admin_client.patch(f"/api/v1/users/{other.id}", json={"role": "admin"})
    async with _client() as o:
        assert (await _login(o, "second@example.com", ADMIN["password"])).status_code == 200
        r = await o.patch(f"/api/v1/users/{admin.id}", json={"active": False})
        assert r.status_code == 200
        # Now the other is the only active admin, and nobody can remove it.
        r = await o.patch(f"/api/v1/users/{other.id}", json={"active": False})
        assert r.status_code == 409 and r.json()["error"]["code"] == "self_lockout"


async def test_members_cannot_manage_users(client):
    await make_user("admin")
    member = await make_user("member")
    assert (await _login(client, MEMBER["email"], MEMBER["password"])).status_code == 200
    r = await client.patch(f"/api/v1/users/{member.id}", json={"role": "admin"})
    assert r.status_code == 403


async def test_admin_sets_a_password_and_old_sessions_end(admin_client, admin):
    member = await make_user("member")
    async with _client() as m:
        assert (await _login(m, MEMBER["email"], MEMBER["password"])).status_code == 200
        r = await admin_client.post(
            f"/api/v1/users/{member.id}/password", json={"password": "a-new-one-to-share"}
        )
        assert r.status_code == 200, r.text
        assert (await m.get("/api/v1/auth/me")).status_code == 401
    async with _client() as m2:
        assert (await _login(m2, MEMBER["email"], MEMBER["password"])).status_code == 401
        assert (await _login(m2, MEMBER["email"], "a-new-one-to-share")).status_code == 200
    # Your own password goes through the change-password route instead.
    r = await admin_client.post(f"/api/v1/users/{admin.id}/password", json={"password": "x" * 12})
    assert r.status_code == 409 and r.json()["error"]["code"] == "use_change_password"


async def test_changing_your_own_password_keeps_this_session_only(admin_client):
    async with _client() as elsewhere:
        assert (await _login(elsewhere, ADMIN["email"], ADMIN["password"])).status_code == 200
        r = await admin_client.post(
            "/api/v1/auth/password",
            json={"current_password": "not-it", "new_password": "a-better-secret"},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "wrong_password"
        r = await admin_client.post(
            "/api/v1/auth/password",
            json={"current_password": ADMIN["password"], "new_password": "a-better-secret"},
        )
        assert r.status_code == 204, r.text
        assert (await admin_client.get("/api/v1/auth/me")).status_code == 200
        assert (await elsewhere.get("/api/v1/auth/me")).status_code == 401
    async with _client() as fresh:
        assert (await _login(fresh, ADMIN["email"], ADMIN["password"])).status_code == 401
        assert (await _login(fresh, ADMIN["email"], "a-better-secret")).status_code == 200


async def test_a_short_new_password_is_rejected(admin_client):
    r = await admin_client.post(
        "/api/v1/auth/password",
        json={"current_password": ADMIN["password"], "new_password": "short"},
    )
    assert r.status_code == 422


async def test_a_blank_display_name_is_rejected(admin_client):
    member = await make_user("member")
    r = await admin_client.patch(f"/api/v1/users/{member.id}", json={"display_name": "   "})
    assert r.status_code == 422
    r = await admin_client.post(
        "/api/v1/users",
        json={"email": "blank@example.com", "display_name": "  ", "password": "long-enough-pw"},
    )
    assert r.status_code == 422


async def test_two_admins_removing_each_other_at_once_leave_one(admin):
    """Review of #80: both used to count two admins, both pass, and leave none.

    The first edit holds the admin rows; the second waits for it to commit,
    counts again, and is refused.
    """
    other = await make_user("admin", email="second@example.com")
    maker = get_sessionmaker()
    async with maker() as first, maker() as second:
        # First: A deactivates B, holding the lock, not yet committed.
        assert await identity._lock_active_admins(first) == 2
        b = await first.get(AppUser, other.id)
        b.active = False
        await first.flush()

        # Second: B deactivates A. It must wait for the first to finish.
        actor_b = await second.get(AppUser, other.id)
        racing = asyncio.create_task(
            identity.edit_user(second, actor_b, admin.id, {"active": False})
        )
        await asyncio.sleep(0.3)
        assert not racing.done(), "the second edit should wait on the admin rows"
        await first.commit()

        with pytest.raises(ApiError) as refused:
            await racing
        assert refused.value.code == "last_admin"

    async with maker() as check:
        assert await identity._lock_active_admins(check) == 1
