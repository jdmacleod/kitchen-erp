"""Weekly flyers (18, 2U-1): sources, the weekly queue, reports and relevance (U1–U6, U10).

A stub helper speaks for the private ``kitchen-erp-products``. Every store, offer and
price is invented.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import asyncpg
import httpx
import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.schemas.products_interchange import FORMAT
from app.services import flyers
from tests.catalog_helpers import make_ingredient
from tests.geo_helpers import make_home_base
from tests.pricebook_helpers import SYNTH, make_location, make_product
from tests.test_products_helper import helper  # noqa: F401  the stub helper's client

# A Wednesday, and the ad week it starts for a Wednesday source.
WEDNESDAY = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)
WEEK = "2026-11-04"


# --- helpers ------------------------------------------------------------------------------


async def queue(now: datetime = WEDNESDAY) -> int:
    async with get_sessionmaker()() as db:
        return await flyers.queue_flyer_runs(db, now)


async def seed(rows: list[dict]) -> flyers.ImportReport:
    async with get_sessionmaker()() as db:
        return await flyers.import_sources(db, flyers.SeedFile.model_validate({"sources": rows}))


def source_row(location: dict, **extra) -> dict:
    return {
        "location": location["key"],
        "mechanism": "itemized",
        "adapter": "example_itemized",
        "store_ref": {"store": "0042"},
        "publish_weekday": 3,
        "enabled": True,
        **extra,
    }


async def requests(helper_client: httpx.AsyncClient) -> list[dict]:
    r = await helper_client.get("/api/v1/lookup-requests", headers=helper_client.read_headers)
    assert r.status_code == 200, r.text
    return [i for i in r.json()["items"] if i["kind"] == "flyer"]


def candidate(name: str, price: str = "2.49", **extra) -> dict:
    return {
        "name_as_printed": name,
        "price_basis": "each",
        "price": price,
        "valid_from": "2026-11-04",
        "valid_to": "2026-11-10",
        "confidence": "0.9",
        **extra,
    }


async def report(
    helper_client: httpx.AsyncClient,
    request_id: str,
    *,
    status: str = "ok",
    candidates: list[dict] | None = None,
    **extra,
) -> httpx.Response:
    body = {
        "format": FORMAT,
        "request_id": request_id,
        "status": status,
        "adapter_version": "itemized-1",
        "fetched_at": "2026-11-04T12:00:00Z",
        **({"ad_week_from": "2026-11-04", "ad_week_to": "2026-11-10"} if status == "ok" else {}),
        "candidates": candidates or [],
        **extra,
    }
    return await helper_client.post(
        "/api/v1/flyer-reports",
        content=json.dumps(body),
        headers={**helper_client.suggest_headers, "Content-Type": "application/json"},
    )


@pytest.fixture
async def store(admin_client) -> dict:
    """An invented store in an invented kitchen, publishing an itemized circular on Wednesdays."""
    await make_home_base(admin_client, "Larkspur kitchen", SYNTH[0])
    location = await make_location(admin_client, "Larkspur Market", "Larkspur Market")
    assert (await seed([source_row(location)])).created == 1
    return location


async def bought(admin_client, location: dict, ingredient: str, product: str) -> dict:
    made = await make_product(admin_client, ingredient, product)
    r = await admin_client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": location["id"],
            "purchased_at": (WEDNESDAY - timedelta(days=30)).isoformat(),
            "lines": [{"product_id": made["id"], "qty": "1", "unit": "each", "unit_price": "3"}],
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201, r.text
    return made


# --- U1: the tables -----------------------------------------------------------------------


async def test_runs_and_offers_are_append_only(store, helper, app_conn: asyncpg.Connection):  # noqa: F811
    assert await queue() == 1
    [request] = await requests(helper)
    r = await report(helper, request["id"], candidates=[candidate("Watched Lentils")])
    assert r.status_code == 200, r.text
    for table in ("flyer_run", "flyer_offer"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(f"UPDATE {table} SET created_at = now()")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(f"DELETE FROM {table}")


# --- U2: the registry ---------------------------------------------------------------------


async def test_importing_the_same_seed_twice_changes_nothing(admin_client, store):
    again = await seed([source_row(store)])
    assert (again.created, again.updated, again.unchanged) == (0, 0, 1)
    moved = await seed([source_row(store, publish_weekday=4)])
    assert (moved.created, moved.updated) == (0, 1)


async def test_an_unmatched_location_is_reported_and_skipped(admin_client, store):
    out = await seed([source_row({"key": "nowhere/nothing"})])
    assert out.unmatched == ["nowhere/nothing"] and out.created == 0


async def test_an_image_source_asks_before_using_its_offers_by_default(admin_client):
    location = await make_location(admin_client, "Tideline Grocer", "Tideline Grocer")
    await seed([source_row(location, mechanism="image")])
    async with get_sessionmaker()() as db:
        [(confirm,)] = (
            await db.execute(
                text("SELECT require_confirmation FROM ad_source WHERE adapter = :a"),
                {"a": "example_itemized"},
            )
        ).all()
    assert confirm is True


# --- U3: the weekly queue -----------------------------------------------------------------


async def test_nothing_is_queued_without_a_helper(store):
    assert await queue() == 0


async def test_one_request_per_due_source_with_what_the_helper_needs(store, helper):  # noqa: F811
    assert await queue() == 1
    assert await queue() == 0  # one open request at a time
    [request] = await requests(helper)
    assert request["value"] == WEEK
    assert request["flyer"] == {
        "mechanism": "itemized",
        "adapter": "example_itemized",
        "store_ref": {"store": "0042"},
        "tiles": None,
        "ad_week": WEEK,
    }


async def test_the_ad_week_starts_on_the_publication_day(store, helper):  # noqa: F811
    tuesday = WEDNESDAY + timedelta(days=6)
    assert await queue(tuesday) == 1
    [request] = await requests(helper)
    assert request["value"] == WEEK  # still the week that began the Wednesday before


async def test_nothing_is_queued_while_a_receipt_is_being_read(store, helper, monkeypatch):  # noqa: F811
    async def busy(_db) -> bool:
        return True

    monkeypatch.setattr(flyers, "reading_receipts", busy)
    assert await queue() == 0


async def test_a_read_week_is_not_read_again_until_the_next(store, helper):  # noqa: F811
    await queue()
    [request] = await requests(helper)
    assert (await report(helper, request["id"], status="empty")).status_code == 200
    assert await queue(WEDNESDAY + timedelta(days=2)) == 0
    assert await queue(WEDNESDAY + timedelta(days=7)) == 1


async def test_a_failing_week_gets_three_tries_a_day_apart(store, helper):  # noqa: F811
    now = WEDNESDAY
    for _ in range(flyers.ATTEMPTS_PER_WEEK):
        assert await queue(now) == 1
        [request] = await requests(helper)
        r = await report(helper, request["id"], status="failed", reason="The page didn't load.")
        assert r.status_code == 200, r.text
        assert await queue(now + timedelta(hours=2)) == 0  # not before a day has passed
        now += timedelta(days=1)
    assert await queue(now) == 0  # the week has had its tries


async def test_two_failed_weeks_pause_a_source_and_hide_it(store, helper, owner_conn):  # noqa: F811
    for week in range(2):
        start = WEDNESDAY + timedelta(days=7 * week)
        r = await queue(start)
        assert r == 1
        [request] = await requests(helper)
        await report(helper, request["id"], status="not_allowed", reason="robots.txt says no.")
    paused = await owner_conn.fetchval("SELECT paused_until FROM ad_source")
    assert paused is not None and paused > datetime.now(UTC)
    # A request queued before the pause is held back from the helper.
    await owner_conn.execute("UPDATE ad_source SET paused_until = NULL")
    assert await queue(WEDNESDAY + timedelta(days=14)) == 1
    await owner_conn.execute("UPDATE ad_source SET paused_until = now() + interval '7 days'")
    assert await requests(helper) == []


async def test_a_turned_off_source_is_not_queued(store, helper, owner_conn):  # noqa: F811
    await owner_conn.execute("UPDATE ad_source SET enabled = false")
    assert await queue() == 0


# --- U4, U5: reports ----------------------------------------------------------------------


async def test_a_report_keeps_its_run_and_the_offers_that_matter(
    admin_client,
    store,
    helper,  # noqa: F811
    owner_conn,
):
    await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await queue()
    [request] = await requests(helper)
    r = await report(
        helper,
        request["id"],
        candidates=[
            candidate("Green Lentils", size_qty="16", size_unit="oz"),
            candidate("Garden Hose 50 ft", price="19.99"),
            candidate(
                "Sparkling Water",
                price="5",
                price_basis="multi",
                basis_qty="3",
                requires_loyalty=True,
            ),
        ],
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["status"], out["offered"], out["relevant"]) == ("ok", 3, 1)
    offer = await owner_conn.fetchrow("SELECT * FROM flyer_offer")
    assert out["offer_ids"] == [str(offer["id"]), None, None]
    assert (offer["name_as_printed"], offer["relevance"], str(offer["size_qty"])) == (
        "Green Lentils",
        "purchased",
        "16",
    )
    assert offer["valid_from"] == date(2026, 11, 4)
    assert await owner_conn.fetchval("SELECT status FROM lookup_request") == "answered"


async def test_an_invalid_report_is_refused_and_counts_as_a_failed_try(
    store,
    helper,  # noqa: F811
    owner_conn,
):
    await queue()
    [request] = await requests(helper)
    bad = candidate("Green Lentils", price_basis="multi")  # multi without basis_qty
    r = await report(helper, request["id"], candidates=[bad])
    assert r.status_code == 422
    assert await owner_conn.fetchval("SELECT count(*) FROM flyer_run") == 0
    assert await owner_conn.fetchval("SELECT count(*) FROM flyer_offer") == 0
    assert await owner_conn.fetchval("SELECT status FROM lookup_request") == "answered"
    refused = await owner_conn.fetchrow("SELECT request_id, outcome FROM lookup_answer")
    assert (str(refused["request_id"]), refused["outcome"]) == (request["id"], "refused")
    # It was this week's first try: the next comes a day later.
    assert await queue(WEDNESDAY + timedelta(hours=2)) == 0
    assert await queue(WEDNESDAY + timedelta(days=1)) == 1


async def test_a_report_naming_no_request_is_refused_and_recorded(
    store,
    helper,  # noqa: F811
    owner_conn,
):
    r = await report(helper, str(uuid.uuid4()), candidates=[{"name_as_printed": "x"}])
    assert r.status_code == 422
    assert await owner_conn.fetchval("SELECT request_id FROM lookup_answer") is None


async def test_a_report_on_a_closed_request_is_refused(store, helper, owner_conn):  # noqa: F811
    await queue()
    [request] = await requests(helper)
    await owner_conn.execute("UPDATE lookup_request SET status = 'closed'")
    assert (await report(helper, request["id"], status="empty")).status_code == 409


async def test_a_replay_with_a_newer_adapter_is_a_new_run(admin_client, store, helper, owner_conn):  # noqa: F811
    await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await queue()
    [request] = await requests(helper)
    await report(helper, request["id"], candidates=[candidate("Green Lentils", price="2.49")])
    again = await report(
        helper,
        request["id"],
        candidates=[candidate("Green Lentils", price="2.29")],
        adapter_version="itemized-2",
    )
    assert again.status_code == 200, again.text
    runs = await owner_conn.fetch("SELECT adapter_version FROM flyer_run ORDER BY created_at")
    assert [r["adapter_version"] for r in runs] == ["itemized-1", "itemized-2"]


async def test_a_replay_neither_uses_up_tries_nor_moves_the_pause(store, helper, owner_conn):  # noqa: F811
    await queue()
    [request] = await requests(helper)
    for _ in range(flyers.ATTEMPTS_PER_WEEK):
        await report(helper, request["id"], status="failed", reason="The page didn't load.")
    assert await queue(WEDNESDAY + timedelta(days=1)) == 1  # still the week's second try
    [second] = await requests(helper)
    await report(helper, second["id"], status="not_allowed", reason="robots.txt says no.")
    await report(helper, second["id"], status="not_allowed", reason="robots.txt says no.")
    source = await owner_conn.fetchrow("SELECT failures, paused_until FROM ad_source")
    assert (source["failures"], source["paused_until"]) == (1, None)


async def test_a_size_in_an_unknown_unit_is_dropped_not_the_offer(
    admin_client,
    store,
    helper,  # noqa: F811
    owner_conn,
):
    await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await queue()
    [request] = await requests(helper)
    r = await report(
        helper,
        request["id"],
        candidates=[candidate("Green Lentils", size_qty="2", size_unit="sacks")],
    )
    assert r.status_code == 200, r.text
    assert await owner_conn.fetchval("SELECT size_unit FROM flyer_offer") is None


# --- U6: relevance ------------------------------------------------------------------------


async def test_each_reason_an_offer_matters_and_the_rest_are_counted(
    admin_client,
    store,
    helper,  # noqa: F811
    owner_conn,
):
    await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    barley = await make_ingredient(admin_client, "Pearl barley")
    recipe_id = uuid.uuid4()
    await owner_conn.execute(
        "INSERT INTO recipe (id, path, title, content_hash, dirty, status, "
        "last_indexed_at, last_seen_at, created_at, updated_at) "
        "VALUES ($1, 'stew.cook', 'Stew', 'abc', false, 'ok', now(), now(), now(), now())",
        recipe_id,
    )
    await owner_conn.execute(
        "INSERT INTO recipe_ingredient (id, recipe_id, seq, raw_name, name_norm, qty_kind, "
        "resolution, negligible, yield_mode, ingredient_id) "
        "VALUES ($1, $2, 1, 'pearl barley', 'pearl barley', 'none', 'manual', false, 'auto', $3)",
        uuid.uuid4(),
        recipe_id,
        uuid.UUID(barley["id"]),
    )
    kitchen = (await admin_client.get("/api/v1/home-bases")).json()["items"][0]
    tahini = await make_ingredient(admin_client, "Tahini")
    r = await admin_client.post(
        "/api/v1/plan/list/items",
        json={"home_base_id": kitchen["id"], "kind": "ingredient", "ingredient_id": tahini["id"]},
    )
    assert r.status_code == 201, r.text
    saffron = await make_ingredient(admin_client, "Saffron")
    await owner_conn.execute(
        "UPDATE ingredient SET watch = true WHERE id = $1", uuid.UUID(saffron["id"])
    )
    vendor_id = uuid.UUID(store["vendor"]["id"])
    for name, product in (("tinned figs", True), ("garden hose 50 ft", False)):
        await owner_conn.execute(
            "INSERT INTO flyer_alias (id, vendor_id, name_norm, product_id, ignore) "
            "VALUES ($1, $2, $3, (SELECT id FROM product LIMIT 1), false)"
            if product
            else "INSERT INTO flyer_alias (id, vendor_id, name_norm, product_id, ignore) "
            "VALUES ($1, $2, $3, NULL, true)",
            uuid.uuid4(),
            vendor_id,
            name,
        )
    await queue()
    [request] = await requests(helper)
    names = [
        "Tinned Figs",
        "Green Lentils",
        "Pearl Barley 2 lb",
        "Tahini, 16 oz jar",
        "Spanish Saffron",
        "Garden Hose 50 ft",
        "Motor Oil 5 qt",
    ]
    r = await report(helper, request["id"], candidates=[candidate(n) for n in names])
    assert r.status_code == 200, r.text
    assert (r.json()["offered"], r.json()["relevant"]) == (7, 5)
    rows = await owner_conn.fetch("SELECT name_as_printed, relevance FROM flyer_offer")
    assert {row["name_as_printed"]: row["relevance"] for row in rows} == {
        "Tinned Figs": "alias",
        "Green Lentils": "purchased",
        "Pearl Barley 2 lb": "recipe",
        "Tahini, 16 oz jar": "list",
        "Spanish Saffron": "watched",
    }


async def test_a_removed_line_is_not_a_purchase(admin_client, store, helper, owner_conn):  # noqa: F811
    await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    await owner_conn.execute(
        "UPDATE purchase_line SET removed_at = now(), "
        "removed_by = (SELECT id FROM app_user LIMIT 1)"
    )
    await queue()
    [request] = await requests(helper)
    r = await report(helper, request["id"], candidates=[candidate("Green Lentils")])
    assert r.json()["relevant"] == 0


async def test_accented_names_match_however_they_are_printed(
    admin_client,
    store,
    helper,  # noqa: F811
    owner_conn,
):
    acai = await make_ingredient(admin_client, "Açaí")
    await owner_conn.execute(
        "UPDATE ingredient SET watch = true WHERE id = $1", uuid.UUID(acai["id"])
    )
    await queue()
    [request] = await requests(helper)
    r = await report(helper, request["id"], candidates=[candidate("Acai Puree")])
    assert r.json()["relevant"] == 1


async def test_a_name_never_matches_inside_a_longer_word(
    admin_client,
    store,
    helper,  # noqa: F811
    owner_conn,
):
    salt = await make_ingredient(admin_client, "Salt")
    await owner_conn.execute(
        "UPDATE ingredient SET watch = true WHERE id = $1", uuid.UUID(salt["id"])
    )
    await queue()
    [request] = await requests(helper)
    r = await report(
        helper,
        request["id"],
        candidates=[candidate("Garden Salad Kit"), candidate("Sea Salt Flakes")],
    )
    assert r.json()["offer_ids"][0] is None
    assert r.json()["relevant"] == 1


def test_names_fold_case_accents_and_punctuation():
    assert (
        flyers.flyer_relevance.normalize_name("  Jalapeño-Peppers, 1 LB!")
        == "jalapeno peppers 1 lb"
    )


# --- U10: an offer is never a price -------------------------------------------------------


async def test_offers_never_reach_the_price_book(admin_client, store, helper, owner_conn):  # noqa: F811
    await bought(admin_client, store, "Lentils", "Larkspur Green Lentils 1 lb")
    before = await owner_conn.fetchval("SELECT count(*) FROM price_observation")
    await queue()
    [request] = await requests(helper)
    await report(helper, request["id"], candidates=[candidate("Green Lentils", price="0.99")])
    assert await owner_conn.fetchval("SELECT count(*) FROM price_observation") == before
