"""Kitchen supply lines (04, 2T; issue 262): spend only, learned like an ignore.

Every store, wording and amount here is invented.
"""

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.services import resolution
from app.services.resolution import set_ranker
from tests.pricebook_helpers import make_location, make_product
from tests.resolution_helpers import make_receipt_purchase


@pytest.fixture(autouse=True)
def _no_ranker():
    set_ranker(None)
    yield
    set_ranker(None)


async def resolve(client: httpx.AsyncClient, purchase_id: str, db_session) -> dict:
    await resolution.resolve_purchase(db_session, uuid.UUID(purchase_id))
    return (await client.get(f"/api/v1/purchases/{purchase_id}")).json()


async def decide(client: httpx.AsyncClient, purchase_id: str, line_id: str, body: dict):
    return await client.post(f"/api/v1/purchases/{purchase_id}/lines/{line_id}/resolve", json=body)


async def alias_rows(owner_conn) -> list[dict]:
    rows = await owner_conn.fetch(
        "SELECT raw_text_norm, disposition, product_id, supply_category, confirmed_count "
        "FROM receipt_alias ORDER BY raw_text_norm"
    )
    return [dict(r) for r in rows]


FOIL = "BRIGHTWRAP FOIL 75SF 4.29"


async def test_categories_are_a_short_fixed_list(admin_client):
    r = await admin_client.get("/api/v1/supply-categories")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["key"] for i in items] == ["wraps", "storage", "cooking", "filters", "fuel"]
    assert items[0]["label"] == "Wraps and papers" and "parchment" in items[0]["examples"]


async def test_categories_need_a_session(client):
    assert (await client.get("/api/v1/supply-categories")).status_code == 401


async def test_a_supply_keeps_its_amount_emits_no_price_and_is_learned(
    admin_client, admin, db_session, owner_conn
):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    p1 = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [
            {"raw_text": FOIL, "line_total": "4.29"},
            {"raw_text": "FOIL SAVINGS -0.50", "line_total": "-0.50", "line_kind": "discount"},
            {"raw_text": "CLOUDBERRY JAM 3.10", "line_total": "3.10"},
        ],
    )
    body = await resolve(admin_client, p1, db_session)
    foil, saving, _jam = body["lines"]
    await admin_client.patch(
        f"/api/v1/purchases/{p1}/lines/{saving['id']}", json={"parent_line_id": foil["id"]}
    )
    r = await decide(admin_client, p1, foil["id"], {"supply_category": "wraps"})
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["resolution"] == "supply" and line["supply_category"] == "wraps"
    assert line["product"] is None and line["resolved_by"] == str(admin.id)
    assert r.json()["supply_total"] == "3.7900"
    assert await alias_rows(owner_conn) == [
        {
            "raw_text_norm": "BRIGHTWRAP FOIL 75SF",
            "disposition": "supply",
            "product_id": None,
            "supply_category": "wraps",
            "confirmed_count": 1,
        }
    ]

    committed = (await admin_client.post(f"/api/v1/purchases/{p1}/commit")).json()
    # Part of the purchase and its total; no price; the jam alone waits in To identify.
    assert committed["computed_total"] == "6.8900"
    assert committed["lines"][0]["observation_id"] is None
    assert (await admin_client.get("/api/v1/price-observations")).json()["items"] == []
    queue = (await admin_client.get("/api/v1/to-identify")).json()["items"]
    assert [g["raw_text_norm"] for g in queue] == ["CLOUDBERRY JAM"]

    # The same wording at the same vendor resolves without a person.
    p2 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": FOIL, "line_total": "4.29"}]
    )
    again = (await resolve(admin_client, p2, db_session))["lines"][0]
    assert again["resolution"] == "supply" and again["supply_category"] == "wraps"
    assert again["resolved_by"] is None and again["suggestions"] == []


async def test_a_purchase_without_supplies_has_no_supply_total(admin_client, admin, db_session):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    p = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "CLOUDBERRY JAM 3.10", "line_total": "3.10"}]
    )
    assert (await resolve(admin_client, p, db_session))["supply_total"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"supply_category": "cleaning"},
        {"supply_category": "wraps", "ignore": True},
        {"supply_category": "wraps", "product_id": str(uuid.uuid4())},
    ],
)
async def test_a_decision_is_one_supply_category_and_nothing_else(
    admin_client, admin, db_session, body
):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    p = await make_receipt_purchase(admin.id, loc["id"], [{"raw_text": FOIL, "line_total": "4.29"}])
    line = (await resolve(admin_client, p, db_session))["lines"][0]
    assert (await decide(admin_client, p, line["id"], body)).status_code == 422


async def test_an_unconfirmed_supply_alias_only_suggests(
    admin_client, admin, db_session, owner_conn
):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    await owner_conn.execute(
        "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, supply_category, "
        "confirmed_count, last_seen_at) VALUES ($1, $2, $3, 'supply', 'filters', 0, now())",
        uuid.uuid4(),
        uuid.UUID(loc["vendor"]["id"]),
        "CONE FILTERS 100CT",
    )
    p = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "CONE FILTERS 100CT 2.75", "line_total": "2.75"}]
    )
    line = (await resolve(admin_client, p, db_session))["lines"][0]
    assert line["resolution"] == "unmatched" and line["supply_category"] is None
    [hint] = line["suggestions"]
    assert hint["kind"] == "alias_unconfirmed" and hint["supply_category"] == "filters"
    assert hint["product_id"] is None and hint["ignore"] is False
    # Accepting the hint is the person's confirmation.
    r = await decide(
        admin_client, p, line["id"], {"supply_category": "filters", "accepted_kind": "alias"}
    )
    assert r.json()["lines"][0]["resolution"] == "supply"
    assert (await alias_rows(owner_conn))[0]["confirmed_count"] == 1


