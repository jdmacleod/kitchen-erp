"""Lines costed from another recipe (07, 3D; #526, rulings S2–S4, Part 2).

A recipe reference (`@./basics/lantern-syrup{1%fl oz}`) and route (b) (a line on
an ingredient whose ``made_by_recipe_id`` names the recipe that makes it) cost
from that recipe's snapshot, scaled by what it makes. Stores, products and
recipes are invented; the geography is synthetic.

The figures: a 1 kg sugar bag at $4.00 is $0.004000 per g. Lantern syrup warms
250 g of sugar in water and makes 500 ml, so it costs $1.0000. One fl oz is
29.5735295625 ml, so a drink's 1 fl oz is 29.5735295625 / 500 of a batch:
$0.059147059125, $0.0591 at the column's scale.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.models import Recipe, RecipeCostLine, RecipeCostSnapshot, RecipeIngredient
from app.services import recipe_costing
from app.services.packs import money
from tests.catalog_helpers import make_ingredient
from tests.geo_helpers import NEAR_A, make_home_base
from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_posted_prices import post_listing_price

D = Decimal
FL_OZ = D("29.5735295625")
SYRUP = "---\ntitle: Lantern syrup\nyield: 500 ml\n---\nWarm @sugar{250%g} in @water{250%ml}.\n"
SOUR = "---\ntitle: Harbor sour\n---\nShake @./basics/lantern-syrup{1%fl oz} with ice.\n"


async def rescan(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200, r.text


async def write(client: httpx.AsyncClient, repo: TempRepo, files: dict[str, str]) -> None:
    for path, body in files.items():
        repo.write(path, body)
    repo.commit()
    await rescan(client)


async def sugar(client: httpx.AsyncClient, price: str = "4.00", *, posted: bool = False) -> dict:
    loc = await make_location(client, "Harbor Market", "Harbor Market")
    bag = await make_product(client, "Sugar", "Sugar bag", pack_qty="1", pack_unit="kg")
    if posted:
        await post_listing_price(loc, bag, price)
    else:
        await shelf(client, bag["id"], loc["id"], price)
    return {"loc": loc, "bag": bag}


async def recipe_id(path: str) -> uuid.UUID:
    async with get_sessionmaker()() as db:
        return (await db.execute(select(Recipe.id).where(Recipe.path == path))).scalar_one()


async def snap(
    path: str, basis: str = "latest", window: int | None = None
) -> tuple[RecipeCostSnapshot, dict[str, RecipeCostLine]]:
    """Compute the recipe's snapshot now; the snapshot and its lines by name."""
    async with get_sessionmaker()() as db:
        recipe = (await db.execute(select(Recipe).where(Recipe.path == path))).scalar_one()
        snapshot = await recipe_costing.compute_snapshot(db, recipe, basis, window)
        rows = (
            await db.execute(
                select(RecipeCostLine, RecipeIngredient.name_norm)
                .join(RecipeIngredient, RecipeIngredient.id == RecipeCostLine.recipe_ingredient_id)
                .where(RecipeCostLine.snapshot_id == snapshot.id)
            )
        ).all()
        return snapshot, {name: line for line, name in rows}


async def made_by(ingredient_name: str, path: str | None) -> None:
    async with get_sessionmaker()() as db:
        source = await recipe_id(path) if path else None
        await db.execute(
            text("UPDATE ingredient SET made_by_recipe_id = :r WHERE lower(name) = lower(:n)"),
            {"r": source, "n": ingredient_name},
        )
        await db.commit()


# --- recipe references ---------------------------------------------------------------------


async def test_a_reference_costs_its_share_of_a_complete_batch(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR},
    )
    syrup, _ = await snap("basics/lantern-syrup.cook")
    assert syrup.consumed_cost == D("1.0000")
    sour, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert line.status == "priced" and line.failure_code is None
    assert line.source_recipe_id == syrup.recipe_id and line.source_partial is False
    assert line.canonical_qty == FL_OZ and line.canonical_unit == "ml"
    assert line.consumed_cost == money(FL_OZ / D(500)) == D("0.0591")
    assert line.basket_cost == line.consumed_cost  # the same share, never a whole batch
    assert sour.consumed_cost == D("0.0591") and sour.basket_cost == D("0.0591")
    assert sour.lines_priced == 1 and sour.lines_partial == 0


