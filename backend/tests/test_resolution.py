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
    await resolution.resolve_purchase(db_session, __import__("uuid").UUID(purchase_id))
    return (await client.get(f"/api/v1/purchases/{purchase_id}")).json()


async def test_confirmed_alias_resolves_and_unconfirmed_only_suggests(
    admin_client, admin, db_session
):
    loc = await make_location(
        admin_client, "Trader Jane's", "Trader Jane's Uptown", kind="chain", price_scope="chain"
    )
    pep = await make_product(
        admin_client, "Hot peppers", "Italian bomba hot pepper spread", pack_qty="6", pack_unit="oz"
    )
    p1 = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [
            {
                "raw_text": "ITAL BOMBA HOT PEP 3.99 F",
                "line_total": "3.99",
                "qty": "1",
                "unit": "each",
            }
        ],
    )
    body = await resolve(admin_client, p1, db_session)
    line = body["lines"][0]
    assert line["resolution"] == "unmatched" and line["raw_text_norm"] == "ITAL BOMBA HOT PEP"
    # A person chooses the product: this writes a confirmed alias.
    r = await admin_client.post(
        f"/api/v1/purchases/{p1}/lines/{line['id']}/resolve", json={"product_id": pep["id"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["resolution"] == "manual"
    # Next receipt from the same vendor, another location, same line: automatic.
    loc2 = await make_location(
        admin_client,
        None,
        "Trader Jane's Downtown",
        vendor_id=loc["vendor"]["id"],
        coords=("33.6", "-120.4"),
    )
    p2 = await make_receipt_purchase(
        admin.id,
        loc2["id"],
        [
            {
                "raw_text": "ITAL BOMBA HOT PEP 3.99 F",
                "line_total": "3.99",
                "qty": "1",
                "unit": "each",
            }
        ],
    )
    body = await resolve(admin_client, p2, db_session)
    line2 = body["lines"][0]
    assert line2["resolution"] == "alias" and line2["product"]["id"] == pep["id"]
    assert line2["resolved_by"] is None and line2["flags"] == []
    assert line2["resolved_by_name"] is None


async def test_unconfirmed_alias_does_not_auto_resolve(admin_client, admin, db_session, owner_conn):
    loc = await make_location(admin_client, "Trader Jane's", "Trader Jane's Uptown")
    pep = await make_product(admin_client, "Hot peppers", "Bomba spread")
    import uuid

    await owner_conn.execute(
        "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, product_id, "
        "confirmed_count, last_seen_at) "
        "VALUES ($1, $2, 'ITAL BOMBA HOT PEP', 'product', $3, 0, now())",
        uuid.uuid4(),
        uuid.UUID(loc["vendor"]["id"]),
        uuid.UUID(pep["id"]),
    )
    p = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "ITAL BOMBA HOT PEP", "line_total": "3.99"}]
    )
    line = (await resolve(admin_client, p, db_session))["lines"][0]
    assert line["resolution"] == "unmatched" and line["product"] is None
    assert line["suggestions"][0]["kind"] == "alias_unconfirmed"
    assert line["suggestions"][0]["product_id"] == pep["id"]


