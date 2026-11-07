"""Weekly flyers 2U-3 (18): offer links, learned flyer names, the offer queue and the
inbox rows (U7, U8, U17; ruling F13). Stores and offers are invented."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core.db import get_sessionmaker
from app.services import flyer_offers, flyers
from app.services.resolution import set_ranker
from tests.pricebook_helpers import make_location
from tests.test_flyers import (
    WEDNESDAY,
    bought,
    candidate,
    queue,
    report,
    requests,
    seed,
    source_row,
    store,  # noqa: F401  the invented store fixture
)
from tests.test_products_helper import helper  # noqa: F401  the stub helper's client

# Inside the fixtures' ad week (2026-11-04 to 2026-11-10).
THURSDAY = date(2026, 11, 5)


@pytest.fixture(autouse=True)
def _in_the_ad_week(monkeypatch):
    monkeypatch.setattr(flyers, "today", lambda now=None: THURSDAY)
    set_ranker(None)
    yield
    set_ranker(None)


async def watch(admin_client, ingredient_id: str) -> None:
    r = await admin_client.patch(f"/api/v1/ingredients/{ingredient_id}", json={"watch": True})
    assert r.status_code == 200, r.text
    assert r.json()["watch"] is True


async def offer_queue(admin_client) -> dict:
    r = await admin_client.get("/api/v1/flyer-offers/queue")
    assert r.status_code == 200, r.text
    return r.json()


async def flyer_rows(admin_client) -> list[dict]:
    r = await admin_client.get("/api/v1/inbox")
    assert r.status_code == 200, r.text
    return [i for i in r.json()["items"] if i["kind"].startswith("flyer_")]


async def read_week(helper_client, names: list[str]) -> dict:
    await queue()
    [request] = await requests(helper_client)
    r = await report(helper_client, request["id"], candidates=[candidate(n) for n in names])
    assert r.status_code == 200, r.text
    return request


async def links(owner_conn) -> list[dict]:
    rows = await owner_conn.fetch(
        "SELECT o.name_as_printed, l.status, l.resolution, l.product_id, l.suggestions "
        "FROM flyer_offer_link l JOIN flyer_offer o ON o.id = l.flyer_offer_id "
        "ORDER BY o.created_at"
    )
    return [dict(r) for r in rows]


# --- U7: resolution -----------------------------------------------------------------------


async def test_an_unwatched_offer_waits_quietly_with_similar_names(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    lentils = await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await read_week(helper, ["Green Lentils"])
    [link] = await links(owner_conn)
    assert (link["status"], link["resolution"]) == ("suggested", "similar")
    assert '"' + lentils["id"] + '"' in link["suggestions"]
    queued = await offer_queue(admin_client)
    assert queued["watched"] == []
    [group] = queued["other"]
    assert (group["name_norm"], len(group["offers"])) == ("green lentils", 1)
    assert group["suggestions"][0]["product_id"] == lentils["id"]
    assert await flyer_rows(admin_client) == []  # F13: only watched offers ask


async def test_a_watched_offer_asks_and_its_answer_links_next_time(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    lentils = await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await watch(admin_client, lentils["ingredient"]["id"])
    request = await read_week(helper, ["Green Lentils"])
    [row] = await flyer_rows(admin_client)
    assert (row["title"], row["action_route"]) == ("1 flyer offer to match", "/shop/offers/match")
    [group] = (await offer_queue(admin_client))["watched"]

    r = await admin_client.post(
        "/api/v1/flyer-offers/decide",
        json={
            "vendor_id": group["vendor"]["id"],
            "name_norm": group["name_norm"],
            "product_id": lentils["id"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["decided"] == 1
    assert await flyer_rows(admin_client) == []
    [link] = await links(owner_conn)
    assert (link["status"], link["resolution"]) == ("confirmed", "person")
    alias = await owner_conn.fetchrow("SELECT name_norm, ignore, confirmed_count FROM flyer_alias")
    assert tuple(alias) == ("green lentils", False, 1)

    # The same name again (a replay stands in for next week) links on its own.
    r = await report(helper, request["id"], candidates=[candidate("Green Lentils")])
    assert r.status_code == 200, r.text
    later = (await links(owner_conn))[-1]
    assert (later["status"], later["resolution"]) == ("auto", "alias")
    assert str(later["product_id"]) == lentils["id"]
    assert (await offer_queue(admin_client)) == {"watched": [], "other": []}


async def test_not_wanted_is_learned_and_drops_the_name_next_time(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    request = await read_week(helper, ["Green Lentils"])
    [group] = (await offer_queue(admin_client))["other"]
    r = await admin_client.post(
        "/api/v1/flyer-offers/decide",
        json={"vendor_id": group["vendor"]["id"], "name_norm": "green lentils", "ignore": True},
    )
    assert r.status_code == 200, r.text
    [link] = await links(owner_conn)
    assert (link["status"], link["product_id"]) == ("rejected", None)
    r = await report(helper, request["id"], candidates=[candidate("Green Lentils")])
    assert r.json()["relevant"] == 0  # an ignored name is never relevant


async def test_one_answer_covers_every_store_of_the_vendor(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    second = await make_location(
        admin_client, "Larkspur Market", "Larkspur Market East", vendor_id=store["vendor"]["id"]
    )
    assert (await seed([source_row(second)])).created == 1
    lentils = await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    assert await queue() == 2
    for request in await requests(helper):
        r = await report(helper, request["id"], candidates=[candidate("Green Lentils")])
        assert r.status_code == 200, r.text
    [group] = (await offer_queue(admin_client))["other"]
    assert len(group["offers"]) == 2
    r = await admin_client.post(
        "/api/v1/flyer-offers/decide",
        json={
            "vendor_id": store["vendor"]["id"],
            "name_norm": "green lentils",
            "product_id": lentils["id"],
        },
    )
    assert r.json()["decided"] == 2
    assert {link["status"] for link in await links(owner_conn)} == {"confirmed"}


async def test_a_source_that_asks_first_suggests_its_alias(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    lentils = await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await owner_conn.execute("UPDATE ad_source SET require_confirmation = true")
    await owner_conn.execute(
        "INSERT INTO flyer_alias (id, vendor_id, name_norm, product_id, ignore) "
        "VALUES ($1, $2, 'green lentils', $3, false)",
        uuid.uuid4(),
        uuid.UUID(store["vendor"]["id"]),
        uuid.UUID(lentils["id"]),
    )
    await read_week(helper, ["Green Lentils"])
    [link] = await links(owner_conn)
    assert (link["status"], link["resolution"]) == ("suggested", "alias")
    # Not watched, but the source asks first, so it asks in the inbox too.
    assert len((await offer_queue(admin_client))["watched"]) == 1
    [row] = await flyer_rows(admin_client)
    assert row["kind"] == "flyer_offers"


async def test_an_expired_offer_leaves_the_queue(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    monkeypatch,
):
    await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await read_week(helper, ["Green Lentils"])
    monkeypatch.setattr(flyers, "today", lambda now=None: date(2026, 11, 11))
    assert await offer_queue(admin_client) == {"watched": [], "other": []}


async def test_a_decision_with_nothing_waiting_is_not_found(admin_client, store):  # noqa: F811
    r = await admin_client.post(
        "/api/v1/flyer-offers/decide",
        json={"vendor_id": store["vendor"]["id"], "name_norm": "green lentils", "ignore": True},
    )
    assert r.status_code == 404


# --- the model, after the report ----------------------------------------------------------


async def test_the_worker_asks_the_model_about_a_watched_offer_once(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    lentils = await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await watch(admin_client, lentils["ingredient"]["id"])
    asked: list[str] = []

    async def ranker(norm: str, shortlist: list[dict]) -> dict:
        asked.append(norm)
        return {"product_id": shortlist[0]["id"], "confidence": "0.9"}

    await read_week(helper, ["Green Lentils"])
    set_ranker(ranker)
    async with get_sessionmaker()() as db:
        assert await flyer_offers.rank_once(db, THURSDAY) is True
        assert await flyer_offers.rank_once(db, THURSDAY) is False  # asked once only
    assert asked == ["green lentils"]
    [link] = await links(owner_conn)
    assert link["resolution"] == "ranked"
    [group] = (await offer_queue(admin_client))["watched"]
    assert (group["suggestions"][0]["kind"], group["suggestions"][0]["product_id"]) == (
        "llm",
        lentils["id"],
    )


# --- U8: a week that went unread ----------------------------------------------------------


async def test_a_week_that_ran_out_of_tries_is_one_inbox_row(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
):
    now = WEDNESDAY
    for attempt in range(flyers.ATTEMPTS_PER_WEEK):
        assert await flyer_rows(admin_client) == []
        assert await queue(now) == 1
        [request] = await requests(helper)
        r = await report(helper, request["id"], status="failed", reason=f"Timed out {attempt}.")
        assert r.status_code == 200, r.text
        now += timedelta(days=1)
    [row] = await flyer_rows(admin_client)
    assert row["kind"] == "flyer_sources"
    assert row["detail"] == "It couldn't be read: Timed out 2."
    assert row["action_route"] == f"/catalog/vendors/{store['vendor']['id']}"


async def test_a_site_that_refuses_is_one_inbox_row_at_once(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
):
    await queue()
    [request] = await requests(helper)
    r = await report(helper, request["id"], status="not_allowed", reason="robots.txt")
    assert r.status_code == 200, r.text
    [row] = await flyer_rows(admin_client)
    assert row["detail"] == "The store's site doesn't allow reading its circular."


# --- U17: watching an ingredient ----------------------------------------------------------


async def test_an_ingredient_can_be_watched_and_unwatched(admin_client, store):  # noqa: F811
    lentils = await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    ingredient_id = lentils["ingredient"]["id"]
    r = await admin_client.get(f"/api/v1/ingredients/{ingredient_id}")
    assert r.json()["watch"] is False
    await watch(admin_client, ingredient_id)
    r = await admin_client.patch(f"/api/v1/ingredients/{ingredient_id}", json={"watch": False})
    assert r.json()["watch"] is False
    r = await admin_client.patch(f"/api/v1/ingredients/{ingredient_id}", json={"notes": "x"})
    assert r.json()["watch"] is False  # untouched by an unrelated edit
