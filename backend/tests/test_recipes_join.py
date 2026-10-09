"""The parser joined into the indexer (07, Phase 3, package 3).

The five parser fixtures index with their ingredient rows, front matter and
titles; a file that stops parsing keeps its last good rows (3A, criterion 4);
relink proposals use titles and ingredient sets; migration 0043 round-trips;
and the detail endpoint shows the rows. Recipes are invented.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import asyncpg
import httpx
from sqlalchemy import select

from app.core.db import dispose_engine, get_sessionmaker
from app.models import Recipe, RecipeIngredient
from app.services import recipes
from tests.conftest import run_alembic
from tests.recipes_helpers import (
    FIXTURE_DIR,
    TempRepo,
    recipes_repo,  # noqa: F401
)

PARSER_FIXTURES = sorted(
    p.name for p in FIXTURE_DIR.glob("*.cook") if not p.name.startswith("index_")
)


def seed_parser_fixtures(repo: TempRepo) -> list[str]:
    for name in PARSER_FIXTURES:
        repo.write(name, (FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return list(PARSER_FIXTURES)


async def run_scan():
    async with get_sessionmaker()() as db:
        return await recipes.scan(db)


async def rows() -> dict[str, Recipe]:
    async with get_sessionmaker()() as db:
        found = (await db.execute(select(Recipe))).scalars().all()
        return {r.path: r for r in found}


async def lines(recipe_id: uuid.UUID) -> list[RecipeIngredient]:
    async with get_sessionmaker()() as db:
        stmt = (
            select(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == recipe_id)
            .order_by(RecipeIngredient.seq)
        )
        return list((await db.execute(stmt)).scalars())


def by_name(found: list[RecipeIngredient]) -> dict[str, RecipeIngredient]:
    return {line.raw_name: line for line in found}


GOOD = """---
title: Lamp oil noodles
servings: 2
---

Boil @noodles{200%g} and toss with @sesame oil{1%tbsp} and @scallions{2}(sliced).
"""

# The same recipe with a brace never closed, and a different title to prove the
# old one stays.
BROKEN = """---
title: Lamp oil noodles, broken
servings: 3
---

