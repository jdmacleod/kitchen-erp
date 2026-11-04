"""`>` notes: words for the cook, kept apart from the numbered steps.

A note line used to be read as a step, so the page numbered it and showed the
`>` of every line it ran over. Recipes indexed before that change are read again
with `kerp recipes rescan --reparse`.
"""

from __future__ import annotations

import httpx
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.models import Recipe
from app.recipes.cooklang import Text, parse
from app.recipes.cooklang.model import ParseError
from app.services import recipes
from app.services.recipe_pages import serialize_body
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401

NOTE = "Without an oven-safe pot, simmer it covered on the lowest heat for the same time."
STEW = """\
Heat the @olive oil{2%tbsp} in a #pot{}.

> Without an oven-safe pot, simmer it covered
> on the lowest heat for the same time.
Braise ~{2%hours}.

> A note can name @butter{} without it being an ingredient.
"""


def test_note_lines_join_into_one_unnumbered_step():
    parsed = parse(STEW)
    assert not isinstance(parsed, ParseError)
    steps = parsed.sections[0].steps
    assert [s.note for s in steps] == [False, True, False, True]
    assert steps[1].items == (Text(NOTE),)
    assert steps[2].timers  # the line straight after a note is a step of its own


def test_a_note_names_no_ingredient():
    parsed = parse(STEW)
    assert not isinstance(parsed, ParseError)
    assert [i.raw_name for i in parsed.ingredients] == ["olive oil"]
    assert "@butter{}" in parsed.sections[0].steps[3].items[0].value


def test_metadata_lines_are_still_metadata():
    parsed = parse(">> servings: 4\n> Serve warm.\n")
    assert not isinstance(parsed, ParseError)
    assert parsed.front_matter == {"servings": "4"}
    assert [s.note for s in parsed.sections[0].steps] == [True]


def test_the_body_carries_a_note_as_one_item():
    parsed = parse(STEW)
    assert not isinstance(parsed, ParseError)
    steps = serialize_body(parsed)[0]["steps"]
    assert steps[1] == [{"t": "note", "v": NOTE}]
    assert all(item["t"] != "note" for item in steps[0] + steps[2])


async def _rows() -> dict[str, Recipe]:
    async with get_sessionmaker()() as db:
        return {r.path: r for r in (await db.execute(select(Recipe))).scalars()}


async def _scan(*, reparse: bool = False):
    async with get_sessionmaker()() as db:
        return await recipes.scan(db, reparse=reparse)


async def test_reparse_reads_an_unchanged_file_again(recipes_repo: TempRepo):  # noqa: F811
    recipes_repo.write("stew.cook", STEW)
    recipes_repo.commit("stew")
    await _scan()
    # As an index written before notes were read: the note was a step.
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(Recipe))).scalar_one()
        row.body = [{"name": None, "steps": [[{"t": "text", "v": "> old"}]]}]
        await db.commit()
    plain = await _scan()
    assert plain.changed == 0
    assert (await _rows())["stew.cook"].body[0]["steps"] == [[{"t": "text", "v": "> old"}]]

    await _scan(reparse=True)
    row = (await _rows())["stew.cook"]
    assert row.body[0]["steps"][1][0]["t"] == "note"
    assert row.status == "ok" and not row.dirty


async def test_the_recipe_page_serves_a_note(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("stew.cook", STEW)
    recipes_repo.commit("stew")
    assert (await admin_client.post("/api/v1/recipes/rescan")).status_code == 200
    (listed,) = (await admin_client.get("/api/v1/recipes")).json()["items"]
    r = await admin_client.get(f"/api/v1/recipes/{listed['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["body"][0]["steps"][1] == [{"t": "note", "v": NOTE}]
