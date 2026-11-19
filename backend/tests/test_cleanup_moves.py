"""Tidy the catalog's move check (#525): a bottle under the generic word is offered
a move to the more specific standard entry its name names. Stores, products and
recipes are invented; the geography is synthetic."""

from __future__ import annotations

import uuid

import asyncpg
import httpx
from hypothesis import given
from hypothesis import strategies as st

from app.catalog import specificity, standard
from app.catalog.standard_match import match_entry
from app.core.db import get_sessionmaker
from app.core.ids import new_id
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.pricebook_helpers import shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_catalog_cleanup import by_kind, suggestions
from tests.test_posted_prices import post_listing_price
from tests.test_recipe_costing import cost, days_ago, indexed, lines_by_name, market


async def spirits(client, db_session, *keys: str) -> dict[str, dict]:
    """The standard entries' ingredients, by key, as a person linking them would make."""
    await seed_units_via_service(db_session)
    entries = standard.by_key()
    return {k: await make_ingredient(client, entries[k].name, standard_key=k) for k in keys}


async def bottle(client, ingredient: dict, name: str) -> dict:
    return await make_product(client, ingredient["id"], name, pack_qty="750", pack_unit="ml")


async def accept(client, s: dict, fingerprint: str | None = None) -> httpx.Response:
    return await client.post(
        "/api/v1/catalog/cleanup/apply",
        json={"keys": [s["key"]], "fingerprints": {s["key"]: fingerprint or s["fingerprint"]}},
    )


async def moves(client) -> list[dict]:
    return by_kind(await suggestions(client), "product_move")


# --- the rule ---------------------------------------------------------------------------


def test_every_standard_name_and_spelling_still_means_its_own_entry():
    """The E1 spellings ("prosecco sparkling wine", "curaçao orange liqueur") change how
    only those two forms resolve: every form of the list means its own entry."""
    wrong = [
        (e.key, form, found.key if found else None)
        for e in standard.standard_list().ingredients
        for form in specificity.entry_forms(e)
        if (found := match_entry(form)) is None or found.key != e.key
    ]
    assert wrong == []
    entries = standard.by_key()
    assert "prosecco sparkling wine" in entries["prosecco"].spellings
    assert "curaçao orange liqueur" in entries["orange-curacao"].spellings


def test_the_forms_the_move_check_relies_on_are_in_the_list():
    entries = standard.by_key()
    assert "bourbon whiskey" in entries["bourbon"].spellings
    assert "whisky" in entries["whiskey"].spellings
    assert "scotch whiskey" in entries["scotch-whisky"].spellings
    for key in ("dark-rum", "rum", "sparkling-wine", "orange-liqueur", "orange-curacao"):
        assert key in entries


def _forms(key: str) -> tuple[str, ...]:
    return specificity.entry_forms(standard.by_key()[key])


def test_narrower_follows_the_head_and_a_strict_superset_of_qualifiers():
    assert specificity.is_narrower(_forms("bourbon"), _forms("whiskey"))
    assert specificity.is_narrower(_forms("scotch-whisky"), _forms("whiskey"))
    assert specificity.is_narrower(_forms("dark-rum"), _forms("rum"))
    assert specificity.is_narrower(_forms("orange-curacao"), _forms("orange-liqueur"))
    assert specificity.is_narrower(_forms("prosecco"), _forms("sparkling-wine"))
    assert not specificity.is_narrower(
        ("orange juice",), _forms("orange liqueur".replace(" ", "-"))
    )
    assert not specificity.is_narrower(_forms("whiskey"), _forms("whiskey"))
    assert not specificity.is_narrower(_forms("whiskey"), _forms("bourbon"))


WORD = st.text(alphabet="abcdefghijklmnopqrstuvwxyzç", min_size=2, max_size=8)


