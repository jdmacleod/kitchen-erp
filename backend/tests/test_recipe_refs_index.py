"""Recipe references and what a recipe makes, read and indexed (07, 3B; #526, Part 1).

The parser's `@./path{qty%unit}` and front matter `yield:`, the pure path and
graph helpers, and the scan's link pass against a temporary repository. Every
recipe, title and path is invented.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.db import get_sessionmaker
from app.models import Recipe, RecipeIngredient
from app.recipes.cooklang import IngredientRef, QuantityRange, Text, parse
from app.recipes.cooklang.hints import reference_hints
from app.recipes.cooklang.makes import YieldParseFailure, parse_yield
from app.recipes.refs import chain, components, cycles, target_path
from app.services import recipes
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401

# --- the parser ---------------------------------------------------------------------


def test_a_recipe_reference_sets_ref_path_and_its_base_name():
    parsed = parse("Shake @./basics/Lantern syrup{1%fl oz} with ice.\n")
    assert not isinstance(parsed, type(None))
    [ref] = parsed.ingredients
    assert ref.ref_path == "./basics/Lantern syrup"
    assert ref.raw_name == "Lantern syrup"
    assert ref.unit == "fl_oz" and ref.quantity.kind == "number"
    assert reference_hints(parsed) == []


def test_the_cooklang_spec_form_round_trips():
    """The spec's own example shape: `@./sauces/Hollandaise{150%g}`."""
    text = "Pour over @./sauces/Comet sauce{150%g}.\n"
    first = parse(text)
    [ref] = first.ingredients
    assert (ref.ref_path, ref.raw_name, ref.unit) == ("./sauces/Comet sauce", "Comet sauce", "g")
    again = parse(f"Pour over @{ref.ref_path}{{150%g}}.\n")
    assert again == first


def test_a_parent_path_and_a_written_cook_suffix_still_name_the_recipe():
    [ref] = parse("Add @../basics/harbor-stock.cook{2%cups}.\n").ingredients
    assert ref.ref_path == "../basics/harbor-stock.cook"
    assert ref.raw_name == "harbor-stock"


def test_the_braceless_form_stays_text():
    parsed = parse("Add @./basics/syrup to taste.\n")
    assert parsed.ingredients == ()
    assert any(isinstance(i, Text) and "@./basics/syrup" in i.value for i in parsed.steps[0].items)


def test_a_reference_range_keeps_both_ends():
    [ref] = parse("Add @./basics/syrup{1-2%tbsp}.\n").ingredients
    assert ref.quantity == QuantityRange(Decimal("1"), Decimal("2"))


def test_reference_hints_never_flag_a_recipe_reference_as_an_at_amp_one():
    text = (
        "= Syrup\nWarm @sugar{100%g}.\n\n= Drink\nShake @./syrup{1%fl oz} and @./syrup{1%tbsp}.\n"
    )
    assert reference_hints(parse(text)) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500 ml", (Decimal("500"), "ml")),
        ("1.5 l", (Decimal("1.5"), "l")),
        ("2 cups", (Decimal("2"), "cup")),
        ("500ml", (Decimal("500"), "ml")),
        ("1 1/2 cups", (Decimal("1.5"), "cup")),
        ("16.9 fl oz", (Decimal("16.9"), "fl_oz")),
        ("250 g", (Decimal("250"), "g")),
    ],
)
def test_parse_yield_reads_an_amount_and_a_mass_or_volume(text, expected):
    assert parse_yield(text) == expected


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("lots", "no_unit"),
        ("0 ml", "not_positive"),
        ("-1 l", "not_a_number"),
        ("400-500 ml", "not_a_number"),
        ("12 pieces", "not_mass_or_volume"),
        ("3 handfuls", "unknown_unit"),
        ("", "empty"),
    ],
)
def test_parse_yield_refuses_what_it_cannot_scale(text, reason):
    found = parse_yield(text)
    assert isinstance(found, YieldParseFailure) and found.reason == reason


