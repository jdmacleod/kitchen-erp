"""Keep times and best-by dates (2Q, docs/spec/15): acceptance criteria Q1-Q7."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.catalog import keep
from app.catalog.standard import standard_list
from app.core.db import get_sessionmaker
from app.services import ingredient_keep_times as catchup
from tests.catalog_helpers import make_ingredient, make_product
from tests.pricebook_helpers import make_location

# 10:00 on 3 June in the household's zone (America/Los_Angeles in tests).
BOUGHT = datetime(2026, 6, 3, 17, 0, tzinfo=UTC)


async def _bought(client, product_id: str, at: datetime = BOUGHT) -> dict:
    loc = await make_location(client, "Lantern Market", "Lantern Market")
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": at.isoformat(),
        "total": "6.00",
        "lines": [{"product_id": product_id, "qty": "1", "unit": "each", "line_total": "6.00"}],
    }
    r = await client.post("/api/v1/purchases", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _chicken(client) -> tuple[dict, dict]:
    chicken = await make_ingredient(client, "chicken breast", standard_key="chicken-breast")
    product = await make_product(client, chicken["id"], "Market chicken breast")
    return chicken, product


async def _keeping(client, purchase: dict, **body) -> dict:
    line = purchase["lines"][0]["id"]
    r = await client.put(f"/api/v1/purchases/{purchase['id']}/lines/{line}/keeping", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- Q1: the standard list ---------------------------------------------------


def test_every_standard_entry_carries_keep_times():
    by_key = {e.key: e.keep for e in standard_list().ingredients}
    # FSIS and FoodSafety.gov, low end of each range.
    assert by_key["chicken-breast"] == keep.KeepTimes(room=None, fridge=1, freezer=270)
    assert by_key["ground-beef"].fridge == 1
    assert by_key["pork-chop"].fridge == 3
    assert by_key["egg"].fridge == 21
    assert by_key["bacon"].fridge == 7
    # UC ANR 8406: nuts a few months on the shelf, a year cold, two frozen.
    assert by_key["walnuts"] == keep.KeepTimes(room=90, fridge=365, freezer=730)
    assert by_key["frozen-mixed-vegetables"].freezer == 240


@pytest.mark.parametrize("days", [-1, 1.5, "7"])
def test_a_negative_or_fractional_keep_time_is_refused(days):
    with pytest.raises(ValidationError):
        keep.KeepTimes(room=None, fridge=days, freezer=None)


def test_stored_in_follows_perishability():
    assert keep.stored_in("shelf_stable") == "room"
    assert keep.stored_in("shelf_months") == "room"
    assert keep.stored_in("refrigerated") == "fridge"
    assert keep.stored_in("fresh") == "fridge"
    assert keep.stored_in("frozen") == "freezer"


# --- Q2: new ingredients and the catch-up -------------------------------------


async def test_a_new_ingredient_takes_its_entry_or_category_times(admin_client):
    chicken, _ = await _chicken(admin_client)
    assert (chicken["keep_room_days"], chicken["keep_fridge_days"]) == (None, 1)
    assert chicken["keep_freezer_days"] == 270
    assert chicken["stored_in"] == "fridge"
    # A name typed in: dairy is refrigerated, which keeps two weeks cold.
    typed = await make_ingredient(admin_client, "cultured curd", category="dairy")
    assert typed["perishability"] == "refrigerated"
    assert (typed["keep_room_days"], typed["keep_fridge_days"]) == (None, 14)


async def test_a_catch_up_never_overwrites_a_persons_time(admin_client):
    chicken, _ = await _chicken(admin_client)
    r = await admin_client.patch(
        f"/api/v1/ingredients/{chicken['id']}", json={"keep_fridge_days": 2}
    )
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as db:
        rows = {row["id"]: row for row in await catchup.plan(db)}
        row = rows[chicken["id"]]
        assert "fridge" not in row["proposed"]
        assert row["proposed"] == {"room": None, "freezer": 270}
        await catchup.apply(db, [{"id": chicken["id"], "fridge": 1}])
    # An approved review is a deliberate write, and settles that place.
    got = (await admin_client.get(f"/api/v1/ingredients/{chicken['id']}")).json()
    assert got["keep_fridge_days"] == 1
    async with get_sessionmaker()() as db:
        rows = {row["id"]: row for row in await catchup.plan(db)}
    assert rows[chicken["id"]]["proposed"] == {"room": None, "freezer": 270}


# --- Q3: inferred dates by place ---------------------------------------------


async def test_raw_chicken_in_the_fridge_is_best_by_the_next_day(admin_client):
    _, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    line = p["lines"][0]
    assert line["stored_in"] == "fridge"
    assert (line["best_by"], line["best_by_source"]) == ("2026-06-04", "inferred")
    moved = (await _keeping(admin_client, p, stored_in="freezer"))["lines"][0]
    assert moved["stored_in"] == "freezer"
    assert moved["best_by"] == (date(2026, 6, 3) + timedelta(days=270)).isoformat()
    assert moved["best_by_source"] == "inferred"


async def test_the_date_is_the_households_not_utcs(admin_client):
    _, product = await _chicken(admin_client)
    # 03:00 UTC on the 4th is the evening of the 3rd in the household's zone.
    p = await _bought(admin_client, product["id"], datetime(2026, 6, 4, 3, 0, tzinfo=UTC))
    assert p["lines"][0]["best_by"] == "2026-06-04"


# --- Q4 and Q5: printed dates and date corrections -----------------------------


async def _correct_date(client, p: dict, at: datetime) -> dict:
    body = {
        "vendor_location_id": p["vendor_location"]["id"],
        "purchased_at": at.isoformat(),
        "total": "6.00",
        "lines": [
            {
                "id": p["lines"][0]["id"],
                "product_id": p["lines"][0]["product"]["id"],
                "qty": "1",
                "unit": "each",
                "line_total": "6.00",
            }
        ],
    }
    r = await client.put(f"/api/v1/purchases/{p['id']}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_printed_use_by_replaces_the_inferred_date_and_stays(admin_client):
    _, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    printed = await _keeping(admin_client, p, date="use_by", best_by="2026-06-06")
    assert printed["lines"][0]["best_by"] == "2026-06-06"
    assert printed["lines"][0]["best_by_source"] == "printed"
    later = await _correct_date(admin_client, printed, datetime(2026, 6, 5, 17, tzinfo=UTC))
    assert later["lines"][0]["best_by"] == "2026-06-06"
    assert later["lines"][0]["best_by_source"] == "printed"


async def test_a_sell_by_date_leaves_the_inferred_one(admin_client):
    _, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    got = await _keeping(admin_client, p, date="sell_by", best_by="2026-06-09")
    assert (got["lines"][0]["best_by"], got["lines"][0]["best_by_source"]) == (
        "2026-06-04",
        "inferred",
    )


async def test_a_person_may_clear_the_date(admin_client):
    _, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    cleared = await _keeping(admin_client, p, date="clear")
    assert (cleared["lines"][0]["best_by"], cleared["lines"][0]["best_by_source"]) == (
        None,
        "person",
    )
    back = await _keeping(admin_client, cleared, date="infer")
    assert back["lines"][0]["best_by"] == "2026-06-04"


async def test_correcting_the_purchase_date_moves_inferred_dates(admin_client):
    _, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    later = await _correct_date(admin_client, p, datetime(2026, 6, 8, 17, tzinfo=UTC))
    assert later["lines"][0]["best_by"] == "2026-06-09"


async def test_a_keep_time_change_moves_inferred_dates(admin_client):
    chicken, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    r = await admin_client.patch(
        f"/api/v1/ingredients/{chicken['id']}", json={"keep_fridge_days": 2}
    )
    assert r.status_code == 200, r.text
    got = (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()
    assert got["lines"][0]["best_by"] == "2026-06-05"


# --- Q6: no keep time, no date -------------------------------------------------


async def test_no_keep_time_means_no_date(admin_client):
    rice = await make_ingredient(admin_client, "sushi rice blend", category="pantry")
    assert (rice["keep_room_days"], rice["keep_fridge_days"]) == (365, None)
    product = await make_product(admin_client, rice["id"], "Market rice")
    p = await _bought(admin_client, product["id"])
    assert p["lines"][0]["best_by"] == "2027-06-03"
    fridge = (await _keeping(admin_client, p, stored_in="fridge"))["lines"][0]
    assert (fridge["best_by"], fridge["best_by_source"]) == (None, None)


# --- Q7: prices are unaffected ---------------------------------------------------


async def test_prices_are_unaffected(admin_client):
    chicken, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    observation = p["lines"][0]["observation_id"]
    await _keeping(admin_client, p, stored_in="freezer")
    await _keeping(admin_client, p, date="use_by", best_by="2026-06-05")
    await admin_client.patch(f"/api/v1/ingredients/{chicken['id']}", json={"keep_fridge_days": 2})
    got = (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()
    assert got["lines"][0]["observation_id"] == observation
    r = await admin_client.get(f"/api/v1/price-observations/{observation}")
    assert r.status_code == 200, r.text
    assert r.json()["voided"] is False


async def test_a_use_by_without_a_date_is_refused(admin_client):
    _, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    line = p["lines"][0]["id"]
    r = await admin_client.put(
        f"/api/v1/purchases/{p['id']}/lines/{line}/keeping", json={"date": "use_by"}
    )
    assert r.status_code == 422


async def test_re_pointing_a_line_takes_the_new_ingredients_place_and_time(admin_client):
    _, product = await _chicken(admin_client)
    p = await _bought(admin_client, product["id"])
    rice = await make_ingredient(admin_client, "sushi rice blend", category="pantry")
    other = await make_product(admin_client, rice["id"], "Market rice")
    line = p["lines"][0]["id"]
    r = await admin_client.post(
        f"/api/v1/purchases/{p['id']}/lines/{line}/resolve", json={"product_id": other["id"]}
    )
    assert r.status_code == 200, r.text
    got = r.json()["lines"][0]
    assert (got["stored_in"], got["best_by"]) == ("room", "2027-06-03")