@given(
    head=WORD,
    general=st.frozensets(WORD, max_size=3),
    extra=st.frozensets(WORD, min_size=1, max_size=3),
)
def test_adding_qualifiers_to_a_name_makes_it_narrower_and_never_the_reverse(head, general, extra):
    extra = extra - general - {head}
    general = general - {head}
    wide = " ".join([*sorted(general), head])
    narrow = " ".join([*sorted(general | extra), head])
    if extra:
        assert specificity.is_narrower((narrow,), (wide,))
    assert not specificity.is_narrower((wide,), (narrow,))
    # Diacritics fold: "curaçao" is "curacao" to the rule.
    assert specificity.is_narrower((narrow.replace("ç", "c"),), (wide,)) == bool(extra)


def test_the_most_specific_named_entry_wins_and_a_tie_offers_nothing():
    whiskies = specificity.narrower_entries(_forms("whiskey"), "whiskey")
    assert specificity.best_move("Harbor Islay Scotch", whiskies).key == "islay-scotch"
    assert specificity.best_move("Islay Scotch whisky", whiskies) is None
    assert specificity.best_move("Bourbon whiskey and rye whiskey sampler", whiskies) is None
    rums = specificity.narrower_entries(_forms("rum"), "rum")
    assert specificity.best_move("Dark chocolate rum balls", rums) is None
    assert specificity.best_move("Harbor white rum", rums) is None, "rum's own spelling"


# --- offered, or not --------------------------------------------------------------------


async def test_a_bourbon_bottle_under_whiskey_is_offered_a_move_to_bourbon(
    admin_client, db_session
):
    found = await spirits(admin_client, db_session, "whiskey", "bourbon")
    b = await bottle(admin_client, found["whiskey"], "Harbor Distillers Bourbon Whiskey")
    [s] = await moves(admin_client)
    assert (s["subject_id"], s["before"], s["after"]) == (b["id"], "whiskey", "bourbon")
    assert s["confidence"] == "guess" and s["consequences"] == [] and s["fingerprint"]
    assert s["reason"] == "its name says bourbon, a kind of whiskey"

    r = await accept(admin_client, s)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 1
    assert r.json()["results"][0]["key"] == s["key"]
    moved = (await admin_client.get(f"/api/v1/products/{b['id']}")).json()
    assert moved["ingredient"]["id"] == found["bourbon"]["id"]
    assert await moves(admin_client) == []
    # Accepted again from a stale page: the suggestion is gone.
    r = await accept(admin_client, s)
    assert r.status_code == 409 and r.json()["error"]["code"] == "cleanup_gone"


async def test_no_move_without_a_named_entry_with_an_ingredient(admin_client, db_session):
    found = await spirits(admin_client, db_session, "whiskey", "bourbon", "sparkling-wine")
    await bottle(admin_client, found["whiskey"], "Harbor Distillers Whiskey")  # names nothing
    await bottle(admin_client, found["bourbon"], "Pier Bourbon Whiskey")  # already there
    await bottle(admin_client, found["whiskey"], "Harbor Rye Whiskey")  # no rye whiskey ingredient
    await bottle(admin_client, found["sparkling-wine"], "Cove Brut")
    assert await moves(admin_client) == []


async def test_spellings_carry_prosecco_and_curacao_and_orange_juice_never_moves(
    admin_client, db_session
):
    found = await spirits(
        admin_client, db_session, "sparkling-wine", "prosecco", "orange-liqueur", "orange-curacao"
    )
    await bottle(admin_client, found["sparkling-wine"], "Harbor Prosecco")
    await bottle(admin_client, found["orange-liqueur"], "Pier Orange Curacao")
    await bottle(admin_client, found["orange-liqueur"], "Valencia orange juice")
    offered = {s["subject"]: s["after"] for s in await moves(admin_client)}
    assert offered == {"Harbor Prosecco": "prosecco", "Pier Orange Curacao": "orange curaçao"}


async def test_a_skipped_move_is_gone_when_accepted(admin_client, db_session):
    found = await spirits(admin_client, db_session, "rum", "dark-rum")
    await bottle(admin_client, found["rum"], "Cove Dark Rum")
    [s] = await moves(admin_client)
    r = await admin_client.post("/api/v1/catalog/cleanup/skip", json={"keys": [s["key"]]})
    assert r.status_code == 204, r.text
    r = await accept(admin_client, s)
    assert r.status_code == 409 and r.json()["error"]["code"] == "cleanup_gone"
    assert (
        r.json()["error"]["message"] == "This suggestion changed or was skipped; reload the list."
    )