async def test_fuzzy_alias_and_llm_only_suggest_and_llm_outside_shortlist_is_rejected(
    admin_client, admin, db_session
):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    pep = await make_product(admin_client, "Hot peppers", "Bomba spread")
    rig = await make_product(admin_client, "Rigatoni", "Rigatoni box")
    # Learn an alias, then a near-miss line from the same vendor gets a fuzzy hint.
    p1 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "ITAL BOMBA HOT PEPPER", "line_total": "3.99"}]
    )
    line = (await resolve(admin_client, p1, db_session))["lines"][0]
    await admin_client.post(
        f"/api/v1/purchases/{p1}/lines/{line['id']}/resolve", json={"product_id": pep["id"]}
    )
    p2 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "ITAL BOMBA HOT PEPPR", "line_total": "3.99"}]
    )
    line = (await resolve(admin_client, p2, db_session))["lines"][0]
    assert line["resolution"] == "unmatched" and line["product"] is None
    assert any(s["kind"] == "fuzzy" and s["product_id"] == pep["id"] for s in line["suggestions"])

    # A model that answers outside the shortlist is rejected; inside, it is a suggestion only.
    calls = []

    async def rogue(norm, shortlist):
        calls.append(shortlist)
        return {"product_id": pep["id"], "confidence": 0.9}  # not on the rigatoni shortlist

    set_ranker(rogue)
    p3 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "RIGATONI BOX", "line_total": "2.49"}]
    )
    line = (await resolve(admin_client, p3, db_session))["lines"][0]
    assert calls and all(c["id"] != pep["id"] for c in calls[0])
    assert line["resolution"] == "unmatched" and line["suggestions"] == []

    async def honest(norm, shortlist):
        return {"product_id": shortlist[0]["id"], "confidence": 0.8}

    set_ranker(honest)
    p4 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "RIGATONI BOX", "line_total": "2.49"}]
    )
    line = (await resolve(admin_client, p4, db_session))["lines"][0]
    assert line["resolution"] == "unmatched" and line["product"] is None
    assert (
        line["suggestions"][0]["kind"] == "llm"
        and line["suggestions"][0]["product_id"] == rig["id"]
    )
    # Accepting records the rung and the person.
    r = await admin_client.post(
        f"/api/v1/purchases/{p4}/lines/{line['id']}/resolve",
        json={"product_id": rig["id"], "accepted_kind": "llm"},
    )
    assert r.json()["lines"][0]["resolution"] == "llm"
    assert r.json()["lines"][0]["resolved_by"] == str(admin.id)
    # The name to show, so the review page never prints the id.
    assert r.json()["lines"][0]["resolved_by_name"] == admin.display_name
    # The list carries it too, from one lookup for the whole page.
    listed = (await admin_client.get("/api/v1/purchases")).json()["items"]
    mine = next(p for p in listed if p["id"] == str(p4))
    assert mine["lines"][0]["resolved_by_name"] == admin.display_name


async def test_barcode_rung_resolves_without_a_person(admin_client, admin, db_session):
    loc = await make_location(admin_client, "Warehouse Co", "Warehouse Co")
    code = "036000291452"
    nuts = await make_product(
        admin_client, "Almonds", "Almonds 3 lb", barcode=code, pack_qty="3", pack_unit="lb"
    )
    p = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [
            {
                "raw_text": f"{code} ALMONDS 3LB 12.99",
                "line_total": "12.99",
                "qty": "1",
                "unit": "each",
            }
        ],
    )
    line = (await resolve(admin_client, p, db_session))["lines"][0]
    assert line["resolution"] == "barcode" and line["product"]["id"] == nuts["id"]


async def test_price_outlier_flag_on_alias_resolution(admin_client, admin, db_session):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    pep = await make_product(
        admin_client, "Hot peppers", "Bomba spread", pack_qty="6", pack_unit="oz"
    )
    spec = {"raw_text": "BOMBA SPREAD 3.99", "line_total": "3.99", "qty": "1", "unit": "each"}
    p1 = await make_receipt_purchase(admin.id, loc["id"], [spec])
    line = (await resolve(admin_client, p1, db_session))["lines"][0]
    await admin_client.post(
        f"/api/v1/purchases/{p1}/lines/{line['id']}/resolve", json={"product_id": pep["id"]}
    )
    await admin_client.post(f"/api/v1/purchases/{p1}/commit")
    # Same alias, price five times higher: resolved tentatively, flagged.
    p2 = await make_receipt_purchase(
        admin.id, loc["id"], [{**spec, "raw_text": "BOMBA SPREAD 19.99", "line_total": "19.99"}]
    )
    line = (await resolve(admin_client, p2, db_session))["lines"][0]
    assert line["resolution"] == "alias" and "price_outlier" in line["flags"]
    p3 = await make_receipt_purchase(admin.id, loc["id"], [{**spec, "line_total": "4.29"}])
    line = (await resolve(admin_client, p3, db_session))["lines"][0]
    assert line["resolution"] == "alias" and line["flags"] == []