async def test_a_fuzzy_supply_alias_is_a_suggestion(admin_client, admin, db_session):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    p1 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "HICKORY CHUNKS BAG", "line_total": "6.00"}]
    )
    line = (await resolve(admin_client, p1, db_session))["lines"][0]
    await decide(admin_client, p1, line["id"], {"supply_category": "fuel"})
    p2 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "HICKORY CHUNKS BAGS", "line_total": "6.00"}]
    )
    line = (await resolve(admin_client, p2, db_session))["lines"][0]
    assert line["resolution"] == "unmatched"
    assert [(s["kind"], s["supply_category"]) for s in line["suggestions"]] == [("fuzzy", "fuel")]


async def test_choosing_again_moves_the_line_and_resets_the_alias(
    admin_client, admin, db_session, owner_conn
):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    wrap = await make_product(admin_client, "Rice paper", "Rice paper wrappers")
    p = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "PAPER WRAPS 22CT", "line_total": "3.00"}]
    )
    line = (await resolve(admin_client, p, db_session))["lines"][0]
    await decide(admin_client, p, line["id"], {"supply_category": "wraps"})
    await decide(admin_client, p, line["id"], {"supply_category": "wraps"})
    assert (await alias_rows(owner_conn))[0]["confirmed_count"] == 2
    await decide(admin_client, p, line["id"], {"supply_category": "cooking"})
    assert (await alias_rows(owner_conn))[0]["confirmed_count"] == 1
    # It was a food after all: a product, no category, and a price once committed.
    r = await decide(admin_client, p, line["id"], {"product_id": wrap["id"]})
    moved = r.json()["lines"][0]
    assert moved["resolution"] == "manual" and moved["supply_category"] is None
    assert r.json()["supply_total"] is None
    [alias] = await alias_rows(owner_conn)
    assert (alias["disposition"], alias["supply_category"]) == ("product", None)


async def test_a_supply_line_that_stops_being_an_item_drops_its_category(
    admin_client, admin, db_session
):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    p = await make_receipt_purchase(admin.id, loc["id"], [{"raw_text": FOIL, "line_total": "4.29"}])
    line = (await resolve(admin_client, p, db_session))["lines"][0]
    await decide(admin_client, p, line["id"], {"supply_category": "wraps"})
    r = await admin_client.patch(
        f"/api/v1/purchases/{p}/lines/{line['id']}", json={"line_kind": "fee"}
    )
    assert r.status_code == 200, r.text
    fee = r.json()["lines"][0]
    assert (fee["resolution"], fee["supply_category"]) == ("unmatched", None)


async def test_to_identify_can_mark_a_group_as_a_supply(
    admin_client, admin, db_session, owner_conn
):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    for amount in ("5.25", "5.50"):
        p = await make_receipt_purchase(
            admin.id, loc["id"], [{"raw_text": f"ZIP FREEZER BAGS {amount}", "line_total": amount}]
        )
        await resolve(admin_client, p, db_session)
        await admin_client.post(f"/api/v1/purchases/{p}/commit")
    [group] = (await admin_client.get("/api/v1/to-identify")).json()["items"]
    assert group["line_count"] == 2
    r = await admin_client.post(
        "/api/v1/to-identify/apply",
        json={
            "vendor_id": group["vendor"]["id"],
            "raw_text_norm": group["raw_text_norm"],
            "supply_category": "storage",
        },
    )
    assert r.json() == {"applied": 2}
    assert (await admin_client.get("/api/v1/to-identify")).json()["items"] == []
    [alias] = await alias_rows(owner_conn)
    assert (alias["disposition"], alias["supply_category"]) == ("supply", "storage")
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in inbox if i["kind"] == "identify"]
    bad = await admin_client.post(
        "/api/v1/to-identify/apply",
        json={
            "vendor_id": group["vendor"]["id"],
            "raw_text_norm": group["raw_text_norm"],
            "supply_category": "storage",
            "ignore": True,
        },
    )
    assert bad.status_code == 422


async def test_the_location_panel_counts_supply_spend(admin_client, admin, db_session):
    loc = await make_location(admin_client, "Lantern Row Grocer", "Lantern Row Grocer")
    p = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [
            {"raw_text": "PARCHMENT ROLL 3.80", "line_total": "3.80"},
            {"raw_text": "CLOUDBERRY JAM 3.10", "line_total": "3.10"},
        ],
        purchased_at=datetime.now(UTC),
    )
    lines = (await resolve(admin_client, p, db_session))["lines"]
    await decide(admin_client, p, lines[0]["id"], {"supply_category": "wraps"})
    url = f"/api/v1/vendor-locations/{loc['id']}/price-panel"
    draft = (await admin_client.get(url, params={"days": 30})).json()
    assert draft["supply_spend"] == "0"  # a draft is not spend yet
    await admin_client.post(f"/api/v1/purchases/{p}/commit")
    panel = (await admin_client.get(url, params={"days": 30})).json()
    assert panel["spend"] == "6.9000" and panel["supply_spend"] == "3.8000"