# --- what it changes --------------------------------------------------------------------

DRAM = "---\ntitle: Harbor dram\n---\n\nPour @whiskey{60%ml}.\n"
SOUR = "---\ntitle: Pier sour\n---\n\nShake @bourbon{45%ml}.\n"


async def test_the_reason_names_lines_that_lose_their_price_and_pins_that_stay(
    admin_client,
    db_session,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    found = await spirits(admin_client, db_session, "whiskey", "bourbon")
    b = await bottle(admin_client, found["whiskey"], "Harbor Distillers Bourbon Whiskey")
    await post_listing_price(loc, b, "30.00")
    dram = await indexed(admin_client, recipes_repo, "harbor-dram.cook", DRAM)
    assert lines_by_name(await cost(admin_client, dram["id"]))["whiskey"]["status"] == "posted"
    [s] = await moves(admin_client)
    assert s["reason"] == (
        "its name says bourbon, a kind of whiskey; 1 recipe line naming whiskey loses its "
        "price; pin it or log a whiskey price"
    )
    assert s["consequences"] == [
        {"text": "Harbor dram: whiskey loses its price", "recipe_id": dram["id"]}
    ]

    r = await admin_client.put(
        f"/api/v1/recipes/{dram['id']}/pins/whiskey", json={"product_id": b["id"]}
    )
    assert r.status_code == 200, r.text
    # The page still shows the old consequences: the accept is refused with the new ones.
    r = await accept(admin_client, s)
    assert r.status_code == 409 and r.json()["error"]["code"] == "cleanup_changed"
    fresh = r.json()["error"]["details"]["suggestion"]
    assert {"text": "Harbor dram: whiskey stays pinned to it, now under bourbon",
            "recipe_id": dram["id"]} in fresh["consequences"]  # fmt: skip

    r = await accept(admin_client, fresh)
    assert r.status_code == 200, r.text
    line = lines_by_name(await cost(admin_client, dram["id"]))["whiskey"]
    assert line["pinned"] is True and line["status"] == "unpriced"
    assert line["hint"]["kind"] == "pin_moved"


async def test_a_move_recomputes_recipes_on_both_ingredients(
    admin_client,
    db_session,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    found = await spirits(admin_client, db_session, "whiskey", "bourbon")
    moving = await bottle(admin_client, found["whiskey"], "Harbor Distillers Bourbon Whiskey")
    staying = await bottle(admin_client, found["whiskey"], "Harbor Distillers Whiskey")
    await shelf(admin_client, moving["id"], loc["id"], "45.00", observed_at=days_ago(3))
    await shelf(
        admin_client, staying["id"], loc["id"], "15.00"
    )  # the newer: the line's observation
    dram = await indexed(admin_client, recipes_repo, "harbor-dram.cook", DRAM)
    sour = await indexed(admin_client, recipes_repo, "pier-sour.cook", SOUR)
    # An average over both bottles: $30.00 a bottle, $0.04 a ml, $2.40 for 60 ml.
    average = lines_by_name(await cost(admin_client, dram["id"], basis="average"))["whiskey"]
    assert average["consumed_cost"] == "2.4000"
    assert lines_by_name(await cost(admin_client, sour["id"]))["bourbon"]["status"] == "unpriced"

    [s] = await moves(admin_client)
    r = await accept(admin_client, s)
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["recipes_recomputed"] == 2
    # The average no longer counts the moved bottle ($15.00: $1.20), and bourbon is priced.
    average = lines_by_name(await cost(admin_client, dram["id"], basis="average"))["whiskey"]
    assert average["consumed_cost"] == "1.2000"
    bourbon = lines_by_name(await cost(admin_client, sour["id"]))["bourbon"]
    assert bourbon["status"] == "priced" and bourbon["consumed_cost"] == "2.7000"


async def test_a_failed_move_changes_nothing(admin_client, db_session, monkeypatch):
    from app.services import cleanup_moves

    found = await spirits(admin_client, db_session, "whiskey", "bourbon")
    b = await bottle(admin_client, found["whiskey"], "Harbor Distillers Bourbon Whiskey")
    [s] = await moves(admin_client)

    async def breaks(*_args, **_kwargs):
        raise RuntimeError("the recompute broke")

    monkeypatch.setattr(cleanup_moves.recipe_cost_triggers, "after_recipes_changed", breaks)
    r = await accept(admin_client, s)
    assert r.status_code == 500 and r.json()["error"]["code"] == "cleanup_failed"
    assert r.json()["error"]["message"] == "The move failed, and nothing changed."
    still = (await admin_client.get(f"/api/v1/products/{b['id']}")).json()
    assert still["ingredient"]["id"] == found["whiskey"]["id"]
    assert [m["key"] for m in await moves(admin_client)] == [s["key"]]


async def test_two_accepts_racing_move_once(
    admin_client, db_session, owner_conn: asyncpg.Connection
):
    found = await spirits(admin_client, db_session, "whiskey", "bourbon")
    await bottle(admin_client, found["whiskey"], "Harbor Distillers Bourbon Whiskey")
    [s] = await moves(admin_client)
    import asyncio

    first, second = await asyncio.gather(accept(admin_client, s), accept(admin_client, s))
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], (first.text, second.text)
    loser = first if first.status_code == 409 else second
    assert loser.json()["error"]["code"] == "cleanup_gone"
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM product WHERE ingredient_id = $1",
            uuid.UUID(found["bourbon"]["id"]),
        )
        == 1
    )