Boil @noodles{200%g} and toss with @scallions{2}(sliced) and @sesame oil{1%tbsp
"""

FIXED = """---
title: Lamp oil noodles, fixed
servings: 3
---

Boil @noodles{250%g} and toss with @sesame oil{1%tbsp}, @chilli oil{1%tsp}
and @scallions{2}(sliced).
"""


# --- the five parser fixtures -------------------------------------------------------


async def test_the_parser_fixtures_index_with_their_ingredient_rows(recipes_repo: TempRepo):  # noqa: F811
    seed_parser_fixtures(recipes_repo)
    recipes_repo.commit("five")
    result = await run_scan()
    assert result.created == 5 and result.parse_errors == 0
    found = await rows()
    assert all(r.status == "ok" and r.parse_error_message is None for r in found.values())

    # A number from a fraction, a range, a text quantity, a note; seq in document order.
    porridge = found["copper-kettle-porridge.cook"]
    assert porridge.title == "Copper kettle porridge"
    assert porridge.servings is None and porridge.servings_text == "2 to 3"
    assert porridge.front_matter == {
        "title": "Copper kettle porridge",
        "servings": "2 to 3",
        "tags": ["breakfast"],
    }
    got = await lines(porridge.id)
    assert [line.seq for line in got] == [1, 2, 3, 4, 5, 6]
    assert [line.raw_name for line in got] == [
        "steel cut oats",
        "milk",
        "water",
        "maple syrup",
        "toasted walnuts",
        "blueberries",
    ]
    oats, milk, _water, syrup, walnuts, berries = got
    assert oats.qty_kind == "number" and oats.qty == Decimal(2) / Decimal(3)
    assert oats.unit_text == "cup" and oats.unit == "cup"
    assert milk.qty == Decimal("1.5") and milk.unit_text == "cups" and milk.unit == "cup"
    assert syrup.qty_kind == "range" and (syrup.qty, syrup.qty_high) == (Decimal(1), Decimal(2))
    assert syrup.unit == "tbsp" and syrup.qty_text is None
    assert walnuts.note == "roughly chopped" and walnuts.qty == Decimal(2)
    assert berries.qty_kind == "text" and berries.qty_text == "a handful"
    assert berries.qty is None and berries.unit_text is None and berries.unit is None
    assert berries.negligible and not oats.negligible
    for line in got:
        assert line.name_norm == line.raw_name  # plain lowercase names normalize to themselves
        assert line.ingredient_id is None and line.yield_mode == "auto" and line.section is None
        # No ingredient exists yet, so nothing resolves; "water" is on the default
        # negligible list (3C), the rest wait for the resolve queue.
        expected = "negligible" if line.raw_name == "water" else "unmatched"
        assert line.resolution == expected, line.raw_name

    # An unparseable unit stays NULL with its text kept; unicode fractions; `>>` front matter.
    skillet = found["thunder-pepper-skillet.cook"]
    assert skillet.title == "Thunder pepper skillet" and skillet.servings == Decimal(2)
    assert skillet.front_matter == {
        "title": "Thunder pepper skillet",
        "servings": "2",
        "tags": "supper, one pan",
    }
    got = by_name(await lines(skillet.id))
    assert got["garlic"].unit is None and got["garlic"].unit_text == "cloves"
    assert got["garlic"].qty == Decimal(3) and got["garlic"].note == "crushed"
    assert got["chicken thighs"].qty_kind == "range"
    assert (got["chicken thighs"].qty, got["chicken thighs"].qty_high) == (Decimal(2), Decimal(3))
    assert got["chicken thighs"].note == "skin on"
    assert got["onion"].qty == Decimal("0.5") and got["chilli flakes"].qty == Decimal("0.75")
    assert got["stock"].unit == "ml" and got["bell peppers"].unit == "cup"

    # Section names, a no-quantity ingredient, and a nested front-matter key kept verbatim.
    bowl = found["meadow-barley-bowl.cook"]
    assert bowl.servings == Decimal(4) and bowl.servings_text == "4"
    assert bowl.front_matter["source"] == {
        "kind": "invented",
        "note": "synthetic fixture for the kitchen-erp test suite",
    }
    assert bowl.front_matter["tags"] == ["grain", "weeknight"]
    got = await lines(bowl.id)
    assert [(line.seq, line.section, line.raw_name) for line in got] == [
        (1, "Grain", "pearl barley"),
        (2, "Grain", "water"),
        (3, "Grain", "salt"),
        (4, "Dressing", "lemon juice"),
        (5, "Dressing", "olive oil"),
        (6, "Dressing", "honey"),
        (7, "Dressing", "parsley"),
        (8, "Dressing", "feta"),
    ]
    salt = got[2]
    assert salt.qty_kind == "none" and salt.qty is None and salt.qty_text is None
    assert salt.negligible and salt.unit_text is None
    assert salt.resolution == "negligible"  # by name as well as by quantity (3C)
    assert got[6].note == "chopped" and got[6].negligible  # parsley, "a handful"
    assert got[4].note == "the peppery kind"

    # Nested keys two levels down, stored as strings.
    crumble = found["quiet-orchard-crumble.cook"]
    assert crumble.front_matter["oven"] == {"temperature": "180 C", "rack": "middle"}
    assert crumble.front_matter["time"] == {"prep": "15 minutes", "bake": "40 minutes"}
    assert crumble.servings == Decimal(6)
    got = by_name(await lines(crumble.id))
    assert got["butter"].note == "cold, cubed" and got["cinnamon"].qty == Decimal("0.5")
    assert len(got) == 7

    # No front matter at all: the humanized stem stands in for the title.
    soup = found["saltmarsh-noodle-soup.cook"]
    assert soup.title == "Saltmarsh noodle soup"
    assert soup.front_matter == {} and soup.servings is None and soup.servings_text is None
    got = by_name(await lines(soup.id))
    assert got["spring onions"].qty_kind == "none" and got["spring onions"].negligible
    assert got["ginger"].qty_text == "a thumb" and got["ginger"].note == "sliced"
    assert got["vegetable stock"].qty == Decimal("1.5") and got["vegetable stock"].unit == "l"

    # A second scan re-parses nothing and leaves every row alone.
    ids = {name: [line.id for line in await lines(r.id)] for name, r in found.items()}
    assert (await run_scan()).changed == 0
    assert {
        name: [line.id for line in await lines(r.id)] for name, r in (await rows()).items()
    } == ids


async def test_front_matter_title_wins_over_the_stem_and_the_stem_is_humanized(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("soups/winter_root-soup.cook", "Simmer @parsnip{2}.\n")
    recipes_repo.write("named.cook", "---\ntitle: A name of its own\n---\n\nStir @oats{1%cup}.\n")
    recipes_repo.write("blank-title.cook", "---\ntitle: '  '\n---\n\nStir @oats{1%cup}.\n")
    await run_scan()
    found = await rows()
    assert found["soups/winter_root-soup.cook"].title == "Winter root soup"
    assert found["named.cook"].title == "A name of its own"
    assert found["blank-title.cook"].title == "Blank title"


async def test_a_pure_move_refreshes_a_stem_title_but_not_a_front_matter_one(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("first.cook", "Stir @oats{1%cup}.\n")
    recipes_repo.write("second.cook", "---\ntitle: Kept\n---\n\nStir @oats{1%cup}.\n")
    await run_scan()
    recipes_repo.move("first.cook", "renamed-dish.cook")
    recipes_repo.move("second.cook", "other.cook")
    assert (await run_scan()).moved == 2
    found = await rows()
    assert found["renamed-dish.cook"].title == "Renamed dish"
    assert found["other.cook"].title == "Kept"


# --- criterion 4 ---------------------------------------------------------------------


async def test_criterion_4_a_file_that_stops_parsing_keeps_its_rows_until_fixed(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("lamp-oil-noodles.cook", GOOD)
    recipes_repo.commit("good")
    await run_scan()
    good = (await rows())["lamp-oil-noodles.cook"]
    good_lines = await lines(good.id)
    assert [line.raw_name for line in good_lines] == ["noodles", "sesame oil", "scallions"]
    assert good.servings == Decimal(2)

    # Broken: the error is located, the rows and the old parse stay, the hash moves on.
    recipes_repo.write("lamp-oil-noodles.cook", BROKEN)
    result = await run_scan()
    assert result.updated == 1 and result.parse_errors == 1
    broken = (await rows())["lamp-oil-noodles.cook"]
    assert broken.id == good.id and broken.status == "parse_error"
    assert broken.parse_error_message == "`@sesame oil{` is never closed (line 6, column 73)"
    assert broken.content_hash != good.content_hash and broken.dirty
    assert broken.last_indexed_at > good.last_indexed_at
    assert broken.title == "Lamp oil noodles" and broken.servings == Decimal(2)
    assert broken.front_matter == good.front_matter
    assert [line.id for line in await lines(broken.id)] == [line.id for line in good_lines]

    # Not retried until the file changes.
    again = await run_scan()
    assert again.updated == 0 and again.parse_errors == 0
    still = (await rows())["lamp-oil-noodles.cook"]
    assert still.status == "parse_error" and still.last_indexed_at == broken.last_indexed_at
    assert still.parse_error_message == broken.parse_error_message
    async with get_sessionmaker()() as db:
        status = await recipes.status(db)
    assert status.counts.parse_error == 1 and status.counts.ok == 0

    # Fixed: back to ok, rows replaced.
    recipes_repo.write("lamp-oil-noodles.cook", FIXED)
    result = await run_scan()
    assert result.updated == 1 and result.parse_errors == 0
    fixed = (await rows())["lamp-oil-noodles.cook"]
    assert fixed.id == good.id and fixed.status == "ok" and fixed.parse_error_message is None
    assert fixed.title == "Lamp oil noodles, fixed" and fixed.servings == Decimal(3)
    fixed_lines = await lines(fixed.id)
    assert [line.raw_name for line in fixed_lines] == [
        "noodles",
        "sesame oil",
        "chilli oil",
        "scallions",
    ]
    assert fixed_lines[0].qty == Decimal(250)
    assert not {line.id for line in fixed_lines} & {line.id for line in good_lines}


async def test_a_new_file_that_never_parsed_has_no_rows_and_a_stem_title(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("broken-from-birth.cook", BROKEN)
    recipes_repo.path("not-text.cook").write_bytes(b"Stir @oats{1%cup}.\n\xff\xfe oops\n")
    recipes_repo.age("not-text.cook")
    result = await run_scan()
    assert result.created == 2 and result.parse_errors == 2
    found = await rows()
    row = found["broken-from-birth.cook"]
    assert row.status == "parse_error" and row.title == "Broken from birth"
    assert row.front_matter is None and row.servings is None
    assert "(line 6, column 73)" in row.parse_error_message
    assert await lines(row.id) == []
    binary = found["not-text.cook"]
    assert binary.status == "parse_error"
    assert binary.parse_error_message == "file is not UTF-8 text (line 2, column 1)"


# --- relink proposals from real titles and ingredient sets ---------------------------


async def test_a_relink_is_proposed_by_ingredient_overlap_when_titles_differ(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write(
        "alpha.cook",
        "---\ntitle: Alpha stew\n---\n\nSimmer @celeriac{1}, @leek{2} and @barley{100%g}.\n",
    )
    recipes_repo.commit("alpha")
    await run_scan()
    old = (await rows())["alpha.cook"]
    recipes_repo.move("alpha.cook", "zeta.cook")
    recipes_repo.write(
        "zeta.cook",
        "---\ntitle: Zeta bake\n---\n\n"
        "Bake @celeriac{1}, @leek{2}, @spelt{100%g} and @thyme{some}.\n",
    )
    result = await run_scan()
    assert result.created == 1 and result.missing == 1 and result.proposals == 1
    found = await rows()
    assert found["alpha.cook"].relink_candidate_id == found["zeta.cook"].id
    assert found["alpha.cook"].relink_reason == "2/3 ingredients in common"

    # Confirming carries the new file's rows to the old id and drops the new row.
    async with get_sessionmaker()() as db:
        await recipes.relink(db, old.id, found["zeta.cook"].id)
    found = await rows()
    assert list(found) == ["zeta.cook"] and found["zeta.cook"].id == old.id
    assert found["zeta.cook"].title == "Zeta bake"
    assert [line.raw_name for line in await lines(old.id)] == [
        "celeriac",
        "leek",
        "spelt",
        "thyme",
    ]


async def test_a_relink_is_proposed_by_title_when_ingredients_differ(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write(
        "one.cook", "---\ntitle: Harvest moon pie\n---\n\nMix @pumpkin{500%g} and @cream{200%ml}.\n"
    )
    recipes_repo.commit("one")
    await run_scan()
    recipes_repo.move("one.cook", "two.cook")
    recipes_repo.write(
        "two.cook",
        "---\ntitle: Harvest moon pie II\n---\n\nMix @squash{500%g} and @milk{200%ml}.\n",
    )
    assert (await run_scan()).proposals == 1
    found = await rows()
    assert found["one.cook"].relink_candidate_id == found["two.cook"].id
    assert found["one.cook"].relink_reason.startswith("title similarity")


async def test_a_new_file_that_fails_to_parse_contributes_no_ingredients(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write(
        "alpha.cook",
        "---\ntitle: Alpha stew\n---\n\nSimmer @celeriac{1}, @leek{2} and @barley{100%g}.\n",
    )
    recipes_repo.commit("alpha")
    await run_scan()
    recipes_repo.move("alpha.cook", "zeta.cook")
    recipes_repo.write(
        "zeta.cook",
        "---\ntitle: Zeta bake\n---\n\nBake @celeriac{1}, @barley{100%g} and @leek{2\n",
    )
    result = await run_scan()
    assert result.created == 1 and result.parse_errors == 1 and result.proposals == 0
    assert (await rows())["alpha.cook"].relink_candidate_id is None


async def test_relinking_to_a_target_without_rows_keeps_the_last_good_rows(
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("one.cook", "---\ntitle: Harvest moon pie\n---\n\nMix @pumpkin{500%g}.\n")
    recipes_repo.commit("one")
    await run_scan()
    old = (await rows())["one.cook"]
    recipes_repo.move("one.cook", "two.cook")
    recipes_repo.write("two.cook", "---\ntitle: Harvest moon pie II\n---\n\nMix @pumpkin{500%g.\n")
    await run_scan()
    found = await rows()
    async with get_sessionmaker()() as db:
        await recipes.relink(db, old.id, found["two.cook"].id)
    row = (await rows())["two.cook"]
    assert row.id == old.id and row.status == "parse_error"
    assert [line.raw_name for line in await lines(old.id)] == ["pumpkin"]


# --- migration 0043 -----------------------------------------------------------------


async def test_migration_0043_round_trips(owner_conn: asyncpg.Connection):
    async def has_note() -> bool:
        return bool(
            await owner_conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'recipe_ingredient' AND column_name = 'note'"
            )
        )

    assert await has_note()
    run_alembic("downgrade", "0042")
    assert not await has_note()
    run_alembic("upgrade", "head")
    assert await has_note()
    assert await owner_conn.fetchval("SELECT version_num FROM alembic_version") >= "0043"
    await dispose_engine()  # pooled connections may hold plans against the old column set


# --- the API -------------------------------------------------------------------------


async def test_the_detail_lists_ingredient_rows_with_decimals_as_strings(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    seed_parser_fixtures(recipes_repo)
    r = await admin_client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200 and r.json()["parse_errors"] == 0
    items = {i["path"]: i for i in (await admin_client.get("/api/v1/recipes")).json()["items"]}
    assert items["copper-kettle-porridge.cook"]["servings"] is None
    assert items["copper-kettle-porridge.cook"]["servings_text"] == "2 to 3"
    assert items["meadow-barley-bowl.cook"]["servings"] == "4"

    detail = (
        await admin_client.get(f"/api/v1/recipes/{items['thunder-pepper-skillet.cook']['id']}")
    ).json()
    assert detail["servings"] == "2" and detail["servings_text"] == "2"
    assert detail["front_matter"]["tags"] == "supper, one pan"
    assert detail["parse_error_message"] is None
    assert [i["seq"] for i in detail["ingredients"]] == [1, 2, 3, 4, 5, 6]
    thighs, onion, peppers, garlic, flakes, stock = detail["ingredients"]
    assert set(thighs) == {
        "id",
        "seq",
        "section",
        "raw_name",
        "name_norm",
        "qty_kind",
        "qty",
        "qty_high",
        "qty_text",
        "unit_text",
        "unit",
        "note",
        "negligible",
        "resolution",
        "ingredient_id",
        "ingredient_name",
    }
    assert thighs["qty_kind"] == "range" and thighs["qty"] == "2" and thighs["qty_high"] == "3"
    assert thighs["note"] == "skin on" and thighs["section"] is None
    assert onion["qty"] == "0.5" and onion["unit"] is None and onion["unit_text"] is None
    assert peppers["qty"] == "1.5" and peppers["unit_text"] == "cups" and peppers["unit"] == "cup"
    assert garlic["unit"] is None and garlic["unit_text"] == "cloves"
    assert flakes["qty"] == "0.75" and stock["unit"] == "ml"
    for line in detail["ingredients"]:
        assert line["resolution"] == "unmatched" and line["ingredient_id"] is None
        assert line["ingredient_name"] is None
        assert isinstance(line["qty"], str | type(None))
    assert detail["pins"] == []

    bowl = (
        await admin_client.get(f"/api/v1/recipes/{items['meadow-barley-bowl.cook']['id']}")
    ).json()
    assert bowl["front_matter"]["source"]["kind"] == "invented"
    assert [(i["section"], i["raw_name"]) for i in bowl["ingredients"]][:3] == [
        ("Grain", "pearl barley"),
        ("Grain", "water"),
        ("Grain", "salt"),
    ]
    assert bowl["ingredients"][2]["qty_kind"] == "none" and bowl["ingredients"][2]["negligible"]
    assert (
        bowl["ingredients"][6]["qty_kind"] == "text"
        and bowl["ingredients"][6]["qty_text"] == "a handful"
    )


async def test_the_detail_of_a_broken_file_still_shows_the_last_good_rows(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("lamp-oil-noodles.cook", GOOD)
    await admin_client.post("/api/v1/recipes/rescan")
    (item,) = (await admin_client.get("/api/v1/recipes")).json()["items"]
    recipes_repo.write("lamp-oil-noodles.cook", BROKEN)
    assert (await admin_client.post("/api/v1/recipes/rescan")).json()["parse_errors"] == 1
    detail = (await admin_client.get(f"/api/v1/recipes/{item['id']}")).json()
    assert detail["status"] == "parse_error"
    assert detail["parse_error_message"].endswith("(line 6, column 73)")
    assert [i["raw_name"] for i in detail["ingredients"]] == ["noodles", "sesame oil", "scallions"]
    assert detail["title"] == "Lamp oil noodles"
    listed = (await admin_client.get("/api/v1/recipes?status=parse_error")).json()["items"]
    assert [i["id"] for i in listed] == [item["id"]]
