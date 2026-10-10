"""Recompute triggers for cost snapshots (07, 3D; package 6b): criteria 24 and 25,
and the alias, pin, price, purchase and merge triggers.

Every recipe, vendor and product is invented. Prices: a 1 kg bag of barley at
$4.00 is $0.004 per g, so 200 g is $0.80; the sack at $6.00 makes it $1.20.

A trigger is observed through ``computed_at``: the snapshot of a recipe the
change reached carries a new one, an unrelated recipe's snapshot keeps its own.
``GET /cost`` only computes when the current content has no snapshot, and the
scan gives every recipe one, so the figures it returns here are the triggers'.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import httpx
import pytest

from app.core.db import get_sessionmaker
from app.services import recipe_cost_triggers, recipe_costing
from tests.pricebook_helpers import make_product, shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_recipe_costing import (
    cost,
    indexed,
    lines_by_name,
    market,
    recipe_by_path,
    rescan,
)

D = Decimal

BOWL = """---
title: Harbor bowl
---

Serve @barley{200%g}.
"""

SOUP = """---
title: Harbor soup
---

Simmer @barley{100%g} in water.
"""

CUPS = """---
title: Harbor cups
---

Grate @carrot{1%cup} over @barley{200%g}.
"""

ROOTS = """---
title: Harbor roots
---

Roast @dragon root{2} with @barley{100%g}.
"""


# --- helpers ------------------------------------------------------------------------------


async def computed_at(conn: asyncpg.Connection, recipe_id: str, basis: str = "latest"):
    """When the recipe's snapshot of its current content under ``basis`` was computed."""
    rows = await conn.fetch(
        "SELECT s.computed_at FROM recipe_cost_snapshot s JOIN recipe r ON r.id = s.recipe_id "
        "WHERE s.recipe_id = $1 AND s.basis = $2 AND s.content_hash = r.content_hash",
        uuid.UUID(recipe_id),
        basis,
    )
    assert len(rows) == 1, f"expected one current {basis} snapshot, found {len(rows)}"
    return rows[0]["computed_at"]