async def test_commit_with_unresolved_lines_queues_them_and_identify_applies_to_all(
    admin_client, admin, db_session
):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    rig = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    p1 = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [
            {"raw_text": "RIGATONI 2.49", "line_total": "2.49", "qty": "1", "unit": "each"},
            {"raw_text": "MYSTERY ITEM 1.00", "line_total": "1.00"},
        ],
    )
    body = await resolve(admin_client, p1, db_session)
    l1, l2 = body["lines"]
    await admin_client.post(
        f"/api/v1/purchases/{p1}/lines/{l1['id']}/resolve", json={"product_id": rig["id"]}
    )
    r = await admin_client.post(f"/api/v1/purchases/{p1}/commit")
    assert r.status_code == 200, r.text
    committed = r.json()
    assert committed["status"] == "committed"
    assert (
        committed["lines"][0]["observation_id"] and committed["lines"][1]["observation_id"] is None
    )
    assert committed["total"] == "3.4900"
    p2 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "MYSTERY ITEM 1.05", "line_total": "1.05"}]
    )
    await resolve(admin_client, p2, db_session)
    await admin_client.post(f"/api/v1/purchases/{p2}/commit")
    queue = (await admin_client.get("/api/v1/to-identify")).json()["items"]
    assert (
        len(queue) == 1
        and queue[0]["raw_text_norm"] == "MYSTERY ITEM"
        and queue[0]["line_count"] == 2
    )
    mystery = await make_product(admin_client, "Mystery", "Mystery item")
    r = await admin_client.post(
        "/api/v1/to-identify/apply",
        json={
            "vendor_id": loc["vendor"]["id"],
            "raw_text_norm": "MYSTERY ITEM",
            "product_id": mystery["id"],
        },
    )
    assert r.json() == {"applied": 2}
    assert (await admin_client.get("/api/v1/to-identify")).json()["items"] == []
    obs = (
        await admin_client.get("/api/v1/price-observations", params={"product_id": mystery["id"]})
    ).json()["items"]
    assert len(obs) == 2 and {o["observed_at"][:10] for o in obs} == {"2026-05-02"}
    assert {o["price"] for o in obs} == {"1.0000", "1.0500"}
    # The alias was learned: a third receipt resolves automatically.
    p3 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "MYSTERY ITEM 1.10", "line_total": "1.10"}]
    )
    resolved = await resolve(admin_client, p3, db_session)
    assert resolved["lines"][0]["resolution"] == "alias"


async def test_ignored_lines_emit_nothing_and_stay_ignored(admin_client, admin, db_session):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    p1 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "PAPER TOWELS 8.99", "line_total": "8.99"}]
    )
    line = (await resolve(admin_client, p1, db_session))["lines"][0]
    r = await admin_client.post(
        f"/api/v1/purchases/{p1}/lines/{line['id']}/resolve", json={"ignore": True}
    )
    assert r.json()["lines"][0]["resolution"] == "ignored"
    await admin_client.post(f"/api/v1/purchases/{p1}/commit")
    assert (await admin_client.get("/api/v1/price-observations")).json()["items"] == []
    assert (await admin_client.get("/api/v1/to-identify")).json()["items"] == []
    p2 = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "PAPER TOWELS 9.49", "line_total": "9.49"}]
    )
    resolved = await resolve(admin_client, p2, db_session)
    assert resolved["lines"][0]["resolution"] == "ignored"


