"""Perishability: the four values, standard-list values, defaults, and the catch-up."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.catalog import perishability
from app.catalog.standard import parse, standard_list
from app.core.db import get_sessionmaker
from app.services import ingredient_perishability as catchup
from app.services.pricebook_views import stale_after_days
from tests.catalog_helpers import make_ingredient, seed_units_via_service

HEADER = "format: kitchen-erp-standard-ingredients/1\ningredients:\n"


def test_every_standard_entry_says_how_it_keeps():
    entries = standard_list().ingredients
    assert {e.perishability for e in entries} <= set(perishability.VALUES)
    by_key = {e.key: e.perishability for e in entries}
    # A few the USDA charts settle: raw poultry and milk keep days, eggs weeks,
    # canned and dry goods months, frozen vegetables are bought frozen.
    assert by_key["chicken-breast"] == "fresh"
    assert by_key["whole-milk"] == "fresh"
    assert by_key["egg"] == "refrigerated"
    assert by_key["bacon"] == "refrigerated"
    assert by_key["canned-tuna"] == "shelf_stable"
    assert by_key["all-purpose-flour"] == "shelf_stable"
    assert by_key["frozen-mixed-vegetables"] == "frozen"
    # Room temperature, but stale or rancid within months [FB-BC].
    assert by_key["walnuts"] == "shelf_months"
    assert by_key["tortilla-chips"] == "shelf_months"
    assert by_key["saltines"] == "shelf_months"
    assert by_key["olive-oil"] == "shelf_months"
    assert by_key["long-grain-white-rice"] == "shelf_stable"


def test_an_entry_without_perishability_is_refused():
    with pytest.raises(ValidationError):
        parse(HEADER + "  - key: oats\n    name: oats\n    category: pantry\n    unit: g\n")


def test_the_default_follows_the_category():
    assert perishability.default_for("Dairy") == "refrigerated"
    assert perishability.default_for("fish") == "fresh"
    assert perishability.default_for("frozen") == "frozen"
    assert perishability.default_for("canned") == "shelf_stable"
    assert perishability.default_for(None) == "shelf_stable"
    assert perishability.default_for("something new") == "shelf_stable"


def test_a_price_goes_stale_after_one_window_whatever_the_ingredient():
    assert stale_after_days() == 90


async def test_a_new_ingredient_takes_its_entry_or_category_value(admin_client, db_session):
    await seed_units_via_service(db_session)
    chicken = await make_ingredient(admin_client, "chicken breast", standard_key="chicken-breast")
    assert chicken["perishability"] == "fresh"
    yogurt = await make_ingredient(admin_client, "Strained yogurt", category="dairy")
    assert yogurt["perishability"] == "refrigerated"
    chosen = await make_ingredient(
        admin_client, "Frozen dumplings", category="dairy", perishability="frozen"
    )
    assert chosen["perishability"] == "frozen"


async def test_the_catch_up_proposes_unreviewed_values_and_writes_approved_ones(
    admin_client, db_session
):
    await seed_units_via_service(db_session)
    chicken = await make_ingredient(admin_client, "chicken breast", standard_key="chicken-breast")
    yogurt = await make_ingredient(admin_client, "Strained yogurt", category="dairy")
    picked = await make_ingredient(admin_client, "Ice", category="dairy", perishability="frozen")
    # A person changes the defaulted yogurt: from now on the value is theirs.
    r = await admin_client.patch(
        f"/api/v1/ingredients/{yogurt['id']}", json={"perishability": "fresh"}
    )
    assert r.status_code == 200, r.text

    async with get_sessionmaker()() as db:
        rows = {row["name"]: row for row in await catchup.plan(db)}
    assert set(rows) == {"chicken breast", "Ice"}
    assert rows["chicken breast"]["proposed"] == "fresh"
    assert rows["chicken breast"]["basis"] == "standard:chicken-breast"
    # Chosen at creation with no source behind it: shown for review, never changed here.
    assert rows["Ice"]["basis"] == "category:dairy" and rows["Ice"]["current"] == "frozen"

    async with get_sessionmaker()() as db:
        counts = await catchup.apply(
            db,
            [
                {"id": chicken["id"], "perishability": "fresh"},
                {"id": picked["id"], "perishability": "frozen"},
                {"id": str(uuid.uuid4()), "perishability": "fresh"},
            ],
        )
    assert counts == {"changed": 0, "unchanged": 2, "missing": 1}
    yogurt_now = (await admin_client.get(f"/api/v1/ingredients/{yogurt['id']}")).json()
    assert yogurt_now["perishability"] == "fresh"

    async with get_sessionmaker()() as db:
        with pytest.raises(ValueError):
            await catchup.apply(db, [{"id": chicken["id"], "perishability": "chilled"}])

    # Approved in review, even against the proposal: settled, not proposed again.
    async with get_sessionmaker()() as db:
        await catchup.apply(db, [{"id": chicken["id"], "perishability": "refrigerated"}])
        assert chicken["id"] not in {row["id"] for row in await catchup.plan(db)}
