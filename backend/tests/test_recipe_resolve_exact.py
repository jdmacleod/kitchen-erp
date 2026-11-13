"""Accept every exact standard-list match in the recipe-name queue at once.

A name that is a standard entry's own name, its plural or a listed spelling is
marked ``exact`` and settles in one click with the others; a name matched only
by its head noun and qualifiers stays for a person. Recipes here are invented.
"""

from __future__ import annotations

import httpx

from tests.catalog_helpers import make_ingredient
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_recipe_resolution import queue, rescan

PANTRY = (
    "Toss @olive oil{2%tbsp} with @fresh basil{1%cup}, @lemons{2}\n"
    "and @swede{200%g}, then @rutabaga{100%g}.\n"
)


async def test_exact_standard_matches_are_marked_and_accepted_together(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    basil = await make_ingredient(admin_client, "Basil", standard_key="basil")
    recipes_repo.write("pantry-toss.cook", PANTRY)
    await rescan(admin_client)
    items = {i["name_norm"]: i for i in (await queue(admin_client))["items"]}
    standard = {
        name: next(p for p in item["proposals"] if p["tier"] == "standard")
        for name, item in items.items()
        if any(p["tier"] == "standard" for p in item["proposals"])
    }
    # Its own name, a plural and a listed spelling are exact; a qualifier is not.
    assert standard["olive oil"]["exact"] is True
    assert standard["lemons"]["exact"] is True
    assert standard["swede"]["exact"] is True
    assert standard["fresh basil"]["ingredient_id"] == basil["id"]
    assert standard["fresh basil"]["exact"] is False
    # An exact entry is the one proposal: no similar or USDA guesses beside it.
    assert [p["tier"] for p in items["olive oil"]["proposals"]] == ["standard"]

    r = await admin_client.post("/api/v1/recipes/resolve/exact")
    assert r.status_code == 200, r.text
    left = [i["name_norm"] for i in (await queue(admin_client))["items"]]
    assert left == ["fresh basil"]
    assert r.json() == {"resolved": 4, "remaining": 1}
    # Two names for one entry made one ingredient between them.
    roots = [
        i
        for i in (await admin_client.get("/api/v1/ingredients", params={"q": "rutabaga"})).json()[
            "items"
        ]
        if i["name"] == "rutabaga"
    ]
    assert len(roots) == 1