async def test_a_range_scales_both_ends(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    sour = SOUR.replace("{1%fl oz}", "{1-2%fl oz}")
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": sour}
    )
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert (line.consumed_cost, line.consumed_cost_high) == (D("0.0591"), D("0.1183"))
    assert line.canonical_qty_high == 2 * FL_OZ


async def test_a_partial_source_marks_the_line_and_the_referrer_incomplete(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Moonflower")  # no product, no price
    partial = SYRUP.replace("in @water", "with @moonflower{2%g} in @water")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": partial, "harbor-sour.cook": SOUR},
    )
    sour, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert line.status == "priced" and line.source_partial is True
    assert line.consumed_cost == D("0.0591")
    assert sour.lines_partial == 1 and sour.lines_priced == 1


async def test_a_source_on_a_posted_price_makes_the_line_posted_and_uncounted(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client, posted=True)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    sour, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert line.status == "posted" and line.consumed_cost == D("0.0591")
    assert sour.consumed_cost is None and sour.posted_consumed_cost == D("0.0591")
    assert sour.lines_posted == 1 and sour.lines_priced == 0


async def test_a_part_counted_part_posted_source_posts_the_sum(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    stuff = await sugar(admin_client)
    honey = await make_product(admin_client, "Honey", "Honey jar", pack_qty="500", pack_unit="g")
    await post_listing_price(stuff["loc"], honey, "5.00")  # $0.010000 per g
    mixed = SYRUP.replace("in @water", "with @honey{50%g} in @water")
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": mixed, "harbor-sour.cook": SOUR}
    )
    syrup, _ = await snap("basics/lantern-syrup.cook")
    assert (syrup.consumed_cost, syrup.posted_consumed_cost) == (D("1.0000"), D("0.5000"))
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert line.status == "posted"
    assert line.consumed_cost == money(FL_OZ / D(500) * D("1.5000"))


@pytest.mark.parametrize(
    ("front", "unit", "code"),
    [
        ("---\ntitle: Lantern syrup\n---\n", "fl oz", "no_yield"),
        ("---\ntitle: Lantern syrup\nyield: 2 jars\n---\n", "fl oz", "bad_yield"),
        ("---\ntitle: Lantern syrup\nyield: 500 ml\n---\n", "g", "yield_unit"),
        ("---\ntitle: Lantern syrup\nyield: 500 ml\n---\n", "oz", "yield_unit"),
    ],
)
async def test_a_reference_that_cannot_scale_says_why_and_names_its_source(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    front: str,
    unit: str,
    code: str,
):
    await sugar(admin_client)
    syrup = front + "Warm @sugar{250%g} in @water{250%ml}.\n"
    sour = SOUR.replace("%fl oz}", f"%{unit}}}")
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": syrup, "harbor-sour.cook": sour}
    )
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert (line.status, line.failure_code) == ("unconvertible", code)
    assert line.source_recipe_id == await recipe_id("basics/lantern-syrup.cook")