def test_front_matter_yield_fills_makes_and_keeps_the_text():
    good = parse("---\ntitle: Lantern syrup\nyield: 500 ml\n---\nWarm @sugar{250%g}.\n")
    assert (good.makes_qty, good.makes_unit, good.makes_text) == (Decimal("500"), "ml", "500 ml")
    count = parse("---\nyield: 12 pieces\n---\nBake @flour{200%g}.\n")
    assert (count.makes_qty, count.makes_unit, count.makes_text) == (None, None, "12 pieces")
    none = parse("Bake @flour{200%g}.\n")
    assert (none.makes_qty, none.makes_unit, none.makes_text) == (None, None, None)


def test_an_at_amp_reference_is_not_a_recipe_reference():
    parsed = parse("Mix @flour{100%g}.\n\nAdd @&flour{50%g}.\n")
    assert all(isinstance(r, IngredientRef) and r.ref_path is None for r in parsed.ingredients)


# --- paths and the graph ------------------------------------------------------------


@pytest.mark.parametrize(
    ("referrer", "written", "expected"),
    [
        ("drinks/daiquiri.cook", "./basics/syrup", "drinks/basics/syrup.cook"),
        ("drinks/daiquiri.cook", "../basics/syrup", "basics/syrup.cook"),
        ("daiquiri.cook", "./syrup.cook", "syrup.cook"),
        ("drinks/daiquiri.cook", "../../syrup", None),
        ("daiquiri.cook", "../syrup", None),
        ("daiquiri.cook", "/etc/syrup", None),
        ("daiquiri.cook", "./", None),
    ],
)
def test_target_path_normalizes_and_refuses_to_leave_the_root(referrer, written, expected):
    assert target_path(referrer, written) == expected


def test_components_come_children_first_and_cycles_are_found():
    edges = {"cocktail": ["syrup"], "syrup": ["sugar-blend"], "a": ["b"], "b": ["a"], "s": ["s"]}
    order = components(edges, edges)
    flat = [member for component in order for member in component]
    assert flat.index("sugar-blend") < flat.index("syrup") < flat.index("cocktail")
    assert sorted(map(sorted, cycles(edges, edges))) == [["a", "b"], ["s"]]
    assert chain(["a", "b"], edges, "a") == ["a", "b", "a"]
    assert chain(["s"], edges, "s") == ["s", "s"]


def test_a_long_chain_needs_no_recursion():
    edges = {n: [n + 1] for n in range(5000)}
    assert len(components(edges, edges)) == 5001


# --- the scan -----------------------------------------------------------------------

SYRUP = "---\ntitle: Lantern syrup\nyield: 500 ml\n---\nWarm @sugar{250%g} in @water{250%ml}.\n"
DRINK = (
    "---\ntitle: Harbor sour\n---\n"
    "Shake @./basics/lantern-syrup{1%fl oz} with @lime juice{1%fl oz}.\n"
)


async def _lines(path: str) -> list[RecipeIngredient]:
    async with get_sessionmaker()() as db:
        recipe = (await db.execute(select(Recipe).where(Recipe.path == path))).scalar_one()
        return list(
            (
                await db.execute(
                    select(RecipeIngredient)
                    .where(RecipeIngredient.recipe_id == recipe.id)
                    .order_by(RecipeIngredient.seq)
                )
            ).scalars()
        )


async def _recipe(path: str) -> Recipe:
    async with get_sessionmaker()() as db:
        return (await db.execute(select(Recipe).where(Recipe.path == path))).scalar_one()


async def _scan(*, reparse: bool = False):
    async with get_sessionmaker()() as db:
        return await recipes.scan(db, reparse=reparse)