async def history(client: httpx.AsyncClient, recipe_id: str, **params) -> list[dict]:
    r = await client.get(f"/api/v1/recipes/{recipe_id}/cost/history", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def barley_bag(client: httpx.AsyncClient, loc: dict, price: str = "4.00") -> dict:
    bag = await make_product(client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(client, bag["id"], loc["id"], price)
    return bag


async def another_barley(client: httpx.AsyncClient, bag: dict, name: str, **extra) -> dict:
    r = await client.post(
        "/api/v1/products",
        json={"ingredient_id": bag["ingredient"]["id"], "name": name, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


def barley_line(body: dict) -> dict:
    return lines_by_name(body)["barley"]


# --- 24: provisional snapshots and the commit that clears them ------------------------------


async def test_24_a_dirty_file_costs_provisionally_and_committing_it_clears_the_flag(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    await barley_bag(admin_client, await market(admin_client))
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    first = await cost(admin_client, recipe["id"])
    assert first["provisional"] is False and first["totals"]["consumed_cost"] == "0.8000"
    assert [h["id"] for h in await history(admin_client, recipe["id"])] == [first["id"]]

    # An uncommitted edit: the scan costs the new content and marks it provisional.
    recipes_repo.edit("harbor-bowl.cook", "\nAdd @barley{100%g}.\n")
    await rescan(admin_client)
    listed = await recipe_by_path(admin_client, "harbor-bowl.cook")
    assert listed["dirty"] is True and listed["cost"]["provisional"] is True
    assert listed["cost"]["consumed_cost"] == "1.2000"
    dirty = await cost(admin_client, recipe["id"])
    assert dirty["provisional"] is True and dirty["content_hash"] != first["content_hash"]
    assert dirty["head_commit"] == first["head_commit"]
    assert [h["id"] for h in await history(admin_client, recipe["id"])] == [first["id"]]
    before = await computed_at(owner_conn, recipe["id"])

    # Committed unchanged: the flag clears on the next scan, without recomputation.
    sha = recipes_repo.commit("more barley")
    await rescan(admin_client)
    listed = await recipe_by_path(admin_client, "harbor-bowl.cook")
    assert listed["dirty"] is False and listed["cost"]["provisional"] is False
    committed = await cost(admin_client, recipe["id"])
    assert committed["id"] == dirty["id"] and committed["provisional"] is False
    assert committed["head_commit"] == sha
    assert await computed_at(owner_conn, recipe["id"]) == before, "no recomputation"
    assert [h["id"] for h in await history(admin_client, recipe["id"])] == [
        first["id"],
        dirty["id"],
    ]

    # Another edit: a third snapshot, provisional; the two committed ones stay.
    recipes_repo.edit("harbor-bowl.cook", "\nAdd @barley{100%g}.\n")
    await rescan(admin_client)
    third = await cost(admin_client, recipe["id"])
    assert third["provisional"] is True and third["totals"]["consumed_cost"] == "1.6000"
    assert third["id"] not in (first["id"], dirty["id"])
    assert [h["id"] for h in await history(admin_client, recipe["id"])] == [
        first["id"],
        dirty["id"],
    ]
    assert len(await owner_conn.fetch("SELECT id FROM recipe_cost_snapshot")) == 3


# --- 25: a density change reaches exactly the recipes using the ingredient -------------------


async def test_25_density_change_recomputes_exactly_the_recipes_using_the_ingredient(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    await barley_bag(admin_client, loc)
    carrot = await make_product(admin_client, "Carrot", "Carrot bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, carrot["id"], loc["id"], "2.00")  # $0.002 per g
    cups = await indexed(admin_client, recipes_repo, "harbor-cups.cook", CUPS)
    bowl = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    # The average key exists for the cups too: every key a recipe has is recomputed.
    await cost(admin_client, cups["id"], basis="average")
    body = await cost(admin_client, cups["id"])
    assert lines_by_name(body)["carrot"]["status"] == "unconvertible"
    assert lines_by_name(body)["carrot"]["failure_code"] == "no_density"
    cups_before = await computed_at(owner_conn, cups["id"])
    cups_avg_before = await computed_at(owner_conn, cups["id"], "average")
    bowl_before = await computed_at(owner_conn, bowl["id"])

    r = await admin_client.patch(
        f"/api/v1/ingredients/{carrot['ingredient']['id']}",
        json={"density_g_per_ml": "0.5", "density_source": "manual"},
    )
    assert r.status_code == 200, r.text

    assert await computed_at(owner_conn, cups["id"]) > cups_before
    assert await computed_at(owner_conn, cups["id"], "average") > cups_avg_before
    assert await computed_at(owner_conn, bowl["id"]) == bowl_before, "barley has no carrot line"
    body = await cost(admin_client, cups["id"])
    line = lines_by_name(body)["carrot"]
    # 1 cup = 236.5882365 ml × 0.5 g/ml = 118.29411825 g × $0.002 = $0.2366.
    assert line["status"] == "priced" and line["bridge_kind"] == "density"
    assert line["consumed_cost"] == "0.2366"
    assert body["totals"]["consumed_cost"] == "1.0366"

    # A measure is a bridge too, and confirming one moves the unconfirmed share.
    cups_before = await computed_at(owner_conn, cups["id"])
    bowl_before = await computed_at(owner_conn, bowl["id"])
    r = await admin_client.post(
        f"/api/v1/ingredients/{carrot['ingredient']['id']}/measures",
        json={"label": "bunch", "canonical_qty": "300", "source": "manual", "confirmed": False},
    )
    assert r.status_code == 201, r.text
    assert await computed_at(owner_conn, cups["id"]) > cups_before
    assert await computed_at(owner_conn, bowl["id"]) == bowl_before


# --- alias decisions ---------------------------------------------------------------------------


async def test_an_alias_decision_recomputes_the_recipes_using_the_name_and_no_other(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    await barley_bag(admin_client, await market(admin_client))
    roots = await indexed(admin_client, recipes_repo, "harbor-roots.cook", ROOTS)
    bowl = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    body = await cost(admin_client, roots["id"])
    assert body["completeness"]["lines_unmapped"] == 1
    roots_before = await computed_at(owner_conn, roots["id"])
    bowl_before = await computed_at(owner_conn, bowl["id"])

    r = await admin_client.post(
        "/api/v1/recipes/resolve",
        json={
            "name_norm": "dragon root",
            "ingredient": {"name": "Dragon root", "canonical_unit": "each"},
        },
    )
    assert r.status_code == 200, r.text
    assert await computed_at(owner_conn, roots["id"]) > roots_before
    assert await computed_at(owner_conn, bowl["id"]) == bowl_before
    body = await cost(admin_client, roots["id"])
    assert body["completeness"]["lines_unmapped"] == 0
    assert lines_by_name(body)["dragon root"]["status"] == "unpriced", "mapped, nothing sold"
    assert lines_by_name(body)["dragon root"]["line"]["ingredient_name"] == "Dragon root"


# --- pins --------------------------------------------------------------------------------------


async def test_setting_and_removing_a_pin_recomputes_that_recipe_only(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    bag = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    sack = await another_barley(admin_client, bag, "Barley sack", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, sack["id"], loc["id"], "6.00")
    await shelf(admin_client, bag["id"], loc["id"], "4.00")  # newer: the latest price
    bowl = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    soup = await indexed(admin_client, recipes_repo, "harbor-soup.cook", SOUP)
    assert (await cost(admin_client, bowl["id"]))["totals"]["consumed_cost"] == "0.8000"
    bowl_before = await computed_at(owner_conn, bowl["id"])
    soup_before = await computed_at(owner_conn, soup["id"])

    r = await admin_client.put(
        f"/api/v1/recipes/{bowl['id']}/pins/barley", json={"product_id": sack["id"]}
    )
    assert r.status_code == 200, r.text
    assert await computed_at(owner_conn, bowl["id"]) > bowl_before
    assert await computed_at(owner_conn, soup["id"]) == soup_before
    body = await cost(admin_client, bowl["id"])
    assert body["totals"]["consumed_cost"] == "1.2000"
    assert barley_line(body)["pinned"] is True
    assert barley_line(body)["price"]["product_id"] == sack["id"]

    bowl_before = await computed_at(owner_conn, bowl["id"])
    r = await admin_client.delete(f"/api/v1/recipes/{bowl['id']}/pins/barley")
    assert r.status_code == 204, r.text
    assert await computed_at(owner_conn, bowl["id"]) > bowl_before
    assert await computed_at(owner_conn, soup["id"]) == soup_before
    body = await cost(admin_client, bowl["id"])
    assert body["totals"]["consumed_cost"] == "0.8000" and barley_line(body)["pinned"] is False


# --- prices ------------------------------------------------------------------------------------


async def test_a_new_shelf_price_and_its_void_recompute_the_recipes_the_product_fulfils(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    bag = await barley_bag(admin_client, loc)
    parsnip = await make_product(
        admin_client, "Parsnip", "Parsnip bag", pack_qty="1", pack_unit="kg"
    )
    bowl = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    await cost(admin_client, bowl["id"], basis="cheapest")
    assert (await cost(admin_client, bowl["id"]))["totals"]["consumed_cost"] == "0.8000"
    before = await computed_at(owner_conn, bowl["id"])

    # A price of a product no line uses reaches nothing.
    await shelf(admin_client, parsnip["id"], loc["id"], "1.00")
    assert await computed_at(owner_conn, bowl["id"]) == before

    cheaper = await shelf(admin_client, bag["id"], loc["id"], "3.00")
    assert await computed_at(owner_conn, bowl["id"]) > before
    latest = await cost(admin_client, bowl["id"])
    assert latest["totals"]["consumed_cost"] == "0.6000"
    assert barley_line(latest)["price"]["observation_id"] == cheaper["id"]
    cheapest = await cost(admin_client, bowl["id"], basis="cheapest")
    assert cheapest["totals"]["consumed_cost"] == "0.6000", "every key the recipe has"

    before = await computed_at(owner_conn, bowl["id"])
    r = await admin_client.post(
        f"/api/v1/price-observations/{cheaper['id']}/void", json={"reason": "test: mistyped"}
    )
    assert r.status_code == 200, r.text
    assert await computed_at(owner_conn, bowl["id"]) > before
    assert (await cost(admin_client, bowl["id"]))["totals"]["consumed_cost"] == "0.8000"
    assert (await cost(admin_client, bowl["id"], basis="cheapest"))["totals"][
        "consumed_cost"
    ] == "0.8000"


async def test_a_purchase_prices_a_line_when_committed_and_stops_when_reopened(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    bag = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    bowl = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    assert barley_line(await cost(admin_client, bowl["id"]))["status"] == "unpriced"
    before = await computed_at(owner_conn, bowl["id"])

    # A manual purchase commits as it is created: its price qualifies at once.
    r = await admin_client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": loc["id"],
            "purchased_at": datetime.now(UTC).isoformat(),
            "lines": [{"product_id": bag["id"], "qty": "1", "unit": "each", "line_total": "4.00"}],
        },
        headers={"Idempotency-Key": "harbor-barley-trigger-1"},
    )
    assert r.status_code == 201, r.text
    purchase_id = r.json()["id"]
    assert await computed_at(owner_conn, bowl["id"]) > before
    body = await cost(admin_client, bowl["id"])
    assert barley_line(body)["status"] == "priced" and body["totals"]["consumed_cost"] == "0.8000"

    # Reopened, the price no longer qualifies (criterion 27a); committed again, it does.
    before = await computed_at(owner_conn, bowl["id"])
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/reopen")
    assert r.status_code == 200, r.text
    assert await computed_at(owner_conn, bowl["id"]) > before
    assert barley_line(await cost(admin_client, bowl["id"]))["status"] == "unpriced"

    before = await computed_at(owner_conn, bowl["id"])
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/commit")
    assert r.status_code == 200, r.text
    assert await computed_at(owner_conn, bowl["id"]) > before
    assert barley_line(await cost(admin_client, bowl["id"]))["status"] == "priced"


# --- merges ------------------------------------------------------------------------------------


async def test_a_product_merge_recomputes_the_recipes_whose_pins_it_repointed(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    bag = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    sack = await another_barley(admin_client, bag, "Barley sack", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, sack["id"], loc["id"], "6.00")
    await shelf(admin_client, bag["id"], loc["id"], "4.00")
    parsnip = await make_product(
        admin_client, "Parsnip", "Parsnip bag", pack_qty="1", pack_unit="kg"
    )
    await shelf(admin_client, parsnip["id"], loc["id"], "1.00")
    bowl = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    roots = await indexed(admin_client, recipes_repo, "harbor-roots.cook", ROOTS)
    r = await admin_client.put(
        f"/api/v1/recipes/{bowl['id']}/pins/barley", json={"product_id": sack["id"]}
    )
    assert r.status_code == 200, r.text
    assert (await cost(admin_client, bowl["id"]))["totals"]["consumed_cost"] == "1.2000"
    bowl_before = await computed_at(owner_conn, bowl["id"])
    roots_before = await computed_at(owner_conn, roots["id"])

    # The preview writes nothing, snapshots included.
    r = await admin_client.post(
        f"/api/v1/products/{sack['id']}/merge/preview", json={"survivor_id": bag["id"]}
    )
    assert r.status_code == 200, r.text
    assert await computed_at(owner_conn, bowl["id"]) == bowl_before

    r = await admin_client.post(
        f"/api/v1/products/{sack['id']}/merge", json={"survivor_id": bag["id"]}
    )
    assert r.status_code == 200, r.text
    assert await computed_at(owner_conn, bowl["id"]) > bowl_before
    # The roots use barley too, so the survivor's renormalized prices reach them.
    assert await computed_at(owner_conn, roots["id"]) > roots_before
    body = await cost(admin_client, bowl["id"])
    line = barley_line(body)
    assert line["pinned"] is True and line["price"]["product_id"] == bag["id"]
    # The pin names the survivor now, and the sack's price reports under it: the
    # bag's newer $4.00 is the latest.
    assert body["totals"]["consumed_cost"] == "0.8000"
    pins = (await admin_client.get(f"/api/v1/recipes/{bowl['id']}")).json()["pins"]
    assert [p["product_id"] for p in pins] == [bag["id"]]
    repointed = await owner_conn.fetchval(
        "SELECT count(*) FROM recipe_cost_line WHERE product_id = $1", uuid.UUID(sack["id"])
    )
    assert repointed == 0, "cost lines of the loser name the survivor"


async def test_an_ingredient_merge_recomputes_the_recipes_whose_lines_it_repointed(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    await barley_bag(admin_client, loc)
    # "Dragon root" is sold; the roots recipe names it "dragon root" and resolves
    # by name. Merging it into another ingredient repoints the recipe's line.
    sold = await make_product(
        admin_client, "Dragon root", "Dragon root bunch", canonical_unit="each"
    )
    await shelf(admin_client, sold["id"], loc["id"], "3.00", qty="1", unit="each")
    r = await admin_client.post(
        "/api/v1/ingredients", json={"name": "Wyrm root", "canonical_unit": "each"}
    )
    assert r.status_code == 201, r.text
    survivor = r.json()
    roots = await indexed(admin_client, recipes_repo, "harbor-roots.cook", ROOTS)
    bowl = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    body = await cost(admin_client, roots["id"])
    assert lines_by_name(body)["dragon root"]["status"] == "priced"
    roots_before = await computed_at(owner_conn, roots["id"])
    bowl_before = await computed_at(owner_conn, bowl["id"])

    r = await admin_client.post(
        "/api/v1/ingredients/merge",
        json={
            "survivor_id": survivor["id"],
            "loser_id": sold["ingredient"]["id"],
            "name": "Wyrm root",
            "copy_measures": [],
        },
    )
    assert r.status_code == 200, r.text
    assert await computed_at(owner_conn, roots["id"]) > roots_before
    assert await computed_at(owner_conn, bowl["id"]) == bowl_before
    body = await cost(admin_client, roots["id"])
    line = lines_by_name(body)["dragon root"]
    assert line["line"]["ingredient_id"] == survivor["id"]
    assert line["line"]["ingredient_name"] == "Wyrm root"
    assert line["status"] == "priced" and line["consumed_cost"] == "6.0000"


# --- no recipes, and a trigger that fails -------------------------------------------------------


async def test_the_hooks_are_no_ops_without_recipes(
    admin_client: httpx.AsyncClient, owner_conn: asyncpg.Connection
):
    loc = await market(admin_client)
    bag = await barley_bag(admin_client, loc)
    priced = await shelf(admin_client, bag["id"], loc["id"], "3.00")
    r = await admin_client.post(
        f"/api/v1/price-observations/{priced['id']}/void", json={"reason": "test: mistyped"}
    )
    assert r.status_code == 200, r.text
    r = await admin_client.patch(
        f"/api/v1/ingredients/{bag['ingredient']['id']}",
        json={"density_g_per_ml": "0.6", "density_source": "manual"},
    )
    assert r.status_code == 200, r.text
    r = await admin_client.patch(
        f"/api/v1/products/{bag['id']}", json={"pack_qty": "2", "pack_unit": "kg"}
    )
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as db:
        assert await recipe_cost_triggers.after_prices_changed(db, [uuid.UUID(priced["id"])]) == 0
        assert await recipe_cost_triggers.after_recipe_changed(db, uuid.uuid4()) == 0
        assert await recipe_cost_triggers.after_names_changed(db, ["barley"]) == 0
        assert await recipe_cost_triggers.after_lines_settled(db, frozenset()) == 0
        assert (
            await recipe_cost_triggers.after_ingredient_bridge_changed(
                db, uuid.UUID(bag["ingredient"]["id"])
            )
            == 0
        )
        assert (
            await recipe_cost_triggers.after_product_bridge_changed(db, uuid.UUID(bag["id"])) == 0
        )
        assert await recipe_cost_triggers.after_merge(db, []) == 0
        await db.rollback()
    assert await owner_conn.fetchval("SELECT count(*) FROM recipe_cost_snapshot") == 0


async def test_a_failing_recompute_is_logged_and_the_change_still_lands(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    loc = await market(admin_client)
    bag = await barley_bag(admin_client, loc)
    bowl = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    before = await computed_at(owner_conn, bowl["id"])

    async def boom(db, recipe_id):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(recipe_costing, "recompute_recipe_core", boom)
    with caplog.at_level("ERROR"):
        cheaper = await shelf(admin_client, bag["id"], loc["id"], "3.00")
    assert "recipe cost recompute failed" in caplog.text
    assert any(getattr(r, "recipe_id", None) == bowl["id"] for r in caplog.records)
    # The price is in the book and the snapshot stands as it was, for the next trigger.
    got = await admin_client.get(f"/api/v1/price-observations/{cheaper['id']}")
    assert got.status_code == 200 and got.json()["norm"]["status"] == "ok"
    assert await computed_at(owner_conn, bowl["id"]) == before
    assert (await cost(admin_client, bowl["id"]))["totals"]["consumed_cost"] == "0.8000"

    monkeypatch.undo()
    r = await admin_client.put(
        f"/api/v1/recipes/{bowl['id']}/pins/barley", json={"product_id": bag["id"]}
    )
    assert r.status_code == 200, r.text
    assert (await cost(admin_client, bowl["id"]))["totals"]["consumed_cost"] == "0.6000"