async def test_a_reference_to_nothing_is_no_recipe(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await write(admin_client, recipes_repo, {"harbor-sour.cook": SOUR})
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert (line.status, line.failure_code, line.source_recipe_id) == (
        "unmapped",
        "no_recipe",
        None,
    )


async def test_a_source_that_does_not_parse_is_unreadable(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    recipes_repo.write("basics/lantern-syrup.cook", SYRUP + "Stir @sugar{2 and serve.\n")
    recipes_repo.commit("break it")
    await rescan(admin_client)
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert (line.status, line.failure_code) == ("unpriced", "recipe_unreadable")
    assert line.source_recipe_id is not None


async def test_a_source_with_no_price_is_recipe_unpriced(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await make_ingredient(admin_client, "Sugar")
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern-syrup"]
    assert (line.status, line.failure_code) == ("unpriced", "recipe_unpriced")
    assert line.source_partial is False


# --- cycles -----------------------------------------------------------------------------

LOOP_A = "---\ntitle: Comet glaze\nyield: 1 l\n---\nWarm @sugar{100%g} and @./comet-dip{100%ml}.\n"
LOOP_B = "---\ntitle: Comet dip\nyield: 1 l\n---\nStir @sugar{100%g} and @./comet-glaze{100%ml}.\n"


async def test_two_recipes_that_use_each_other_are_both_cycle_even_with_snapshots_stored(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(admin_client, recipes_repo, {"comet-glaze.cook": LOOP_A, "comet-dip.cook": LOOP_B})
    for _ in range(2):  # the second pass has every snapshot stored already
        for path, other in (("comet-glaze.cook", "comet-dip"), ("comet-dip.cook", "comet-glaze")):
            snapshot, lines = await snap(path)
            assert (lines[other].status, lines[other].failure_code) == ("unpriced", "cycle")
            assert snapshot.consumed_cost == D("0.4000")  # the sugar alone, never fed back


async def test_a_recipe_that_uses_itself_is_cycle(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    selfish = "---\nyield: 1 l\n---\nWarm @sugar{100%g} and @./mother-sauce{100%ml}.\n"
    await write(admin_client, recipes_repo, {"mother-sauce.cook": selfish})
    _, lines = await snap("mother-sauce.cook")
    assert lines["mother-sauce"].failure_code == "cycle"


async def test_a_cycle_through_a_made_by_link_is_cycle(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    # The syrup uses itself through its own ingredient: a made-by loop.
    loop = SYRUP.replace("in @water", "with @lantern syrup{10%ml} in @water")
    await write(admin_client, recipes_repo, {"basics/lantern-syrup.cook": loop})
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    _, lines = await snap("basics/lantern-syrup.cook")
    assert (lines["lantern syrup"].status, lines["lantern syrup"].failure_code) == (
        "unpriced",
        "cycle",
    )


# --- route (b) --------------------------------------------------------------------------

SOUR_B = "---\ntitle: Harbor sour\n---\nShake @lantern syrup{1%fl oz} with ice.\n"


async def test_route_b_costs_a_line_whose_ingredient_a_recipe_makes(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR_B},
    )
    _, before = await snap("harbor-sour.cook")
    assert before["lantern syrup"].status == "unpriced"
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern syrup"]
    assert line.status == "priced" and line.consumed_cost == D("0.0591")
    assert line.source_recipe_id == await recipe_id("basics/lantern-syrup.cook")


async def test_route_b_crosses_mass_and_volume_through_the_ingredients_density(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(
        admin_client, "Lantern syrup", density_g_per_ml="1.3", density_source="manual"
    )
    by_weight = SYRUP.replace("yield: 500 ml", "yield: 650 g")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": by_weight, "harbor-sour.cook": SOUR_B},
    )
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern syrup"]
    # 1 fl oz is 38.44558843125 g at 1.3 g/ml, of a 650 g batch: the same share.
    assert line.status == "priced" and line.consumed_cost == D("0.0591")
    assert line.canonical_unit == "g"


async def test_route_b_takes_an_unconvertible_line_when_line_and_yield_share_a_dimension(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    """A syrup in g with no density: its fl oz line can't reach g, but the yield is in ml."""
    await sugar(admin_client)
    await make_ingredient(admin_client, "Lantern syrup")  # canonical g, no density
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR_B},
    )
    _, before = await snap("harbor-sour.cook")
    assert (before["lantern syrup"].status, before["lantern syrup"].failure_code) == (
        "unconvertible",
        "no_density",
    )
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    _, lines = await snap("harbor-sour.cook")
    assert lines["lantern syrup"].status == "priced"


async def test_route_b_keeps_no_density_against_a_yield_in_g(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Lantern syrup")  # canonical g, no density
    by_weight = SYRUP.replace("yield: 500 ml", "yield: 650 g")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": by_weight, "harbor-sour.cook": SOUR_B},
    )
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern syrup"]
    assert (line.status, line.failure_code) == ("unconvertible", "no_density")
    assert line.source_recipe_id == await recipe_id("basics/lantern-syrup.cook")


async def test_a_product_price_or_a_posted_one_always_beats_the_recipe(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    stuff = await sugar(admin_client)
    bottle = await make_product(
        admin_client,
        "Lantern syrup",
        "Lantern syrup bottle",
        canonical_unit="ml",
        pack_qty="750",
        pack_unit="ml",
    )
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR_B},
    )
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    await post_listing_price(stuff["loc"], bottle, "7.50")
    _, lines = await snap("harbor-sour.cook")
    assert lines["lantern syrup"].status == "posted"
    assert lines["lantern syrup"].source_recipe_id is None
    await shelf(admin_client, bottle["id"], stuff["loc"]["id"], "15.00")
    _, lines = await snap("harbor-sour.cook")
    assert lines["lantern syrup"].status == "priced"
    assert lines["lantern syrup"].source_recipe_id is None
    assert lines["lantern syrup"].consumed_cost == money(FL_OZ * D("0.02"))


async def test_a_missing_made_by_recipe_is_no_recipe(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Lantern syrup", canonical_unit="ml")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR_B},
    )
    await made_by("Lantern syrup", "basics/lantern-syrup.cook")
    recipes_repo.remove("basics/lantern-syrup.cook")
    recipes_repo.commit("gone")
    await rescan(admin_client)
    _, lines = await snap("harbor-sour.cook")
    line = lines["lantern syrup"]
    assert (line.status, line.failure_code) == ("unmapped", "no_recipe")
    assert line.source_recipe_id is not None


# --- chains, keys and transactions ----------------------------------------------------------

LEVEL_C = "---\nyield: 3 l\n---\nWarm @sugar{1000%g} in @water{3%l}.\n"
LEVEL_B = "---\nyield: 3 l\n---\nStir @./level-c{1%l} into @water{2%l}.\n"
LEVEL_A = "---\nyield: 3 l\n---\nStir @./level-b{1%l} into @water{2%l}.\n"


async def test_a_three_level_chain_costs_every_level_with_one_quantization_per_line(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(
        admin_client,
        recipes_repo,
        {"level-a.cook": LEVEL_A, "level-b.cook": LEVEL_B, "level-c.cook": LEVEL_C},
    )
    a, lines = await snap("level-a.cook")
    # C is $4.0000; B takes a third of it, quantized once; A a third of B.
    b_cost = money(D(4) / D(3))
    assert b_cost == D("1.3333")
    assert lines["level-b"].consumed_cost == money(b_cost / D(3)) == D("0.4444")
    assert a.consumed_cost == D("0.4444")


async def test_the_sources_on_demand_snapshot_is_kept_under_the_referrers_key(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    await snap("harbor-sour.cook", "average", 30)
    syrup = await recipe_id("basics/lantern-syrup.cook")
    async with get_sessionmaker()() as db:
        keys = (
            await db.execute(
                select(RecipeCostSnapshot.basis, RecipeCostSnapshot.window_days).where(
                    RecipeCostSnapshot.recipe_id == syrup
                )
            )
        ).all()
    assert ("average", 30) in keys


async def test_an_on_demand_source_snapshot_rolls_back_with_its_referrer(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    syrup = await recipe_id("basics/lantern-syrup.cook")

    async def cheapest_count() -> int:
        async with get_sessionmaker()() as db:
            return len(
                (
                    await db.execute(
                        select(RecipeCostSnapshot.id).where(
                            RecipeCostSnapshot.recipe_id == syrup,
                            RecipeCostSnapshot.basis == "cheapest",
                        )
                    )
                ).all()
            )

    async with get_sessionmaker()() as db:
        recipe = (await db.execute(select(Recipe).where(Recipe.path == "harbor-sour.cook"))).one()[
            0
        ]
        await recipe_costing.compute_snapshot(db, recipe, "cheapest", commit=False)
        await db.rollback()
    assert await cheapest_count() == 0
    await snap("harbor-sour.cook", "cheapest")
    assert await cheapest_count() == 1
    async with get_sessionmaker()() as db:
        before = (
            await db.execute(
                select(RecipeCostSnapshot.id).where(
                    RecipeCostSnapshot.recipe_id == syrup, RecipeCostSnapshot.basis == "latest"
                )
            )
        ).scalar_one_or_none()
        await recipe_costing.recompute_all(db)
        after = (
            await db.execute(
                select(RecipeCostSnapshot).where(
                    RecipeCostSnapshot.recipe_id == syrup, RecipeCostSnapshot.basis == "latest"
                )
            )
        ).scalar_one()
    assert after.consumed_cost == D("1.0000")
    assert before is None or after.id != before  # rebuilt, one row for the key


async def test_two_sessions_creating_the_same_source_snapshot_both_succeed(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )

    async def one() -> Decimal | None:
        async with get_sessionmaker()() as db:
            recipe = (
                await db.execute(select(Recipe).where(Recipe.path == "harbor-sour.cook"))
            ).scalar_one()
            snapshot = await recipe_costing.compute_snapshot(db, recipe, "average", 7)
            return snapshot.consumed_cost

    first, second = await asyncio.gather(one(), one())
    assert first == second == D("0.0591")


async def test_a_kitchen_view_and_offers_cost_the_source_under_latest(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    harbor = await make_home_base(admin_client, "Harbor kitchen", NEAR_A)
    loc = await make_location(
        admin_client, "Harbor Market", "Harbor Market", coords=SYNTH[0], home_base_id=harbor["id"]
    )
    bag = await make_product(admin_client, "Sugar", "Sugar bag", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, bag["id"], loc["id"], "4.00")
    await write(
        admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP, "harbor-sour.cook": SOUR}
    )
    async with get_sessionmaker()() as db:
        recipe = (
            await db.execute(select(Recipe).where(Recipe.path == "harbor-sour.cook"))
        ).scalar_one()
        for basis in ("average", "offers"):
            costing = await recipe_costing.cost_recipe(
                db,
                recipe,
                basis,
                30 if basis == "average" else None,
                None,
                home_base_id=uuid.UUID(harbor["id"]),
            )
            [line] = costing.costs
            assert line.status == "priced" and line.consumed_cost == D("0.0591"), basis
            assert line.source_basis == "latest", basis
        costing = await recipe_costing.cost_recipe(
            db, recipe, "latest", None, None, home_base_id=uuid.UUID(harbor["id"])
        )
        assert costing.costs[0].source_basis is None  # same basis: nothing to say


async def test_no_graph_query_without_recipe_links(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services import recipe_cost_sources

    await sugar(admin_client)
    await write(admin_client, recipes_repo, {"basics/lantern-syrup.cook": SYRUP})
    calls: list[int] = []
    real = recipe_cost_sources.recipe_graph

    async def counted(db):
        calls.append(1)
        return await real(db)

    monkeypatch.setattr(recipe_cost_sources, "recipe_graph", counted)
    syrup, _ = await snap("basics/lantern-syrup.cook")
    assert syrup.consumed_cost == D("1.0000") and calls == []


async def test_a_version_bump_gives_a_referrer_that_sorts_first_a_fresh_source(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    """`kerp migrate`'s rebuild runs in path order and never truncates: `a-sour`
    is recomputed before `z-syrup`, and must not read the syrup's old-version cost."""
    from app.services import derived_versions

    await sugar(admin_client)
    sour = SOUR.replace("./basics/lantern-syrup", "./z-syrup")
    await write(admin_client, recipes_repo, {"z-syrup.cook": SYRUP, "a-sour.cook": sour})
    await snap("z-syrup.cook")
    await snap("a-sour.cook")
    async with get_sessionmaker()() as db:
        # What an older costing version left: a different figure for the syrup.
        await db.execute(
            text("UPDATE recipe_cost_snapshot SET consumed_cost = 9.0000 WHERE recipe_id = :r"),
            {"r": await recipe_id("z-syrup.cook")},
        )
        await db.commit()
    monkeypatch.setattr(recipe_costing, "COSTING_VERSION", recipe_costing.COSTING_VERSION + 1)
    async with get_sessionmaker()() as db:
        assert await derived_versions.refresh_if_stale(db, lambda _: None)
    async with get_sessionmaker()() as db:
        recipe = (await db.execute(select(Recipe).where(Recipe.path == "a-sour.cook"))).scalar_one()
        stored = await recipe_costing.find_snapshot(db, recipe)
        assert stored is not None and stored.consumed_cost == D("0.0591")


async def test_a_recipe_whose_only_gap_is_a_partial_source_is_incomplete(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await sugar(admin_client)
    await make_ingredient(admin_client, "Moonflower")  # no product, no price
    partial = SYRUP.replace("in @water", "with @moonflower{2%g} in @water")
    await write(
        admin_client,
        recipes_repo,
        {"basics/lantern-syrup.cook": partial, "harbor-sour.cook": SOUR},
    )
    await snap("harbor-sour.cook")
    sour = await recipe_id("harbor-sour.cook")
    view = (await admin_client.get(f"/api/v1/recipes/{sour}/cost")).json()
    assert view["completeness"]["lines_partial"] == 1
    assert view["completeness"]["lines_priced"] == view["completeness"]["lines_total"] == 1

    async def paths(completeness: str) -> set[str]:
        r = await admin_client.get("/api/v1/recipes", params={"completeness": completeness})
        return {i["path"] for i in r.json()["items"]}

    assert "harbor-sour.cook" in await paths("incomplete")
    assert "harbor-sour.cook" not in await paths("complete")