async def test_attached_discount_and_deposit_affect_the_observation_correctly(
    admin_client, admin, db_session
):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    r = await admin_client.post(
        "/api/v1/products",
        json={
            "ingredient": {"name": "Sparkling water", "canonical_unit": "ml"},
            "name": "Sparkling water 1 l",
            "pack_qty": "1",
            "pack_unit": "l",
        },
    )
    water = r.json()
    p = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [
            {"raw_text": "SPARKLING WATER 2.49", "line_total": "2.49", "qty": "1", "unit": "each"},
            {
                "raw_text": "MEMBER SAVINGS -0.50",
                "line_total": "-0.50",
                "line_kind": "discount",
                "parent": 1,
            },
            {"raw_text": "CRV 0.10", "line_total": "0.10", "line_kind": "deposit", "parent": 1},
        ],
    )
    body = await resolve(admin_client, p, db_session)
    item = body["lines"][0]
    await admin_client.post(
        f"/api/v1/purchases/{p}/lines/{item['id']}/resolve", json={"product_id": water["id"]}
    )
    committed = (await admin_client.post(f"/api/v1/purchases/{p}/commit")).json()
    assert committed["computed_total"] == "2.0900"
    obs = (
        await admin_client.get(
            f"/api/v1/price-observations/{committed['lines'][0]['observation_id']}"
        )
    ).json()
    assert obs["price"] == "1.9900" and obs["is_promo"] is True
    assert obs["norm"]["norm_unit_price"] == "0.001990"


async def test_reopen_and_repoint_one_line_voids_and_reemits_only_that_line(
    admin_client, admin, db_session
):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    a = await make_product(admin_client, "Rigatoni", "Rigatoni box")
    b = await make_product(admin_client, "Penne", "Penne box")
    c = await make_product(admin_client, "Fusilli", "Fusilli box")
    p = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [
            {"raw_text": "PASTA A 2.49", "line_total": "2.49", "qty": "1", "unit": "each"},
            {"raw_text": "PASTA B 2.59", "line_total": "2.59", "qty": "1", "unit": "each"},
        ],
    )
    body = await resolve(admin_client, p, db_session)
    for line, product in zip(body["lines"], (a, b), strict=True):
        await admin_client.post(
            f"/api/v1/purchases/{p}/lines/{line['id']}/resolve", json={"product_id": product["id"]}
        )
    committed = (await admin_client.post(f"/api/v1/purchases/{p}/commit")).json()
    obs_before = {ln["seq"]: ln["observation_id"] for ln in committed["lines"]}
    assert (
        await admin_client.patch(f"/api/v1/purchases/{p}", json={"total": "9"})
    ).status_code == 409
    reopened = (await admin_client.post(f"/api/v1/purchases/{p}/reopen")).json()
    assert reopened["status"] == "reviewed"
    line_b = reopened["lines"][1]
    r = await admin_client.post(
        f"/api/v1/purchases/{p}/lines/{line_b['id']}/resolve", json={"product_id": c["id"]}
    )
    assert r.status_code == 200
    recommitted = (await admin_client.post(f"/api/v1/purchases/{p}/commit")).json()
    obs_after = {ln["seq"]: ln["observation_id"] for ln in recommitted["lines"]}
    assert obs_after[1] == obs_before[1]
    assert obs_after[2] != obs_before[2]
    old = (await admin_client.get(f"/api/v1/price-observations/{obs_before[2]}")).json()
    assert old["voided"] is True
    new = (await admin_client.get(f"/api/v1/price-observations/{obs_after[2]}")).json()
    assert new["product"]["id"] == c["id"] and new["voided"] is False
    # The alias now points at the new product with a reset count.
    p2 = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [{"raw_text": "PASTA B 2.59", "line_total": "2.59", "qty": "1", "unit": "each"}],
    )
    resolved = await resolve(admin_client, p2, db_session)
    assert resolved["lines"][0]["product"]["id"] == c["id"]


