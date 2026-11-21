"""Keeping referrers current (07, 3D; #526, Part 3).

A price change on a source's ingredient recomputes the source and then every
recipe that costs from it, children first; the result counts recipes, not
snapshots; a merge carries the made-by link. Stores, products and recipes are
invented.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.models import Ingredient, Recipe
from app.services import recipe_cost_sources, recipe_cost_triggers, recipe_costing
from app.services.recipe_cost_sources import RecipeGraph
from tests.catalog_helpers import make_ingredient
from tests.pricebook_helpers import shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_recipe_cost_sources import (
    LEVEL_A,
    LEVEL_B,
    LEVEL_C,
    SOUR,
    SOUR_B,
    SYRUP,
    made_by,
    recipe_id,
    sugar,
    write,
)

D = Decimal


async def latest_cost(path: str) -> Decimal | None:
    async with get_sessionmaker()() as db:
        recipe = (await db.execute(select(Recipe).where(Recipe.path == path))).scalar_one()
        snapshot = await recipe_costing.find_snapshot(db, recipe)
        assert snapshot is not None
        return snapshot.consumed_cost


def watch_order(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    seen: list[uuid.UUID] = []
    real = recipe_costing.recompute_recipe_core

    async def recording(db, rid, **kw):
        seen.append(rid)
        return await real(db, rid, **kw)

    monkeypatch.setattr(recipe_costing, "recompute_recipe_core", recording)
    return seen


async def test_a_price_change_recomputes_the_source_and_then_its_referrer(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    stuff = await sugar(admin_client)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    assert await latest_cost("harbor-sour.cook") == D("0.0591")
    order = watch_order(monkeypatch)
    await shelf(admin_client, stuff["bag"]["id"], stuff["loc"]["id"], "8.00")
    syrup, sour = await recipe_id("basics/lantern-syrup.cook"), await recipe_id("harbor-sour.cook")
    assert order == [syrup, sour]
    assert await latest_cost("basics/lantern-syrup.cook") == D("2.0000")
    assert await latest_cost("harbor-sour.cook") == D("0.1183")


async def test_a_two_level_chain_follows_a_price_change(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    stuff = await sugar(admin_client)
    await write(
        admin_client,
        recipes_repo,
        {"level-a.cook": LEVEL_A, "level-b.cook": LEVEL_B, "level-c.cook": LEVEL_C},
    )
    assert await latest_cost("level-a.cook") == D("0.4444")
    await shelf(admin_client, stuff["bag"]["id"], stuff["loc"]["id"], "8.00")
    # C is $8.0000, B a third of it ($2.6667), A a third of B.
    assert await latest_cost("level-a.cook") == D("0.8889")


async def test_linking_an_ingredient_to_its_recipe_recomputes_the_lines_on_it(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    ing = await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR_B},
    )
    assert await latest_cost("harbor-sour.cook") is None
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    async with get_sessionmaker()() as db:
        result = await recipe_cost_triggers.after_ingredient_bridge_changed(
            db, uuid.UUID(ing["id"]), commit=True
        )
    assert (result.recipes_ok, result.recipes_failed) == (1, 0)
    assert await latest_cost("harbor-sour.cook") == D("0.0591")


async def test_recompute_all_in_path_order_matches_children_first(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    # "a-…" sorts before its source, "z-…".
    await write(
        admin_client,
        recipes_repo,
        {"z-syrup.cook": SYRUP, "a-sour.cook": SOUR.replace("./basics/lantern-syrup", "./z-syrup")},
    )
    children_first = await latest_cost("a-sour.cook")
    async with get_sessionmaker()() as db:
        await recipe_costing.recompute_all(db)
    assert await latest_cost("a-sour.cook") == children_first == D("0.0591")


async def test_recompute_recipe_reaches_its_referrers(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    await sugar(admin_client)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    order = watch_order(monkeypatch)
    syrup = await recipe_id("basics/lantern-syrup.cook")
    async with get_sessionmaker()() as db:
        assert await recipe_costing.recompute_recipe(db, syrup) == 2  # one key each
    assert order == [syrup, await recipe_id("harbor-sour.cook")]


async def test_a_failed_source_is_counted_and_its_referrer_still_runs(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    await sugar(admin_client)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    syrup = await recipe_id("basics/lantern-syrup.cook")
    real = recipe_costing.recompute_recipe_core

    async def flaky(db, rid, **kw):
        if rid == syrup:
            raise RuntimeError("synthetic failure")
        return await real(db, rid, **kw)

    monkeypatch.setattr(recipe_costing, "recompute_recipe_core", flaky)
    with caplog.at_level(logging.WARNING):
        async with get_sessionmaker()() as db:
            result = await recipe_cost_triggers.after_recipe_changed(db, syrup, commit=True)
    assert (result.recipes_ok, result.recipes_failed, result.snapshots) == (1, 1, 1)
    assert "recipe cost recompute reads a source that failed" in caplog.text
    # The referrer read the source's last stored snapshot.
    assert await latest_cost("harbor-sour.cook") == D("0.0591")


async def test_no_graph_query_and_no_expansion_without_links(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    stuff = await sugar(admin_client)
    await write(admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP})
    calls: list[int] = []

    async def counted(db):
        calls.append(1)
        return RecipeGraph()

    monkeypatch.setattr(recipe_cost_sources, "recipe_graph", counted)
    await shelf(admin_client, stuff["bag"]["id"], stuff["loc"]["id"], "8.00")
    assert await latest_cost("basics/lantern-syrup.cook") == D("2.0000")
    assert calls == []


def test_children_first_puts_a_chain_in_order_and_a_cycle_together_by_id():
    a, b, c, x, y = (uuid.UUID(int=n) for n in (5, 4, 3, 9, 8))
    graph = RecipeGraph.of({a: {b}, b: {c}, x: {y}, y: {x}})
    order = graph.children_first({a, b, c, x, y})
    assert order.index(c) < order.index(b) < order.index(a)
    i = order.index(y)
    assert order[i : i + 2] == [y, x]  # the cycle's members together, by str(id)
    assert graph.cycle_of(x) == [y, x] and graph.cycle_of(a) == []
    assert graph.chain(x) == [x, y, x]


async def test_a_merge_carries_the_made_by_link_and_says_when_both_had_one(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "basics/comet-syrup.cook": SYRUP},
    )
    lantern, comet = (
        await recipe_id("basics/lantern-syrup.cook"),
        await recipe_id("basics/comet-syrup.cook"),
    )

    async def pair(loser_link: uuid.UUID | None, survivor_link: uuid.UUID | None, n: int):
        keep = await make_ingredient(admin_client, f"Keep syrup {n}", canonical_unit="ml")
        lose = await make_ingredient(admin_client, f"Lose syrup {n}", canonical_unit="ml")
        async with get_sessionmaker()() as db:
            for ing, link in ((keep, survivor_link), (lose, loser_link)):
                row = await db.get(Ingredient, uuid.UUID(ing["id"]))
                row.made_by_recipe_id = link
            await db.commit()
        r = await admin_client.post(
            "/api/v1/ingredients/merge",
            json={
                "survivor_id": keep["id"],
                "loser_id": lose["id"],
                "name": f"Keep syrup {n}",
                "copy_measures": [],
            },
        )
        assert r.status_code == 200, r.text
        async with get_sessionmaker()() as db:
            kept = await db.get(Ingredient, uuid.UUID(keep["id"]))
            return kept.made_by_recipe_id, r.json()["made_by_dropped_recipe_id"]

    assert await pair(lantern, None, 1) == (lantern, None)
    assert await pair(lantern, comet, 2) == (comet, str(lantern))
    assert await pair(lantern, lantern, 3) == (lantern, None)