async def test_a_reference_row_is_recipe_and_links_to_a_target_made_in_the_same_scan(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("basics/lantern-syrup.cook", SYRUP)
    recipes_repo.write("harbor-sour.cook", DRINK)
    recipes_repo.commit()
    out = await _scan()
    assert out.missing_refs == []
    syrup = await _recipe("basics/lantern-syrup.cook")
    assert (syrup.makes_qty, syrup.makes_unit, syrup.makes_text) == (Decimal("500"), "ml", "500 ml")
    reference, lime = await _lines("harbor-sour.cook")
    assert reference.resolution == "recipe" and reference.ingredient_id is None
    assert reference.ref_path == "./basics/lantern-syrup"
    assert reference.ref_recipe_id == syrup.id
    assert reference.raw_name == "lantern-syrup"
    assert lime.resolution != "recipe" and lime.ref_path is None
    drink = await _recipe("harbor-sour.cook")
    [_, item, *_] = drink.body[0]["steps"][0]
    assert item["t"] == "ingredient" and item["ref_path"] == "./basics/lantern-syrup"


async def test_moving_the_target_unlinks_and_a_recipe_at_the_old_path_links_again(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("basics/lantern-syrup.cook", SYRUP)
    recipes_repo.write("harbor-sour.cook", DRINK)
    recipes_repo.commit()
    await _scan()
    recipes_repo.move("basics/lantern-syrup.cook", "syrups/lantern-syrup.cook")
    recipes_repo.commit("move")
    out = await _scan()
    [missing] = out.missing_refs
    assert (missing.referrer_path, missing.ref_path, missing.target) == (
        "harbor-sour.cook",
        "./basics/lantern-syrup",
        "basics/lantern-syrup.cook",
    )
    reference, _ = await _lines("harbor-sour.cook")
    assert reference.ref_recipe_id is None and reference.resolution == "recipe"

    recipes_repo.write("basics/lantern-syrup.cook", SYRUP.replace("250%g", "300%g"))
    recipes_repo.commit("again")
    assert (await _scan()).missing_refs == []
    again = await _recipe("basics/lantern-syrup.cook")
    reference, _ = await _lines("harbor-sour.cook")
    assert reference.ref_recipe_id == again.id


async def test_deleting_the_target_unlinks_its_referrers(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("basics/lantern-syrup.cook", SYRUP)
    recipes_repo.write("harbor-sour.cook", DRINK)
    recipes_repo.commit()
    await _scan()
    recipes_repo.remove("basics/lantern-syrup.cook")
    recipes_repo.commit("gone")
    out = await _scan()
    assert [m.target for m in out.missing_refs] == ["basics/lantern-syrup.cook"]
    reference, _ = await _lines("harbor-sour.cook")
    assert reference.ref_recipe_id is None


async def test_a_reference_outside_the_root_names_nothing(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("harbor-sour.cook", "Shake @../../elsewhere/syrup{1%fl oz}.\n")
    recipes_repo.commit()
    out = await _scan()
    [missing] = out.missing_refs
    assert missing.target is None and missing.ref_path == "../../elsewhere/syrup"


async def test_an_edit_and_a_reparse_keep_the_reference_rows(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("basics/lantern-syrup.cook", SYRUP)
    recipes_repo.write("harbor-sour.cook", DRINK)
    recipes_repo.commit()
    await _scan()
    recipes_repo.edit("harbor-sour.cook", "\nGarnish and serve.\n")
    recipes_repo.commit("edit")
    await _scan()
    reference, _ = await _lines("harbor-sour.cook")
    assert reference.resolution == "recipe" and reference.ref_recipe_id is not None
    await _scan(reparse=True)
    reference, _ = await _lines("harbor-sour.cook")
    assert reference.resolution == "recipe" and reference.ref_recipe_id is not None


async def test_a_reparse_fills_makes_on_an_unchanged_file(recipes_repo: TempRepo):  # noqa: F811
    """What `kerp recipes rescan --reparse` does after the 0071 upgrade."""
    recipes_repo.write("basics/lantern-syrup.cook", SYRUP)
    recipes_repo.commit()
    await _scan()
    async with get_sessionmaker()() as db:
        await db.execute(update(Recipe).values(makes_qty=None, makes_unit=None, makes_text=None))
        await db.commit()
    assert (await _scan()).updated == 0
    assert (await _recipe("basics/lantern-syrup.cook")).makes_qty is None
    await _scan(reparse=True)
    syrup = await _recipe("basics/lantern-syrup.cook")
    assert (syrup.makes_qty, syrup.makes_unit) == (Decimal("500"), "ml")


async def test_a_count_yield_indexes_its_text_only(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("comet-buns.cook", "---\nyield: 12 pieces\n---\nBake @flour{300%g}.\n")
    recipes_repo.commit()
    await _scan()
    buns = await _recipe("comet-buns.cook")
    assert (buns.makes_qty, buns.makes_unit, buns.makes_text) == (None, None, "12 pieces")