async def test_review_edits_lines_and_header(admin_client, admin, db_session):
    loc = await make_location(admin_client, "Corner Grocer", "Corner Grocer")
    loc2 = await make_location(
        admin_client,
        None,
        "Corner Grocer Annex",
        vendor_id=loc["vendor"]["id"],
        coords=("33.6", "-120.4"),
    )
    p = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "THING 1.00", "line_total": "1.00"}]
    )
    r = await admin_client.post(
        f"/api/v1/purchases/{p}/lines",
        json={"raw_text": "COUPON -0.25", "line_kind": "discount", "line_total": "-0.25"},
    )
    assert r.status_code == 201 and len(r.json()["lines"]) == 2
    item, coupon = r.json()["lines"]
    r = await admin_client.patch(
        f"/api/v1/purchases/{p}/lines/{coupon['id']}", json={"parent_line_id": item["id"]}
    )
    assert r.json()["lines"][1]["parent_line_id"] == item["id"]
    r = await admin_client.patch(
        f"/api/v1/purchases/{p}/lines/{item['id']}",
        json={"qty": "2", "unit": "each", "unit_price": "0.50"},
    )
    assert r.json()["lines"][0]["qty"] == "2"
    r = await admin_client.patch(
        f"/api/v1/purchases/{p}", json={"vendor_location_id": loc2["id"], "total": "0.75"}
    )
    assert r.json()["vendor_location"]["id"] == loc2["id"] and r.json()["total"] == "0.7500"
    r = await admin_client.delete(f"/api/v1/purchases/{p}/lines/{coupon['id']}")
    assert r.status_code == 200 and len(r.json()["lines"]) == 1
    assert r.json()["computed_total"] == "1.0000"


async def test_resolver_names_is_one_query_for_a_page_and_none_for_nobody():
    """A page of purchases looks resolvers up once, not once per purchase."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.services import purchases as purchase_service

    class Recorder:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            return SimpleNamespace(all=lambda: [])

    a, b = uuid4(), uuid4()
    page = [
        SimpleNamespace(lines=[SimpleNamespace(resolved_by=a), SimpleNamespace(resolved_by=None)]),
        SimpleNamespace(lines=[SimpleNamespace(resolved_by=b)]),
        SimpleNamespace(lines=[SimpleNamespace(resolved_by=a)]),
    ]
    db = Recorder()
    await purchase_service.resolver_names(db, page)
    assert db.calls == 1

    nobody = Recorder()
    await purchase_service.resolver_names(
        nobody, [SimpleNamespace(lines=[SimpleNamespace(resolved_by=None)])]
    )
    assert nobody.calls == 0


async def test_a_person_setting_the_quantity_clears_the_quantity_flags(admin_client, admin):
    """#31: qty_assumed says nothing supports the number; once someone sets it, it is theirs."""
    loc = await make_location(admin_client, "Quay Grocer", "Quay Grocer")
    purchase_id = await make_receipt_purchase(
        admin.id,
        loc["id"],
        [
            {
                "raw_text": "APPLES 2.10 1b 3.13",
                "line_total": "3.13",
                "qty": "1",
                "unit": "each",
                "flags": ["qty_assumed", "price_outlier"],
            },
        ],
    )
    detail = (await admin_client.get(f"/api/v1/purchases/{purchase_id}")).json()
    line_id = detail["lines"][0]["id"]
    assert detail["lines"][0]["flags"] == ["qty_assumed", "price_outlier"]

    # Editing something else leaves the flag: the quantity is still unsupported.
    r = await admin_client.patch(
        f"/api/v1/purchases/{purchase_id}/lines/{line_id}", json={"raw_text": "APPLES 2.10 lb 3.13"}
    )
    assert r.json()["lines"][0]["flags"] == ["qty_assumed", "price_outlier"]

    r = await admin_client.patch(
        f"/api/v1/purchases/{purchase_id}/lines/{line_id}", json={"qty": "2.10", "unit": "lb"}
    )
    assert r.status_code == 200
    line = r.json()["lines"][0]
    assert (line["qty"], line["unit"]) == ("2.10", "lb")
    assert line["flags"] == ["price_outlier"]
