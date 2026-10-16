"""Tidy the catalog: sure and guessed suggestions, misfiled categories, kept guesses
(issue 372). Everything is invented."""

from __future__ import annotations

from app.core.ids import new_id
from tests.catalog_helpers import clear_category, make_ingredient, seed_units_via_service
from tests.conftest import run_alembic
from tests.test_catalog_cleanup import by_kind, suggestions


async def apply(client, keys: list[str], chosen: dict[str, str] | None = None) -> dict:
    r = await client.post(
        "/api/v1/catalog/cleanup/apply", json={"keys": keys, "chosen": chosen or {}}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def category_of(client, ingredient: dict) -> str | None:
    return (await client.get(f"/api/v1/ingredients/{ingredient['id']}")).json()["category"]


async def _sure_and_guessed(client) -> tuple[dict, dict, dict]:
    """An uncategorized fish (its name settles it) and an uncategorized seed whose
    category is only guessed from the household's other seeds."""
    await make_ingredient(client, "smoked lanternseed", category="spices")
    await make_ingredient(client, "lanternseed", category="spices")
    fish = await clear_category(client, await make_ingredient(client, "lantern trout"))
    seed = await clear_category(client, await make_ingredient(client, "hot lanternseed"))
    rows = {s["subject_id"]: s for s in by_kind(await suggestions(client), "category")}
    return fish, seed, rows


async def test_a_category_from_the_name_is_sure_and_one_from_similar_ingredients_a_guess(
    admin_client, db_session
):
    await seed_units_via_service(db_session)
    fish, seed, rows = await _sure_and_guessed(admin_client)
    assert (rows[fish["id"]]["value"], rows[fish["id"]]["confidence"]) == ("seafood", "sure")
    assert (rows[seed["id"]]["value"], rows[seed["id"]]["confidence"]) == ("spices", "guess")
    assert rows[seed["id"]]["reason"] == "a guess from similar ingredients"


async def test_accepting_several_never_applies_a_guess(admin_client, db_session):
    await seed_units_via_service(db_session)
    fish, seed, rows = await _sure_and_guessed(admin_client)
    sure, guess = rows[fish["id"]]["key"], rows[seed["id"]]["key"]
    result = await apply(admin_client, [sure, guess])
    assert (result["applied"], result["held"]) == (1, [guess])
    assert await category_of(admin_client, fish) == "seafood"
    assert await category_of(admin_client, seed) is None
    # On its own, after a glance, the guess is applied.
    assert (await apply(admin_client, [guess]))["applied"] == 1
    assert await category_of(admin_client, seed) == "spices"


async def test_a_guess_with_a_category_chosen_is_a_persons_choice(admin_client, db_session):
    await seed_units_via_service(db_session)
    fish, seed, rows = await _sure_and_guessed(admin_client)
    sure, guess = rows[fish["id"]]["key"], rows[seed["id"]]["key"]
    result = await apply(admin_client, [sure, guess], {guess: "pantry"})
    assert (result["applied"], result["held"]) == (2, [])
    assert await category_of(admin_client, seed) == "pantry"


async def test_a_fish_filed_under_meat_is_offered_seafood(admin_client, db_session):
    await seed_units_via_service(db_session)
    trout = await make_ingredient(admin_client, "lantern trout", category="meat")
    await make_ingredient(admin_client, "lantern fish sauce", category="pantry")
    [s] = by_kind(await suggestions(admin_client), "category")
    assert (s["subject_id"], s["before"], s["value"]) == (trout["id"], "meat", "seafood")
    assert (s["confidence"], s["reason"]) == ("sure", "the name says it's a fish or shellfish")
    await apply(admin_client, [s["key"]])
    assert await category_of(admin_client, trout) == "seafood"


async def _link(owner_conn, ingredient: dict, fdc_id: int) -> None:
    await owner_conn.execute(
        "INSERT INTO ingredient_ref (id, ingredient_id, system, external_id, is_preferred) "
        "VALUES ($1, $2, 'fdc', $3, true)",
        new_id(),
        ingredient["id"],
        str(fdc_id),
    )


async def _foods(owner_conn) -> None:
    await owner_conn.executemany(
        "INSERT INTO fdc_food (fdc_id, data_type, description, category) VALUES ($1, $2, $3, $4)",
        [
            (900061, "sr_legacy_food", "Watermelon, raw", "Fruits and Fruit Juices"),
            (900062, "foundation_food", "Lanternbeans, raw", "Legumes and Legume Products"),
            (900063, "sr_legacy_food", "Lanternpeas", "Vegetables and Vegetable Products"),
        ],
    )


async def test_a_relink_to_a_raw_foundation_record_is_sure_and_any_other_a_guess(
    admin_client, db_session, owner_conn
):
    await seed_units_via_service(db_session)
    await _foods(owner_conn)
    beans = await make_ingredient(admin_client, "lanternbeans")
    peas = await make_ingredient(admin_client, "lanternpeas")
    await _link(owner_conn, beans, 900061)
    await _link(owner_conn, peas, 900061)
    rows = {s["subject_id"]: s for s in by_kind(await suggestions(admin_client), "usda_link")}
    assert (rows[beans["id"]]["after"], rows[beans["id"]]["confidence"]) == (
        "Lanternbeans, raw",
        "sure",
    )
    assert (rows[peas["id"]]["after"], rows[peas["id"]]["confidence"]) == ("Lanternpeas", "guess")


async def test_the_usda_guesses_are_kept_until_the_catalog_version_changes(
    admin_client, db_session, owner_conn
):
    await seed_units_via_service(db_session)
    await _foods(owner_conn)
    beans = await make_ingredient(admin_client, "lanternbeans")
    await _link(owner_conn, beans, 900061)
    [s] = by_kind(await suggestions(admin_client), "usda_link")
    assert s["after"] == "Lanternbeans, raw"
    kept = await owner_conn.fetchrow(
        "SELECT fdc_id, version FROM cleanup_guess WHERE kind = 'usda_link' AND name = $1",
        "lanternbeans",
    )
    assert kept["fdc_id"] == 900062
    # The page reads what is kept rather than searching again...
    await owner_conn.execute("UPDATE cleanup_guess SET fdc_id = 900063")
    [s] = by_kind(await suggestions(admin_client), "usda_link")
    assert s["after"] == "Lanternpeas"
    # ...until a USDA release is loaded, which guesses again.
    await owner_conn.execute(
        "INSERT INTO fdc_release (id, source_name, foods, portions) VALUES ($1, 'invented', 3, 0)",
        new_id(),
    )
    [s] = by_kind(await suggestions(admin_client), "usda_link")
    assert s["after"] == "Lanternbeans, raw"
    versions = await owner_conn.fetch("SELECT DISTINCT version FROM cleanup_guess")
    assert [v["version"] for v in versions] != [kept["version"]]


def test_the_guess_migration_applies_and_reverses():
    run_alembic("downgrade", "0059")
    run_alembic("upgrade", "head")
