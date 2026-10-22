"""A weighed-item code some counters share between items (04, 2K; #448).

Two cheeses from one invented counter print the same item code; the digit that
tells them apart falls outside the label layout. The store, wording and codes
are invented. Weighed-item labels get computed check digits.
"""

from __future__ import annotations

import asyncpg

from app.core.db import get_sessionmaker
from app.services.waiting_lines import match_all_waiting, match_waiting
from tests.pricebook_helpers import make_location, make_product
from tests.resolution_helpers import make_receipt_purchase
from tests.test_item_codes import label, remember
from tests.test_resolution import resolve

CODE = "12345"
CHEESE_A = {"raw_text": f"{label('2 12345 10000')} CTR CHEESE ONE", "line_total": "6.10"}
CHEESE_B = {"raw_text": f"{label('2 12345 70000')} CTR CHEESE TWO", "line_total": "7.40"}


async def _waiting(admin_client, admin, db_session, loc: dict, lines: list[dict]) -> str:
    purchase = await make_receipt_purchase(admin.id, loc["id"], lines)
    await resolve(admin_client, purchase, db_session)
    r = await admin_client.post(f"/api/v1/purchases/{purchase}/commit")
    assert r.status_code == 200, r.text
    return purchase


async def _lines(admin_client, purchase: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for line in (await admin_client.get(f"/api/v1/purchases/{purchase}")).json()["lines"]:
        out.setdefault(line["raw_text"].split(" ", 1)[1], []).append(line)
    return out


async def _identify(admin_client, purchase: str, line: dict, product: dict) -> None:
    r = await admin_client.post(
        f"/api/v1/purchases/{purchase}/lines/{line['id']}/resolve",
        json={"product_id": product["id"]},
    )
    assert r.status_code == 200, r.text


async def test_remembering_a_code_other_wording_prints_asks_first_and_identifies_only_its_own(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese_a = await make_product(admin_client, "Cheese A", "Counter cheese A")
    purchase = await _waiting(
        admin_client, admin, db_session, loc, [CHEESE_A, CHEESE_A, CHEESE_B, CHEESE_B]
    )
    first_a = (await _lines(admin_client, purchase))["CTR CHEESE ONE"][0]
    await _identify(admin_client, purchase, first_a, cheese_a)
    remember_url = f"/api/v1/purchases/{purchase}/lines/{first_a['id']}/remember-code"
    count = f"SELECT count(*) FROM product_identifier WHERE value = '{CODE}'"

    r = await admin_client.post(remember_url)
    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["code"] == "code_shared"
    assert error["details"]["wordings"] == [{"text": "CTR CHEESE TWO", "lines": 2}]
    assert await owner_conn.fetchval(count) == 0  # nothing without the second click

    r = await admin_client.post(remember_url, json={"shared_ok": True})
    assert r.status_code == 200, r.text
    lines = await _lines(admin_client, purchase)
    held = {h["line_id"] for h in r.json()["held"]}
    assert held == {ln["id"] for ln in lines["CTR CHEESE TWO"]}
    # The other line that reads like it is identified; the other wording waits.
    assert {ln["product"]["id"] for ln in lines["CTR CHEESE ONE"]} == {cheese_a["id"]}
    for line in lines["CTR CHEESE TWO"]:
        assert line["resolution"] == "unmatched"
        assert line["suggestions"][0]["kind"] == "code"
        assert line["suggestions"][0]["product_id"] == cheese_a["id"]


async def test_a_new_receipt_reading_differently_is_only_offered_the_code(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese_a = await make_product(admin_client, "Cheese A", "Counter cheese A")
    first = await _waiting(admin_client, admin, db_session, loc, [CHEESE_A])
    await _identify(
        admin_client, first, (await _lines(admin_client, first))["CTR CHEESE ONE"][0], cheese_a
    )
    await remember(owner_conn, cheese_a["id"], "rw_item", CODE, loc["vendor"]["id"])

    later = await make_receipt_purchase(admin.id, loc["id"], [CHEESE_A, CHEESE_B])
    same, other = sorted(
        (await resolve(admin_client, later, db_session))["lines"], key=lambda ln: ln["seq"]
    )
    assert same["resolution"] == "identifier" and same["product"]["id"] == cheese_a["id"]
    assert other["resolution"] == "unmatched"
    assert [s["product_id"] for s in other["suggestions"] if s["kind"] == "code"] == [
        cheese_a["id"]
    ]


async def test_waiting_lines_with_two_wordings_take_a_new_code_only_when_they_agree(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    """A code added with no wording known yet: one wording takes it, two wait."""
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese_a = await make_product(admin_client, "Cheese A", "Counter cheese A")
    mixed = await _waiting(admin_client, admin, db_session, loc, [CHEESE_A, CHEESE_B])
    await remember(owner_conn, cheese_a["id"], "rw_item", CODE, loc["vendor"]["id"])
    async with get_sessionmaker()() as db:
        assert await match_all_waiting(db, admin) == (0, 1)
    assert all(
        ln["resolution"] == "unmatched"
        for lns in (await _lines(admin_client, mixed)).values()
        for ln in lns
    )


async def test_waiting_lines_that_all_read_alike_take_a_new_code(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese_a = await make_product(admin_client, "Cheese A", "Counter cheese A")
    alike = await _waiting(admin_client, admin, db_session, loc, [CHEESE_A, CHEESE_A])
    await remember(owner_conn, cheese_a["id"], "rw_item", CODE, loc["vendor"]["id"])
    async with get_sessionmaker()() as db:
        assert await match_all_waiting(db, admin) == (2, 1)
    lines = (await _lines(admin_client, alike))["CTR CHEESE ONE"]
    assert {ln["product"]["id"] for ln in lines} == {cheese_a["id"]}


async def test_a_weighed_code_only_this_wording_prints_is_remembered_at_once(
    admin_client, admin, db_session
):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese_a = await make_product(admin_client, "Cheese A", "Counter cheese A")
    purchase = await _waiting(admin_client, admin, db_session, loc, [CHEESE_A, CHEESE_A])
    first, second = (await _lines(admin_client, purchase))["CTR CHEESE ONE"]
    await _identify(admin_client, purchase, first, cheese_a)
    r = await admin_client.post(f"/api/v1/purchases/{purchase}/lines/{first['id']}/remember-code")
    assert r.status_code == 200, r.text
    assert r.json()["scheme"] == "rw_item" and r.json()["held"] == []
    after = (await _lines(admin_client, purchase))["CTR CHEESE ONE"]
    assert next(ln for ln in after if ln["id"] == second["id"])["resolution"] == "identifier"


async def test_a_line_another_product_names_is_judged_by_that_products_wordings(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    """Matching A's code also selects a label whose price digits hold it; B's code decides B."""
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese = await make_product(admin_client, "Cheese A", "Counter cheese A")
    olives = await make_product(admin_client, "Olives", "Counter olives")
    olive_line = {"raw_text": f"{label('2 54321 12345')} CTR OLIVES", "line_total": "4.10"}
    purchase = await _waiting(admin_client, admin, db_session, loc, [CHEESE_A, olive_line])
    await remember(owner_conn, olives["id"], "rw_item", "54321", loc["vendor"]["id"])
    await remember(owner_conn, cheese["id"], "rw_item", CODE, loc["vendor"]["id"])
    held: list[dict] = []
    async with get_sessionmaker()() as db:
        await match_waiting(db, admin, cheese["id"], held)
    lines = await _lines(admin_client, purchase)
    assert lines["CTR OLIVES"][0]["product"]["id"] == olives["id"]
    assert lines["CTR CHEESE ONE"][0]["product"]["id"] == cheese["id"]
    assert held == []


async def test_a_new_receipt_with_two_wordings_and_none_known_holds_both(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    """A code added from a page or an edit: no wording is known, so neither line takes it first."""
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese_a = await make_product(admin_client, "Cheese A", "Counter cheese A")
    await remember(owner_conn, cheese_a["id"], "rw_item", CODE, loc["vendor"]["id"])
    purchase = await make_receipt_purchase(admin.id, loc["id"], [CHEESE_B, CHEESE_A])
    lines = (await resolve(admin_client, purchase, db_session))["lines"]
    assert [ln["resolution"] for ln in lines] == ["unmatched", "unmatched"]
    for line in lines:
        assert line["suggestions"][0]["kind"] == "code"
        assert line["suggestions"][0]["product_id"] == cheese_a["id"]


async def test_a_line_the_code_identified_does_not_vouch_for_its_wording(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    """History from before the check: the code identified the other item's line on its own."""
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese_a = await make_product(admin_client, "Cheese A", "Counter cheese A")
    await remember(owner_conn, cheese_a["id"], "rw_item", CODE, loc["vendor"]["id"])
    earlier = await make_receipt_purchase(admin.id, loc["id"], [CHEESE_B])
    [old] = (await resolve(admin_client, earlier, db_session))["lines"]
    assert old["resolution"] == "identifier"  # the only wording, so it took the code
    await owner_conn.execute("DELETE FROM product_identifier WHERE value = $1", CODE)

    purchase = await _waiting(admin_client, admin, db_session, loc, [CHEESE_A])
    [line] = (await _lines(admin_client, purchase))["CTR CHEESE ONE"]
    await _identify(admin_client, purchase, line, cheese_a)
    r = await admin_client.post(f"/api/v1/purchases/{purchase}/lines/{line['id']}/remember-code")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["details"]["wordings"] == [{"text": "CTR CHEESE TWO", "lines": 1}]


async def test_a_held_line_is_still_offered_similar_names(
    admin_client, admin, db_session, owner_conn: asyncpg.Connection
):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    cheese_a = await make_product(admin_client, "Cheese A", "Counter cheese A")
    smoked = await make_product(admin_client, "Smoked gouda", "Deli smoked gouda")
    # Wording far from cheese A's alias, so no fuzzy alias speaks for it.
    gouda = {"raw_text": f"{label('2 12345 70000')} DELI SMOKED GOUDA", "line_total": "7.40"}
    first = await _waiting(admin_client, admin, db_session, loc, [CHEESE_A])
    [line] = (await _lines(admin_client, first))["CTR CHEESE ONE"]
    await _identify(admin_client, first, line, cheese_a)
    await remember(owner_conn, cheese_a["id"], "rw_item", CODE, loc["vendor"]["id"])
    later = await make_receipt_purchase(admin.id, loc["id"], [gouda])
    [held] = (await resolve(admin_client, later, db_session))["lines"]
    assert held["resolution"] == "unmatched"
    assert held["suggestions"][0]["product_id"] == cheese_a["id"]
    assert smoked["id"] in [s["product_id"] for s in held["suggestions"][1:]]
