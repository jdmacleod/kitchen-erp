"""The rest of the matching cascade (07, 3C; VS2, package 5): prep-word stripping,
the USDA pool and the local model, after the standard and similar tiers.

Every tier only proposes: a line resolves without a person through an exact
name, spelling or inflection and nothing else (VC3). The queue caps proposals
at three per name, higher tiers first, each badged by its tier (UI-7.16). The
model is replayed from recorded answers (the live run is ``-m llm``); its reply
counts only when it validates against the shortlist schema. Recipes,
ingredients and USDA foods here are invented.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.ingest.llm import BEGIN_DELIMITER, END_DELIMITER, RECIPE_NAMES_SYSTEM_PROMPT
from app.models import RecipeIngredient
from app.models.catalog import FdcFood, IngredientRef
from app.recipes.prep import Stripped, form_words, prep_words, strip_prep
from app.services import recipe_resolution
from tests.catalog_helpers import make_ingredient
from tests.ingest_helpers import recorded  # noqa: F401
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_recipe_resolution import decide, lines_of, queue, recipes_by_path, rescan

TIER_RANK = {tier: i for i, tier in enumerate(recipe_resolution.TIER_ORDER)}


async def seed_foods(*rows: tuple[int, str, str, int]) -> None:
    """Invented FoodData Central rows: (fdc_id, data_type, description, survey uses)."""
    async with get_sessionmaker()() as db:
        for fdc_id, data_type, description, uses in rows:
            db.add(
                FdcFood(
                    fdc_id=fdc_id, data_type=data_type, description=description, fndds_uses=uses
                )
            )
        await db.commit()


async def ask(client: httpx.AsyncClient, name_norm: str) -> httpx.Response:
    return await client.post("/api/v1/recipes/resolve/ask", json={"name_norm": name_norm})


async def notes_of(name_norm: str) -> list[str | None]:
    async with get_sessionmaker()() as db:
        rows = await db.execute(
            select(RecipeIngredient.note)
            .where(RecipeIngredient.name_norm == name_norm)
            .order_by(RecipeIngredient.note)
        )
        return list(rows.scalars())


def assert_ordered(proposals: list[dict]) -> None:
    ranks = [TIER_RANK[p["tier"]] for p in proposals]
    assert ranks == sorted(ranks), [p["tier"] for p in proposals]
    assert len(proposals) <= recipe_resolution.MAX_PROPOSALS


# --- prep-word stripping, pure ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("minced garlic", Stripped("garlic", ("minced",))),
        ("garlic minced", Stripped("garlic", ("minced",))),
        ("finely chopped shallots", Stripped("shallots", ("finely", "chopped"))),
        ("peeled and diced carrots", Stripped("carrots", ("peeled", "and", "diced"))),
        ("fresh basil leaves chopped", Stripped("basil leaves", ("fresh", "chopped"))),
        (
            "chopped dried apricots",
            Stripped("dried apricots", ("chopped",)),
        ),  # stops at a form word
        ("ground cumin", None),  # a form word is never stripped
        ("unsalted butter", None),
        ("minced", None),  # never stripped to nothing
        ("minced and", None),
        ("olive oil", None),
    ],
)
def test_strip_prep(name: str, expected: Stripped | None) -> None:
    assert strip_prep(name) == expected


def test_prep_and_form_words_are_disjoint_data_files() -> None:
    assert prep_words() and form_words()
    assert not prep_words() & form_words()
    for word in prep_words() | form_words():
        assert word == word.lower() == word.strip() and " " not in word


# --- the prep tier --------------------------------------------------------------------


async def test_prep_tier_proposes_the_stripped_name_and_the_decision_writes_the_note(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    garlic = await make_ingredient(admin_client, "Garlic")
    recipes_repo.write("stew.cook", "Soften @minced garlic{2%cloves} in oil.\n")
    recipes_repo.write("sauce.cook", "Add @Minced garlic{1%clove}(for the sauce) at the end.\n")
    recipes_repo.write("salad.cook", "Scatter @finely chopped shallots{2} over the top.\n")
    await rescan(admin_client)
    items = {i["name_norm"]: i for i in (await queue(admin_client))["items"]}
    # The catalog has garlic: the stripped name finds it exactly. The similar
    # tier found the same ingredient by trigram; the prep proposal takes its
    # place, badged prep, carrying the stripped word as the note.
    [proposal] = [p for p in items["minced garlic"]["proposals"] if p["tier"] == "prep"]
    assert proposal["ingredient_id"] == garlic["id"] and proposal["note"] == "minced"
    assert proposal["name"] == "Garlic"
    assert not [p for p in items["minced garlic"]["proposals"] if p["tier"] == "similar"]
    # No garlic-free catalog hit for shallots: the standard entry, to create from.
    [proposal] = [p for p in items["finely chopped shallots"]["proposals"] if p["tier"] == "prep"]
    assert proposal["ingredient_id"] is None and proposal["standard_key"] == "shallot"
    assert proposal["note"] == "finely chopped"
    # Nothing resolved itself (VC3).
    for path in ("stew.cook", "sauce.cook", "salad.cook"):
        recipe = (await recipes_by_path(admin_client))[path]
        assert all(
            line["resolution"] == "unmatched"
            for line in (await lines_of(admin_client, recipe["id"])).values()
        )
    # The decision sends the proposal's note back; it goes in front of each line's own.
    r = await decide(
        admin_client, name_norm="minced garlic", ingredient_id=garlic["id"], note="minced"
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "matched" and r.json()["lines"] == 2
    assert await notes_of("minced garlic") == ["minced", "minced; for the sauce"]
    r = await decide(
        admin_client,
        name_norm="finely chopped shallots",
        ingredient={"name": "shallot", "standard_key": "shallot"},
        note="finely chopped",
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "created" and r.json()["ingredient"]["name"] == "shallot"
    assert await notes_of("finely chopped shallots") == ["finely chopped"]
    # A note goes with an ingredient, never with "not an ingredient".
    recipes_repo.write("wrap.cook", "Line a tin with @parchment paper{1%sheet}.\n")
    await rescan(admin_client)
    r = await decide(admin_client, name_norm="parchment paper", ignore=True, note="cut")
    assert r.status_code == 422


async def test_form_words_are_never_stripped(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await make_ingredient(admin_client, "Cumin")
    await make_ingredient(admin_client, "Butter")
    recipes_repo.write("rub.cook", "Mix @ground cumin{1%tsp} into @unsalted butter{50%g}.\n")
    await rescan(admin_client)
    items = {i["name_norm"]: i for i in (await queue(admin_client))["items"]}
    for name in ("ground cumin", "unsalted butter"):
        assert items[name]["proposals"] is not None
        assert not [p for p in items[name]["proposals"] if p["tier"] == "prep"], name
        assert all(p["note"] is None for p in items[name]["proposals"]), name
    rub = (await recipes_by_path(admin_client))["rub.cook"]
    lines = await lines_of(admin_client, rub["id"])
    assert lines["ground cumin"]["resolution"] == "unmatched"
    assert lines["unsalted butter"]["resolution"] == "unmatched"


# --- the USDA tier --------------------------------------------------------------------


async def test_usda_tier_proposes_foods_to_create_an_ingredient_from(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await seed_foods(
        (900001, "sr_legacy_food", "Lanternfruit, raw", 5),
        (900002, "foundation_food", "Lanternfruit, dried", 1),
        (900003, "survey_fndds_food", "Lanternfruit pie", 9),  # survey foods are not references
    )
    recipes_repo.write("tart.cook", "Fill with @diced lanternfruit{2}(ripe) and bake.\n")
    recipes_repo.write("jam.cook", "Simmer @lanternfruit{500%g}.\n")
    await rescan(admin_client)
    items = {i["name_norm"]: i for i in (await queue(admin_client))["items"]}
    # The name without its prep word is searched and proposed as the new
    # ingredient's name; the most used food first; the prep word is the note.
    proposals = items["diced lanternfruit"]["proposals"]
    assert [p["tier"] for p in proposals] == ["usda", "usda"]
    assert [p["fdc_id"] for p in proposals] == [900001, 900002]
    assert proposals[0]["fdc_description"] == "Lanternfruit, raw"
    assert proposals[0]["name"] == "lanternfruit" and proposals[0]["note"] == "diced"
    assert [p["fdc_id"] for p in items["lanternfruit"]["proposals"]] == [900001, 900002]
    assert items["lanternfruit"]["proposals"][0]["note"] is None
    tart = (await recipes_by_path(admin_client))["tart.cook"]
    assert (await lines_of(admin_client, tart["id"]))["diced lanternfruit"][
        "resolution"
    ] == "unmatched"
    # A food that is not loaded is refused; the food goes with a new ingredient only.
    r = await decide(
        admin_client,
        name_norm="diced lanternfruit",
        ingredient={"name": "lanternfruit"},
        fdc_id=123,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_fdc_id"
    r = await decide(
        admin_client, name_norm="diced lanternfruit", ingredient_id=tart["id"], fdc_id=900001
    )
    assert r.status_code == 422
    # Creating from the food: the ingredient's preferred USDA reference, so the
    # USDA review page offers its densities and measures.
    r = await decide(
        admin_client,
        name_norm="diced lanternfruit",
        ingredient={"name": "lanternfruit"},
        fdc_id=900001,
        note="diced",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "created" and body["ingredient"]["name"] == "lanternfruit"
    # "lanternfruit" is the new ingredient's own name: the other recipe settled at once.
    assert body["remaining"] == 0
    async with get_sessionmaker()() as db:
        ref = (
            await db.execute(
                select(IngredientRef).where(
                    IngredientRef.system == "fdc", IngredientRef.is_preferred
                )
            )
        ).scalar_one()
        assert str(ref.ingredient_id) == body["ingredient"]["id"]
        assert ref.external_id == "900001"
    assert await notes_of("diced lanternfruit") == ["diced; ripe"]


# --- the model tier -------------------------------------------------------------------


async def test_model_tier_offers_only_shortlist_names_that_validate(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    recorded,  # noqa: F811
):
    await make_ingredient(admin_client, "Onion")
    recipes_repo.write("soup.cook", "Wilt @wild garlic leaves{1%bunch} into the broth.\n")
    await rescan(admin_client)
    transport = recorded({"recipe_names": {"ingredients": ["garlic", "bay leaves"]}})
    # The queue never calls the model: nothing was proposed, and nothing was asked.
    items = {i["name_norm"]: i for i in (await queue(admin_client))["items"]}
    assert items["wild garlic leaves"]["proposals"] == []
    assert transport.requests == []
    # Asking about the name does, with a schema that admits only the shortlist.
    r = await ask(admin_client, "wild garlic leaves")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_asked"] is True
    assert [(p["tier"], p["standard_key"]) for p in body["proposals"]] == [
        ("model", "garlic"),
        ("model", "bay-leaves"),
    ]
    assert all(p["ingredient_id"] is None for p in body["proposals"])
    [request] = transport.requests
    schema = request["body"]["format"]
    assert schema["title"] == "RecipeNameSuggestions"
    allowed = schema["properties"]["ingredients"]["items"]
    enum = allowed.get("enum") or schema["$defs"][allowed["$ref"].rsplit("/", 1)[-1]]["enum"]
    assert {"garlic", "bay leaves"} <= set(enum) and "Onion" not in enum
    assert schema["properties"]["ingredients"]["maxItems"] == 2
    messages = request["body"]["messages"]
    assert messages[0] == {"role": "system", "content": RECIPE_NAMES_SYSTEM_PROMPT}
    block = messages[-1]["content"].split(BEGIN_DELIMITER)[1].split(END_DELIMITER)[0].strip()
    assert block == "wild garlic leaves"
    # Still unmatched: a model's guess is never applied (VC3).
    soup = (await recipes_by_path(admin_client))["soup.cook"]
    assert (await lines_of(admin_client, soup["id"]))["wild garlic leaves"][
        "resolution"
    ] == "unmatched"
    # Garbage, a name outside the shortlist, or more than two: discarded, nothing offered.
    for reply in (
        "this is not json",
        {"ingredients": ["moon cheese"]},
        {"ingredients": ["garlic"] * 3},
    ):
        recorded({"recipe_names": reply})
        r = await ask(admin_client, "wild garlic leaves")
        assert r.status_code == 200, r.text
        assert r.json() == {"name_norm": "wild garlic leaves", "proposals": [], "model_asked": True}
    assert (await ask(admin_client, "nothing here")).status_code == 404


async def test_model_tier_is_off_without_a_model_or_a_reachable_server(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    recorded,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    recipes_repo.write("soup.cook", "Wilt @wild garlic leaves{1%bunch} into the broth.\n")
    await rescan(admin_client)
    # Nothing listens at OLLAMA_BASE_URL in tests: no suggestion, no error.
    r = await ask(admin_client, "wild garlic leaves")
    assert r.status_code == 200, r.text
    assert r.json() == {"name_norm": "wild garlic leaves", "proposals": [], "model_asked": True}
    transport = recorded({"recipe_names": {"ingredients": ["garlic"]}})
    monkeypatch.setattr(get_settings(), "llm_model", "")
    r = await ask(admin_client, "wild garlic leaves")
    assert r.status_code == 200, r.text
    assert r.json() == {"name_norm": "wild garlic leaves", "proposals": [], "model_asked": False}
    assert transport.requests == []


# --- the cap and the order ------------------------------------------------------------


async def test_three_proposals_per_name_higher_tiers_first(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    recorded,  # noqa: F811
):
    await make_ingredient(admin_client, "Thai basil")
    await make_ingredient(admin_client, "Holy basil")
    await seed_foods(
        (900011, "sr_legacy_food", "Basil, fresh", 3),
        (900012, "sr_legacy_food", "Basil, dried", 1),
        (900021, "foundation_food", "Lanternfruit, raw", 5),
        (900022, "sr_legacy_food", "Lanternfruit, dried", 0),
    )
    recipes_repo.write("bowl.cook", "Top with @torn basil{1%cup} and @torn lanternfruit{1}.\n")
    await rescan(admin_client)
    items = {i["name_norm"]: i for i in (await queue(admin_client))["items"]}
    # Two similar hits, the prep tier's standard entry, then two USDA foods: cut to three.
    basil = items["torn basil"]["proposals"]
    assert_ordered(basil)
    assert [p["tier"] for p in basil] == ["similar", "similar", "prep"]
    assert {p["name"] for p in basil[:2]} == {"Thai basil", "Holy basil"}
    assert basil[2]["standard_key"] == "basil" and basil[2]["note"] == "torn"
    # No room left: asking does not reach the model.
    transport = recorded({"recipe_names": {"ingredients": ["jackfruit"]}})
    r = await ask(admin_client, "torn basil")
    assert r.status_code == 200 and r.json()["model_asked"] is False
    assert [p["tier"] for p in r.json()["proposals"]] == ["similar", "similar", "prep"]
    assert transport.requests == []
    # Nothing but the USDA pool knows lanternfruit; the model's pick fills the last place.
    fruit = items["torn lanternfruit"]["proposals"]
    assert_ordered(fruit)
    assert [(p["tier"], p["fdc_id"]) for p in fruit] == [("usda", 900021), ("usda", 900022)]
    r = await ask(admin_client, "torn lanternfruit")
    assert r.status_code == 200, r.text
    asked = r.json()["proposals"]
    assert_ordered(asked)
    assert [p["tier"] for p in asked] == ["usda", "usda", "model"]
    assert asked[2]["standard_key"] == "jackfruit" and r.json()["model_asked"] is True
    # Every line is still waiting for a person (VC3).
    bowl = (await recipes_by_path(admin_client))["bowl.cook"]
    assert {line["resolution"] for line in (await lines_of(admin_client, bowl["id"])).values()} == {
        "unmatched"
    }
