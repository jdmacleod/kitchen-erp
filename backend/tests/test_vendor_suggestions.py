"""Vendor suggestions from an outside tool (1F, criteria 69-71; eng review R10).

Every vendor, street and number is invented; phones use the 555-01xx range and
coordinates the synthetic grid from SECURITY.md.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from app.main import app
from tests import geo_helpers as gh
from tests.conftest import _dsn
from tests.geo_helpers import NEAR_A, make_location

clean_geo = gh.clean_geo
SITE = "https://inventedmart.example/stores/elm"


@pytest.fixture
async def tool_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def elm(admin_client: httpx.AsyncClient) -> dict[str, Any]:
    return await make_location(
        admin_client, "Elm St", NEAR_A, vendor={"name": "Invented Mart", "kind": "chain"}
    )


@pytest.fixture
async def suggest_token(admin_client: httpx.AsyncClient) -> dict[str, str]:
    r = await admin_client.post(
        "/api/v1/api-tokens",
        json={"name": "enricher", "scopes": ["vendors:read", "vendors:suggest"]},
    )
    return {"Authorization": f"Bearer {r.json()['plaintext']}"}


def item(key: str, field: str, proposed: Any, *, target: str = "location", **extra: Any) -> dict:
    return {
        "target": target,
        "key": key,
        "field": field,
        "proposed": proposed,
        "source_url": SITE,
        **extra,
    }


async def post(client: httpx.AsyncClient, headers: dict, *items: dict, raw: str | None = None):
    body = (
        raw
        if raw is not None
        else json.dumps({"tool": "enrich-tool", "tool_version": "0.3", "items": list(items)})
    )
    return await client.post(
        "/api/v1/vendor-suggestions",
        content=body,
        headers={**headers, "Content-Type": "application/json"},
    )


async def pending(owner: asyncpg.Connection) -> int:
    return await owner.fetchval("SELECT count(*) FROM vendor_suggestion")


async def test_a_malformed_batch_stores_nothing(
    tool_client, suggest_token, elm, owner_conn: asyncpg.Connection
):
    """Criterion 69."""
    for bad in [
        item("invented-mart/nowhere", "phone", "+1 555 0100"),
        item(elm["key"], "price_scope", "chain"),  # a vendor field on a location
        item(elm["key"], "phone", "call us"),
        item(elm["key"], "opening_hours", "whenever"),
        item(elm["key"], "osm", {"type": "node", "id": "one"}),
        item(elm["key"], "phone", "+1 555 0100", source_url="javascript:alert(1)"),
        item(elm["key"], "notes", "hi"),
    ]:
        r = await post(tool_client, suggest_token, item(elm["key"], "phone", "+1 555 0100"), bad)
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["error"]["code"] == "invalid_suggestions"
    floaty = await post(
        tool_client,
        suggest_token,
        raw='{"tool": "t", "tool_version": "1", "items": [{"target": "location", "key": "'
        + elm["key"]
        + '", "field": "phone", "proposed": "+1 555 0100", "source_url": "https://a.example",'
        ' "confidence": 0.9}]}',
    )
    assert floaty.status_code == 422
    assert await pending(owner_conn) == 0


async def test_posting_changes_no_vendor_and_collapses_repeats(
    admin_client, tool_client, suggest_token, elm
):
    """Criterion 69: a valid batch is stored and nothing else changes."""
    batch = [
        item(elm["key"], "phone", "+1 555 0100", evidence="<b>Listed</b> on the store page"),
        item(elm["key"], "phone", "+1 555 0100"),  # a repeat in the same batch
        item(elm["key"], "name", "Elm St"),  # already true
        item(
            "invented-mart",
            "website",
            "https://inventedmart.example",
            target="vendor",
            confidence="0.8",
        ),
    ]
    r = await post(tool_client, suggest_token, *batch)
    assert r.status_code == 201, r.text
    assert (r.json()["stored"], r.json()["collapsed"]) == (2, 2)
    again = await post(tool_client, suggest_token, batch[0])
    assert (again.json()["stored"], again.json()["collapsed"]) == (0, 1)
    location = (await admin_client.get(f"/api/v1/vendor-locations/{elm['id']}")).json()
    assert location["phone"] is None
    listed = (await admin_client.get("/api/v1/vendor-suggestions")).json()["items"]
    assert [(s["field"], s["stale"]) for s in listed] == [("website", False), ("phone", False)]
    phone = listed[1]
    assert phone["evidence"] == "<b>Listed</b> on the store page"  # stored as plain text
    assert phone["source_domain"] == "inventedmart.example"
    assert (phone["location_name"], phone["vendor_name"]) == ("Elm St", "Invented Mart")


async def test_accept_writes_the_value_and_its_source(
    admin_client, tool_client, suggest_token, elm
):
    """Criteria 70 and 71."""
    await post(
        tool_client,
        suggest_token,
        item(elm["key"], "phone", "+1 555 0100"),
        item(elm["key"], "osm", {"type": "node", "id": 301}),
    )
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    row = next(i for i in inbox if i["kind"] == "vendor_suggestions")
    assert row["title"] == "2 vendor suggestions to review"
    assert row["action_route"] == "/catalog/vendors?suggestions=1"
    assert "enrich-tool" in row["detail"]

    listed = (await admin_client.get("/api/v1/vendor-suggestions")).json()["items"]
    for s in listed:
        r = await admin_client.post(f"/api/v1/vendor-suggestions/{s['id']}/accept")
        assert r.status_code == 200 and r.json()["outcome"] == "accepted", r.text
    location = (await admin_client.get(f"/api/v1/vendor-locations/{elm['id']}")).json()
    assert location["phone"] == "+1 555 0100"
    assert (location["osm_type"], location["osm_id"]) == ("node", 301)
    assert location["sources"]["phone"] == {
        "source": "enriched:enrich-tool",
        "ref": SITE,
        "checked_at": location["sources"]["phone"]["checked_at"],
    }
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in inbox if i["kind"] == "vendor_suggestions"]
    twice = await admin_client.post(f"/api/v1/vendor-suggestions/{listed[0]['id']}/accept")
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "already_decided"


async def test_a_field_changed_since_proposed_goes_stale(
    admin_client, tool_client, suggest_token, elm
):
    """Criterion 70: accepting a changed field marks it stale; an override applies it."""
    await post(tool_client, suggest_token, item(elm["key"], "phone", "+1 555 0100"))
    await admin_client.patch(f"/api/v1/vendor-locations/{elm['id']}", json={"phone": "555-0142"})
    [s] = (await admin_client.get("/api/v1/vendor-suggestions")).json()["items"]
    assert s["stale"] is True and s["current"] == "555-0142" and s["expected"] is None

    r = await admin_client.post(f"/api/v1/vendor-suggestions/{s['id']}/accept")
    assert r.json()["outcome"] == "stale" and r.json()["suggestion"]["status"] == "stale"
    location = (await admin_client.get(f"/api/v1/vendor-locations/{elm['id']}")).json()
    assert location["phone"] == "555-0142"

    r = await admin_client.post(
        f"/api/v1/vendor-suggestions/{s['id']}/accept", json={"override": True}
    )
    assert r.json()["outcome"] == "accepted"
    location = (await admin_client.get(f"/api/v1/vendor-locations/{elm['id']}")).json()
    assert location["phone"] == "+1 555 0100"


async def test_reject_and_accept_all_skip_stale(admin_client, tool_client, suggest_token, elm):
    await post(
        tool_client,
        suggest_token,
        item(elm["key"], "phone", "+1 555 0100"),
        item(elm["key"], "address", "4 Pier Lane"),  # pii-scan: allow invented street
        item("invented-mart", "wikidata", "Q123", target="vendor"),
        item("invented-mart", "brand", "Invented Mart", target="vendor"),
    )
    listed = (await admin_client.get("/api/v1/vendor-suggestions")).json()["items"]
    brand = next(s for s in listed if s["field"] == "brand")
    r = await admin_client.post(f"/api/v1/vendor-suggestions/{brand['id']}/reject")
    assert r.json()["outcome"] == "rejected"
    await admin_client.patch(f"/api/v1/vendor-locations/{elm['id']}", json={"phone": "555-0142"})
    r = await admin_client.post(
        f"/api/v1/vendor-suggestions/vendors/{elm['vendor']['id']}/accept-all"
    )
    assert r.json() == {"accepted": 2, "skipped_stale": 1}
    summary = (await admin_client.get("/api/v1/vendor-suggestions/summary")).json()
    assert summary["count"] == 1 and summary["tools"] == ["enrich-tool"]
    assert summary["vendors"][0]["name"] == "Invented Mart"


async def test_limits(tool_client, suggest_token, elm, owner_conn: asyncpg.Connection):
    """R10: 200 items and 2,000 pending at most; over either, nothing is written."""
    many = [item(elm["key"], "address", f"{n} Pier Lane") for n in range(201)]  # pii-scan: allow
    r = await post(tool_client, suggest_token, *many)
    assert r.status_code == 413 and r.json()["error"]["code"] == "batch_too_large"
    ok = await post(tool_client, suggest_token, *many[:200])
    assert ok.status_code == 201 and ok.json()["stored"] == 200

    location_id = uuid.UUID(elm["id"])
    await owner_conn.executemany(
        "INSERT INTO vendor_suggestion (id, batch_id, target, vendor_location_id, field, "
        "proposed_value, source_url, tool, tool_version) VALUES ($1, $2, 'location', $3, "
        "'address', $4::jsonb, 'https://a.example', 't', '1')",
        [(uuid.uuid4(), uuid.uuid4(), location_id, json.dumps(f"filler {n}")) for n in range(1800)],
    )
    before = await pending(owner_conn)
    full = await post(tool_client, suggest_token, item(elm["key"], "phone", "+1 555 0100"))
    assert full.status_code == 409 and full.json()["error"]["code"] == "too_many_pending"
    assert full.json()["error"]["details"]["pending"] == 2000
    assert await pending(owner_conn) == before


async def test_proposals_cannot_be_edited_or_deleted(
    tool_client, suggest_token, elm, owner_conn: asyncpg.Connection
):
    """Criterion 69: the trigger stops even the owner; the runtime role lacks the privilege."""
    await post(tool_client, suggest_token, item(elm["key"], "phone", "+1 555 0100"))
    sid = await owner_conn.fetchval("SELECT id FROM vendor_suggestion")
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="only status"):
        await owner_conn.execute(
            "UPDATE vendor_suggestion SET proposed_value = '\"+1 555 0199\"' WHERE id = $1", sid
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="never deleted"):
        await owner_conn.execute("DELETE FROM vendor_suggestion WHERE id = $1", sid)
    runtime = await asyncpg.connect(_dsn(os.environ["DATABASE_URL"]))
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(
                "UPDATE vendor_suggestion SET source_url = 'https://b.example' WHERE id = $1", sid
            )
        # The decision columns are the runtime role's to change.
        await runtime.execute(
            "UPDATE vendor_suggestion SET status = 'rejected', decided_at = now() WHERE id = $1",
            sid,
        )
    finally:
        await runtime.close()


async def test_a_suggest_token_cannot_read_or_change_vendors(tool_client, admin_client, elm):
    """Criterion 68, the suggest half."""
    r = await admin_client.post(
        "/api/v1/api-tokens", json={"name": "suggest only", "scopes": ["vendors:suggest"]}
    )
    headers = {"Authorization": f"Bearer {r.json()['plaintext']}"}
    for method, path, body in [
        ("GET", "/api/v1/vendors", None),
        ("GET", "/api/v1/vendors/export", None),
        ("PATCH", f"/api/v1/vendor-locations/{elm['id']}", {"phone": "+1 555 0100"}),
        ("GET", "/api/v1/vendor-suggestions", None),
    ]:
        r = await tool_client.request(method, path, json=body, headers=headers)
        assert r.status_code == 403, (method, path)
    posted = await post(tool_client, headers, item(elm["key"], "phone", "+1 555 0100"))
    assert posted.status_code == 201
