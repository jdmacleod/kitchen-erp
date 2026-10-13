"""At the store (17, 4C, package 8): the stop screen and "Done here", criteria
22 to 26 and ruling S1.

Geography is the synthetic grid of ``geo_helpers``; vendors, products and
prices are invented. The kitchen's list holds five 1 kg items. Barley, oats,
rice and lentils are priced only at the chain's north store; millet costs 5.00
there and 3.00 at the pier stall. The value of an hour is 0.00, so the chosen
balanced variant is the cheapest: two stops, the four at north and millet at
the stall, which north's stop screen offers as "Cheaper at stop {m}", +2.00
here. Legs are estimated with every socket beyond the database refused.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest

from tests.pricebook_helpers import make_product, shelf
from tests.test_trip_planner import (  # noqa: F401
    ITEMS,
    TRIPS,
    add_item,
    clean_geo,
    kitchen,
    no_network,
    plan_trip,
    shops,
    variant,
)

NAMES = ("Barley", "Oats", "Rice", "Lentils", "Millet")


@pytest.fixture
async def trip(
    admin_client: httpx.AsyncClient,
    kitchen: dict,  # noqa: F811
    shops: dict,  # noqa: F811
    no_network: None,  # noqa: F811
) -> dict:
    """The planned (not yet chosen) trip, with its products and list items by name."""
    products: dict[str, dict] = {}
    items: dict[str, dict] = {}
    for name in NAMES:
        product = await make_product(
            admin_client, name, f"{name} bag", pack_qty="1", pack_unit="kg"
        )
        products[name] = product
        price = "5.00" if name == "Millet" else "4.00"
        await shelf(admin_client, product["id"], shops["north"]["id"], price)
        items[name] = await add_item(
            admin_client,
            kitchen,
            ingredient_id=product["ingredient"]["id"],
            amounts=[{"amount": "1", "unit": "kg"}],
        )
    await shelf(admin_client, products["Millet"]["id"], shops["stall"]["id"], "3.00")
    plan = await plan_trip(admin_client, kitchen, max_stops=2, value_of_time_per_hour="0.00")
    chosen = variant(plan, "balanced")
    by_location = {s["vendor_location_id"]: s for s in chosen["stops"]}
    assert set(by_location) == {shops["north"]["id"], shops["stall"]["id"]}
    return {
        "plan": plan,
        "variant": chosen,
        "products": products,
        "items": items,
        "north": by_location[shops["north"]["id"]]["seq"],
        "stall": by_location[shops["stall"]["id"]]["seq"],
    }


async def choose(client: httpx.AsyncClient, trip: dict) -> dict:
    r = await client.post(
        f"{TRIPS}/{trip['plan']['id']}/choose", json={"variant_id": trip["variant"]["id"]}
    )
    assert r.status_code == 200, r.text
    return r.json()


def stop_url(trip: dict, seq: int, tail: str = "") -> str:
    return f"{TRIPS}/{trip['plan']['id']}/stops/{seq}{tail}"


async def screen(client: httpx.AsyncClient, trip: dict, seq: int) -> dict:
    r = await client.get(stop_url(trip, seq))
    assert r.status_code == 200, r.text
    return r.json()


def stop_item(body: dict, trip: dict, name: str) -> dict:
    list_item_id = trip["items"][name]["id"]
    return next(i for i in body["items"] if i["list_item_id"] == list_item_id)


async def done(
    client: httpx.AsyncClient, trip: dict, seq: int, checked: list[str], *, key: str | None = None
) -> httpx.Response:
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    return await client.post(
        stop_url(trip, seq, "/done"), json={"checked_item_ids": checked}, headers=headers
    )


async def observation_count(conn: asyncpg.Connection, **where) -> int:
    clauses = " AND ".join(f"{k} = ${n}" for n, k in enumerate(where, start=1)) or "true"
    return await conn.fetchval(
        f"SELECT count(*) FROM price_observation WHERE {clauses}", *where.values()
    )


# --- criterion 22: the stop screen, "Cheaper at stop {m}" and "Get here" -------------------


async def test_the_stop_screen_lists_its_items_and_the_cheaper_ones_elsewhere(
    admin_client: httpx.AsyncClient,
    trip: dict,  # noqa: F811
    shops: dict,  # noqa: F811
):
    r = await admin_client.get(stop_url(trip, trip["north"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "plan_not_chosen"
    await choose(admin_client, trip)

    body = await screen(admin_client, trip, trip["north"])
    assert body["seq"] == trip["north"] and body["stop_count"] == 2
    assert body["plan_status"] == "planned" and body["location"]["name"] == "Harbor Chain north"
    assert body["home_base_name"] == "Harbor kitchen" and body["arrive_at"]
    assert sorted(i["label"] for i in body["items"]) == ["Barley", "Lentils", "Oats", "Rice"]
    barley = stop_item(body, trip, "Barley")
    assert (barley["packs"], barley["pack_price"], barley["line_cost"]) == ("1", "4.0000", "4.0000")
    assert barley["price_stale"] is False and barley["edited"] is False
    assert barley["checked_at"] is None and barley["got_here"] is False
    assert body["predicted_cost"] == "16.0000" and body["estimated_here"] == "16.0000"
    assert body["next_seq"] == trip["stall"] and body["done_at"] is None

    (millet,) = body["cheaper_elsewhere"]
    assert millet["list_item_id"] == trip["items"]["Millet"]["id"]
    assert millet["at_seq"] == trip["stall"] and millet["at_location_name"] == "Pier stall"
    assert (millet["line_cost_there"], millet["line_cost_here"]) == ("3.0000", "5.0000")
    assert millet["difference"] == "2.0000"

    # The stall's screen has no "cheaper" row: nothing at north is sold there.
    stall = await screen(admin_client, trip, trip["stall"])
    assert [i["label"] for i in stall["items"]] == ["Millet"] and stall["cheaper_elsewhere"] == []
    assert (await admin_client.get(stop_url(trip, 3))).status_code == 404

    # "Get here" moves millet to north at north's price.
    r = await admin_client.post(
        stop_url(trip, trip["north"], "/get-here"), json={"item_id": millet["id"]}
    )
    assert r.status_code == 200, r.text
    moved = stop_item(r.json(), trip, "Millet")
    assert moved["got_here"] is True and moved["line_cost"] == "5.0000"
    assert moved["pack_price"] == "5.0000" and r.json()["cheaper_elsewhere"] == []
    assert r.json()["estimated_here"] == "21.0000"
    assert (await screen(admin_client, trip, trip["stall"]))["items"] == []
    again = await admin_client.post(
        stop_url(trip, trip["stall"], "/get-here"), json={"item_id": str(uuid.uuid4())}
    )
    assert again.status_code == 404


async def test_get_here_without_a_price_here_needs_one_and_takes_it(
    admin_client: httpx.AsyncClient,
    trip: dict,  # noqa: F811
):
    await choose(admin_client, trip)
    north = await screen(admin_client, trip, trip["north"])
    oats = stop_item(north, trip, "Oats")
    url = stop_url(trip, trip["stall"], "/get-here")
    r = await admin_client.post(url, json={"item_id": oats["id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_offered_here"
    r = await admin_client.post(url, json={"item_id": oats["id"], "price": "3.75"})
    assert r.status_code == 200, r.text
    moved = stop_item(r.json(), trip, "Oats")
    assert moved["edited"] is True and moved["actual_price"] == "3.7500"
    assert moved["effective_cost"] == "3.7500" and moved["got_here"] is True


# --- criteria 23 to 25: "Done here" ---------------------------------------------------------


async def test_done_here_records_one_purchase_and_observes_bought_lines_through_it(
    admin_client: httpx.AsyncClient,
    trip: dict,  # noqa: F811
    shops: dict,  # noqa: F811
    app_conn: asyncpg.Connection,
):
    await choose(admin_client, trip)
    north = await screen(admin_client, trip, trip["north"])
    ids = {
        name: stop_item(north, trip, name)["id"] for name in ("Barley", "Oats", "Rice", "Lentils")
    }
    # A claim on rice, cleared by the check.
    r = await admin_client.post(f"{ITEMS}/{trip['items']['Rice']['id']}/claim")
    assert r.status_code == 200, r.text

    # Barley read at 4.50 and bought; lentils read at 2.50 and left on the shelf.
    for name, price in (("Barley", "4.50"), ("Lentils", "2.50")):
        r = await admin_client.put(
            stop_url(trip, trip["north"], f"/items/{ids[name]}/price"), json={"price": price}
        )
        assert r.status_code == 200, r.text
    edited = stop_item(r.json(), trip, "Barley")
    assert edited["edited"] is True and edited["effective_cost"] == "4.5000"
    # An edit put back and made again is still one edit.
    url = stop_url(trip, trip["north"], f"/items/{ids['Oats']}/price")
    assert (await admin_client.put(url, json={"price": "9.99"})).status_code == 200
    cleared = await admin_client.delete(url)
    assert stop_item(cleared.json(), trip, "Oats")["edited"] is False

    before = await observation_count(app_conn)
    key = str(uuid.uuid4())
    checked = [ids["Barley"], ids["Oats"], ids["Rice"]]
    r = await done(admin_client, trip, trip["north"], checked, key=key)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "underway" and out["next_seq"] == trip["stall"]
    assert out["lines_recorded"] == 3 and out["checked"] == 3
    assert len(out["observation_ids"]) == 3 and len(out["shelf_observation_ids"]) == 1

    # Criterion 23: one committed manual purchase, linked, three lines, three observations.
    purchase = (await admin_client.get(f"/api/v1/purchases/{out['purchase_id']}")).json()
    assert (purchase["source"], purchase["status"]) == ("manual", "committed")
    assert purchase["trip_plan_id"] == trip["plan"]["id"]
    assert purchase["vendor_location"]["id"] == shops["north"]["id"]
    lines = {line["product"]["id"]: line for line in purchase["lines"]}
    barley = lines[trip["products"]["Barley"]["id"]]
    assert barley["line_total"] == "4.5000" and "planned_price" not in barley["flags"]
    for name in ("Oats", "Rice"):
        line = lines[trip["products"][name]["id"]]
        assert line["line_total"] == "4.0000" and "planned_price" in line["flags"]
    assert trip["products"]["Lentils"]["id"] not in lines
    line_ids = [uuid.UUID(line["id"]) for line in purchase["lines"]]
    assert (
        await app_conn.fetchval(
            "SELECT count(*) FROM price_observation WHERE purchase_line_id = ANY($1)", line_ids
        )
        == 3
    )

    # Criterion 24: exactly one shelf observation, for the edited lentils; none for barley.
    assert await observation_count(app_conn) == before + 4
    shelf_rows = await app_conn.fetch(
        "SELECT product_id, price FROM price_observation WHERE source = 'shelf' AND id = ANY($1)",
        [uuid.UUID(i) for i in out["shelf_observation_ids"]],
    )
    assert [(str(r["product_id"]), str(r["price"])) for r in shelf_rows] == [
        (trip["products"]["Lentils"]["id"], "2.5000")
    ]

    # The list: three checked (rice's claim cleared), lentils and millet still open.
    listing = (
        await admin_client.get(
            "/api/v1/plan/list", params={"home_base_id": trip["plan"]["home_base_id"]}
        )
    ).json()
    assert listing["status"] == "open"
    checked_names = {i["id"] for i in listing["checked"]}
    assert checked_names == {trip["items"][n]["id"] for n in ("Barley", "Oats", "Rice")}
    assert all(i["claimed_by"] is None and i["checked_by"] for i in listing["checked"])
    assert {i["id"] for i in listing["unchecked"]} == {
        trip["items"][n]["id"] for n in ("Lentils", "Millet")
    }

    # The same key replays and changes nothing; a new key on a done stop is a 409.
    replay = await done(admin_client, trip, trip["north"], checked, key=key)
    assert replay.status_code == 200 and replay.json() == out
    assert replay.headers.get("Idempotent-Replayed") == "true"
    assert await observation_count(app_conn) == before + 4
    twice = await done(admin_client, trip, trip["north"], checked)
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "stop_done"
    price_after = await admin_client.put(
        stop_url(trip, trip["north"], f"/items/{ids['Lentils']}/price"), json={"price": "1.00"}
    )
    assert price_after.status_code == 409

    # The trip page: predicted beside actual once a stop is done.
    plan = (await admin_client.get(f"{TRIPS}/{trip['plan']['id']}")).json()
    assert plan["status"] == "underway"
    assert plan["predicted_item_cost"] == trip["variant"]["item_cost"] == "19.0000"
    assert plan["actual_item_cost"] == "12.5000"
    stops = {
        s["seq"]: s
        for s in next(v for v in plan["variants"] if v["id"] == trip["variant"]["id"])["stops"]
    }
    assert stops[trip["north"]]["actual_cost"] == "12.5000"
    assert stops[trip["north"]]["lines_recorded"] == 3
    assert stops[trip["north"]]["purchase_id"] == out["purchase_id"]
    assert stops[trip["stall"]]["actual_cost"] is None
    done_screen = await screen(admin_client, trip, trip["north"])
    assert done_screen["done_at"] and done_screen["actual_cost"] == "12.5000"
    assert stop_item(done_screen, trip, "Barley")["checked_at"] is not None

    # The last stop: millet bought at the plan's price, the plan done.
    stall = await screen(admin_client, trip, trip["stall"])
    last = await done(admin_client, trip, trip["stall"], [stall["items"][0]["id"]])
    assert last.status_code == 200, last.text
    assert last.json()["status"] == "done" and last.json()["next_seq"] is None
    plan = (await admin_client.get(f"{TRIPS}/{trip['plan']['id']}")).json()
    assert plan["status"] == "done" and plan["finished_at"] is not None
    assert plan["actual_item_cost"] == "15.5000"

    # GET /purchases?trip_plan_id= keeps the plan's two purchases.
    found = await admin_client.get("/api/v1/purchases", params={"trip_plan_id": trip["plan"]["id"]})
    assert found.status_code == 200, found.text
    assert {p["id"] for p in found.json()["items"]} == {
        out["purchase_id"],
        last.json()["purchase_id"],
    }
    none = await admin_client.get("/api/v1/purchases", params={"trip_plan_id": str(uuid.uuid4())})
    assert none.json()["items"] == []
    ended = await done(admin_client, trip, trip["stall"], [])
    assert ended.status_code == 409 and ended.json()["error"]["code"] == "plan_ended"


async def test_a_stop_with_nothing_checked_is_done_without_a_purchase(
    admin_client: httpx.AsyncClient,
    trip: dict,  # noqa: F811
    app_conn: asyncpg.Connection,  # noqa: F811
):
    r = await done(admin_client, trip, trip["north"], [])
    assert r.status_code == 409 and r.json()["error"]["code"] == "plan_not_chosen"
    await choose(admin_client, trip)
    before = await observation_count(app_conn)
    r = await done(admin_client, trip, trip["stall"], [])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["purchase_id"] is None and out["lines_recorded"] == 0
    assert out["status"] == "underway" and out["next_seq"] == trip["north"]
    assert await observation_count(app_conn) == before
    plan = (await admin_client.get(f"{TRIPS}/{trip['plan']['id']}")).json()
    assert plan["actual_item_cost"] == "0.0000"
    north = await screen(admin_client, trip, trip["north"])
    unknown = await done(admin_client, trip, trip["north"], [str(uuid.uuid4())])
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unknown_item"
    # A stall item is no longer movable once its stop is done.
    assert north["cheaper_elsewhere"] == []


async def test_the_stop_endpoints_take_a_session_only(client: httpx.AsyncClient):
    base = f"{TRIPS}/{uuid.uuid4()}/stops/1"
    assert (await client.get(base)).status_code == 401
    assert (await client.post(f"{base}/done", json={})).status_code == 401
    assert (await client.post(f"{base}/get-here", json={})).status_code == 401
    assert (await client.put(f"{base}/items/{uuid.uuid4()}/price", json={})).status_code == 401
