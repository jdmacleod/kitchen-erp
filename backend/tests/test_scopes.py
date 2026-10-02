"""Scoped API tokens: refused by default (spec 03 §1F criterion 68; eng review R1, R6).

A token narrower than "*" reaches only the routes that declare its scope: the
public vendor export (vendors:read) and suggestion intake (vendors:suggest).
Every other route answers 403 insufficient_scope, including routes added later,
because the check lives in the shared auth dependency. Sessions and "*" tokens
are never refused for scope.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from app.core.security import digest
from app.main import app
from tests import geo_helpers as gh
from tests.conftest import run_alembic
from tests.geo_helpers import NEAR_A, make_location

clean_geo = gh.clean_geo

DECLARED = {
    ("GET", "/api/v1/vendors/export"): "vendors:read",
    ("POST", "/api/v1/vendor-suggestions"): "vendors:suggest",
    # The products helper (04, 2N): the queue to read, and answers to post.
    ("GET", "/api/v1/lookup-requests"): "products:read",
    ("GET", "/api/v1/lookup-requests/{request_id}/original"): "products:read",
    ("POST", "/api/v1/lookup-requests/{request_id}/mask"): "products:suggest",
    ("POST", "/api/v1/lookup-answers"): "products:suggest",
    ("POST", "/api/v1/listing-price-changes"): "products:suggest",
}
SCOPED = ("vendors:read", "vendors:suggest", "products:read", "products:suggest")
# Walking these with a session would end the session the rest of the walk uses.
SKIP_FOR_SESSION = {("POST", "/api/v1/auth/logout")}


@pytest.fixture
async def bearer_client() -> AsyncIterator[httpx.AsyncClient]:
    """A client with no cookies, so only the Authorization header speaks."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def token(admin_client: httpx.AsyncClient, *scopes: str) -> dict[str, str]:
    body = {"name": f"tool {uuid.uuid4().hex[:6]}"}
    if scopes:
        body["scopes"] = list(scopes)  # type: ignore[assignment]
    r = await admin_client.post("/api/v1/api-tokens", json=body)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['plaintext']}"}


def routes() -> list[tuple[str, str]]:
    """Every documented route: the OpenAPI document is the set clients can call."""
    return [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1")
        for method in operations
    ]


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)


async def call(client: httpx.AsyncClient, method: str, path: str, headers=None) -> httpx.Response:
    return await client.request(method, concrete(path), headers=headers or {})


async def test_scoped_tokens_reach_only_their_routes(
    admin_client: httpx.AsyncClient, bearer_client: httpx.AsyncClient
):
    tokens = [(await token(admin_client, scope), scope) for scope in SCOPED]
    walked = 0
    for method, path in routes():
        anonymous = await call(bearer_client, method, path)
        if anonymous.status_code != 401:
            continue  # a public route (health, login): no user, so no scope either
        walked += 1
        for headers, scope in tokens:
            r = await call(bearer_client, method, path, headers)
            if DECLARED.get((method, path)) == scope:
                assert r.status_code != 403, (method, path, scope, r.text)
            else:
                assert r.status_code == 403, (method, path, scope, r.status_code)
                assert r.json()["error"]["code"] == "insufficient_scope", (method, path)
    assert walked > 50  # the walk really covered the API


async def test_sessions_and_full_tokens_are_never_refused_for_scope(
    admin_client: httpx.AsyncClient, bearer_client: httpx.AsyncClient
):
    """R6: the default-deny check changes nothing for a session or a "*" token."""
    full = await token(admin_client)
    for method, path in routes():
        if (method, path) in SKIP_FOR_SESSION:
            continue
        for client, headers in ((admin_client, None), (bearer_client, full)):
            r = await call(client, method, path, headers)
            if r.status_code == 403:
                assert r.json()["error"]["code"] != "insufficient_scope", (method, path)


async def test_read_token_gets_the_public_export_only(
    admin_client: httpx.AsyncClient, bearer_client: httpx.AsyncClient
):
    await make_location(
        admin_client,
        "Elm St",
        NEAR_A,
        vendor={"name": "Invented Mart", "kind": "chain"},
        publishable=True,
    )
    read = await token(admin_client, "vendors:read")
    public = await bearer_client.get("/api/v1/vendors/export?mode=public&format=json", headers=read)
    assert public.status_code == 200 and "Invented Mart" in public.text
    household = await bearer_client.get(
        "/api/v1/vendors/export?mode=household&format=json", headers=read
    )
    assert household.status_code == 403
    assert household.json()["error"]["code"] == "insufficient_scope"
    listed = await bearer_client.get("/api/v1/vendors", headers=read)
    assert listed.status_code == 403


async def test_token_scopes_are_listed_and_checked(admin_client: httpx.AsyncClient):
    tool = await admin_client.post(
        "/api/v1/api-tokens",
        json={"name": "enricher", "scopes": ["vendors:suggest", "vendors:read"]},
    )
    assert tool.status_code == 201
    assert tool.json()["token"]["scopes"] == ["vendors:read", "vendors:suggest"]
    plain = await admin_client.post("/api/v1/api-tokens", json={"name": "phone"})
    assert plain.json()["token"]["scopes"] == ["*"]
    mixed = await admin_client.post(
        "/api/v1/api-tokens", json={"name": "x", "scopes": ["*", "vendors:read"]}
    )
    assert mixed.status_code == 422
    unknown = await admin_client.post("/api/v1/api-tokens", json={"name": "x", "scopes": ["admin"]})
    assert unknown.status_code == 422


async def test_a_token_made_before_scopes_keeps_full_access(
    admin_client: httpx.AsyncClient,
    bearer_client: httpx.AsyncClient,
    owner_conn: asyncpg.Connection,
):
    """R6: a token that existed before migration 0012 still reaches everything."""
    user_id = (await admin_client.get("/api/v1/auth/me")).json()["id"]
    plaintext = f"kerp_{uuid.uuid4().hex}"
    run_alembic("downgrade", "0011")
    try:
        await owner_conn.execute(
            "INSERT INTO api_token (id, user_id, name, token_hash, created_at, updated_at) "
            "VALUES ($1, $2, 'old capture app', $3, now(), now())",
            uuid.uuid4(),
            uuid.UUID(user_id),
            digest(plaintext),
        )
    finally:
        run_alembic("upgrade", "head")
    from app.core.db import dispose_engine

    await dispose_engine()
    headers = {"Authorization": f"Bearer {plaintext}"}
    assert (await bearer_client.get("/api/v1/vendors", headers=headers)).status_code == 200
    assert (await bearer_client.get("/api/v1/inbox", headers=headers)).status_code == 200
    created = await bearer_client.post("/api/v1/purchases", json={}, headers=headers)
    assert created.status_code == 422  # reached the route: the body is what's wrong