# --- the inbox's landing count ------------------------------------------------------------


async def test_the_move_check_adds_under_50_ms_to_the_count_and_reads_products_once(
    db_session, owner_conn: asyncpg.Connection
):
    """The inbox counts Tidy the catalog on every landing load (#327): the standard
    list's ingredients, 2,000 products, a tenth of them bottles named for a narrower
    entry."""
    import statistics
    import time

    from sqlalchemy import event

    from app.services import catalog_cleanup, cleanup_moves

    await seed_units_via_service(db_session)
    entries = standard.standard_list().ingredients
    ingredients = [(new_id(), e.name, e.unit, e.key, e.category) for e in entries]
    await owner_conn.executemany(
        "INSERT INTO ingredient (id, name, canonical_unit, slug, category) "
        "VALUES ($1, $2, $3, $4, $5)",
        ingredients,
    )
    by_key = {key: iid for iid, _, _, key, _ in ingredients}
    products = []
    for n in range(2000):
        if n % 10 == 0:
            products.append((new_id(), by_key["whiskey"], f"Harbor Bourbon Whiskey {n}"))
        else:
            iid, name, *_ = ingredients[n % len(ingredients)]
            products.append((new_id(), iid, f"Pier {name} {n}"))
    await owner_conn.executemany(
        "INSERT INTO product (id, ingredient_id, name, kind) VALUES ($1, $2, $3, 'branded')",
        products,
    )
    await owner_conn.execute("ANALYZE ingredient; ANALYZE product")

    async with get_sessionmaker()() as db:
        statements: list[str] = []
        engine = (await db.connection()).engine.sync_engine

        def seen(_conn, _cursor, statement, *_args):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", seen)
        try:
            n = await catalog_cleanup.count(db)
        finally:
            event.remove(engine, "before_cursor_execute", seen)
        assert n >= 200, "every bottle under whiskey is offered bourbon"
        from_product = [s for s in statements if "FROM product JOIN ingredient" in s]
        assert len(from_product) == 1, from_product

        # The move check's share, as the count runs it: the products already read.
        rows = [r for r in await catalog_cleanup._product_rows(db) if r.ingredient_live]
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            await cleanup_moves.product_moves(db, guess=False, rows=rows)
            timings.append((time.perf_counter() - started) * 1000)
    assert statistics.median(timings) < 50, timings
