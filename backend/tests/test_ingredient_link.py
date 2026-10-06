"""The link page, rename, skip and merge (03, 1G, criteria 82, 83 and 85). Invented foods."""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from sqlalchemy import select

from app.catalog.names import normalize_name
from app.core.ids import new_id
from app.models.catalog import Ingredient, IngredientAlias, IngredientMeasure, IngredientRef
from tests.catalog_helpers import make_ingredient
from tests.pricebook_helpers import make_location, make_product, shelf


async def _unreviewed(owner_conn: asyncpg.Connection, *ids: str) -> None:
    await owner_conn.execute(
        "UPDATE ingredient SET reconcile_state = 'unreviewed' WHERE id = ANY($1::uuid[])",
        [uuid.UUID(i) for i in ids],
    )


async def _not_applicable(owner_conn: asyncpg.Connection, *ids: str) -> None:
    """As an ingredient typed in before #189, or one a person has decided, would be."""
    await owner_conn.execute(
        "UPDATE ingredient SET reconcile_state = 'not_applicable' WHERE id = ANY($1::uuid[])",
        [uuid.UUID(i) for i in ids],
    )


async def _spellings(db_session, ingredient_id) -> dict[str, tuple[str, str]]:
    rows = await db_session.execute(
        select(IngredientAlias).where(IngredientAlias.ingredient_id == ingredient_id)
    )
    return {a.name_norm: (a.kind, a.source) for a in rows.scalars()}


async def _get(db_session, ingredient_id) -> Ingredient:
    db_session.expire_all()
    return await db_session.get(Ingredient, uuid.UUID(str(ingredient_id)))


async def test_link_page_suggests_and_puts_likely_duplicates_first(admin_client, owner_conn):
    a = await make_ingredient(admin_client, "Sorrel")
    b = await make_ingredient(admin_client, "green onions", canonical_unit="each")
    c = await make_ingredient(admin_client, "Scallion")
    await _unreviewed(owner_conn, a["id"], b["id"])
    await _not_applicable(owner_conn, c["id"])
    page = (await admin_client.get("/api/v1/ingredients/link")).json()
    names = [r["name"] for r in page["to_review"]]
    assert names == ["green onions", "Sorrel"]  # the duplicate first; c isn't unreviewed
    onions = page["to_review"][0]
    assert onions["suggestion"]["key"] == "scallion"
    assert onions["conflict"] == {
        "id": c["id"],
        "name": "Scallion",
        "canonical_unit": "g",
        "products": 0,
    }
    assert page["to_review"][1]["suggestion"] is None
    summary = (await admin_client.get("/api/v1/ingredients/link/summary")).json()
    assert summary == {"to_review": 2, "skipped": 0}


async def test_link_takes_the_standard_name_and_keeps_the_old_one(
    admin_client, db_session, owner_conn
):
    ing = await make_ingredient(admin_client, "Scallions")
    await _unreviewed(owner_conn, ing["id"])
    r = await admin_client.post(
        f"/api/v1/ingredients/{ing['id']}/link", json={"standard_key": "scallion"}
    )
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["slug"]) == ("scallion", "scallion")
    row = await _get(db_session, ing["id"])
    assert row.reconcile_state == "linked"
    spellings = await _spellings(db_session, row.id)
    assert spellings["scallions"] == ("legacy", "rename")
    assert spellings["green onion"] == ("synonym", "standard")
    refs = (await db_session.execute(select(IngredientRef))).scalars().all()
    assert [(x.external_id, x.is_preferred) for x in refs] == [("170005", True)]
    summary = (await admin_client.get("/api/v1/ingredients/link/summary")).json()
    assert summary == {"to_review": 0, "skipped": 0}


async def test_link_to_a_name_another_ingredient_has_asks_to_merge(admin_client, owner_conn):
    other = await make_ingredient(admin_client, "Scallion")
    ing = await make_ingredient(admin_client, "green onions")
    await _unreviewed(owner_conn, ing["id"])
    r = await admin_client.post(
        f"/api/v1/ingredients/{ing['id']}/link", json={"standard_key": "scallion"}
    )
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "merge_needed"
    assert error["details"]["other"]["id"] == other["id"]
    assert error["details"]["target_name"] == "scallion"


async def test_rename_keeps_the_old_name_and_can_ask_to_merge(admin_client, db_session, owner_conn):
    ing = await make_ingredient(admin_client, "guanciale slab")
    await make_ingredient(admin_client, "Pancetta")
    await _unreviewed(owner_conn, ing["id"])
    r = await admin_client.post(
        f"/api/v1/ingredients/{ing['id']}/rename", json={"name": "guanciale"}
    )
    assert r.status_code == 200 and r.json()["name"] == "guanciale"
    row = await _get(db_session, ing["id"])
    assert row.reconcile_state == "not_applicable"
    assert (await _spellings(db_session, row.id))["guanciale slab"] == ("legacy", "rename")
    r = await admin_client.post(
        f"/api/v1/ingredients/{ing['id']}/rename", json={"name": "pancetta"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "merge_needed"


async def test_skip_and_reopen(admin_client, owner_conn):
    ing = await make_ingredient(admin_client, "Sorrel")
    await _unreviewed(owner_conn, ing["id"])
    await admin_client.post(f"/api/v1/ingredients/{ing['id']}/skip")
    page = (await admin_client.get("/api/v1/ingredients/link")).json()
    assert page["to_review"] == [] and [r["name"] for r in page["skipped"]] == ["Sorrel"]
    assert (await admin_client.get("/api/v1/ingredients/link/summary")).json() == {
        "to_review": 0,
        "skipped": 1,
    }
    await admin_client.post(f"/api/v1/ingredients/{ing['id']}/reopen")
    page = (await admin_client.get("/api/v1/ingredients/link")).json()
    assert [r["name"] for r in page["to_review"]] == ["Sorrel"]


async def test_the_inbox_row_appears_and_leaves(admin_client, owner_conn):
    ing = await make_ingredient(admin_client, "Sorrel")
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in inbox if i["kind"] == "link"]
    await _unreviewed(owner_conn, ing["id"])
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    [row] = [i for i in inbox if i["kind"] == "link"]
    assert row["title"] == "1 ingredient to link to the standard list"
    assert row["action_route"] == "/catalog/ingredients/link"
    await admin_client.post(f"/api/v1/ingredients/{ing['id']}/skip")
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in inbox if i["kind"] == "link"]


async def _duplicates(admin_client, owner_conn, *, survivor_first: bool):
    """Two ingredients for one onion, each with a product and a price, in different units."""
    loc = await make_location(admin_client, "Hilltop Stand", "Hilltop Stand")
    scallion_product = await make_product(
        admin_client, "Scallion", "Scallion bag", canonical_unit="g", pack_qty="200", pack_unit="g"
    )
    onion_product = await make_product(
        admin_client, "green onions", "Green onion bunch", canonical_unit="each"
    )
    await shelf(admin_client, scallion_product["id"], loc["id"], "2.00")
    by_bunch = await shelf(admin_client, onion_product["id"], loc["id"], "1.50")
    assert by_bunch["norm"]["status"] == "ok"
    scallion = scallion_product["ingredient"]
    onions = onion_product["ingredient"]
    for ing, label in ((onions, "bunch"), (onions, "stalk"), (scallion, "stalk")):
        r = await admin_client.post(
            f"/api/v1/ingredients/{ing['id']}/measures",
            json={"label": label, "canonical_qty": "1", "source": "manual"},
        )
        assert r.status_code == 201, r.text
    await owner_conn.execute(
        "INSERT INTO ingredient_ref (id, ingredient_id, system, external_id, is_preferred) "
        "VALUES ($1, $2, 'fdc', '170005', true)",
        new_id(),
        uuid.UUID(onions["id"]),
    )
    await _unreviewed(owner_conn, onions["id"])
    survivor, loser = (scallion, onions) if survivor_first else (onions, scallion)
    assert (uuid.UUID(scallion["id"]) < uuid.UUID(onions["id"])) is True
    return survivor, loser, onion_product, by_bunch


@pytest.mark.parametrize("survivor_first", [True, False])
async def test_merge_moves_everything_in_one_commit(
    admin_client, db_session, owner_conn, survivor_first
):
    survivor, loser, onion_product, by_bunch = await _duplicates(
        admin_client, owner_conn, survivor_first=survivor_first
    )
    body = {"survivor_id": survivor["id"], "loser_id": loser["id"], "standard_key": "scallion"}

    preview = await admin_client.post("/api/v1/ingredients/merge/preview", json=body)
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["target_name"] == "scallion"
    assert p["unit_from"] == loser["canonical_unit"] and p["unit_to"] == survivor["canonical_unit"]
    # The price by the bunch can't be put into grams without a bridge.
    assert p["prices_needing_bridge"] == 1
    # Units differ, so no measure can be copied.
    loser_labels = ["bunch", "stalk"] if survivor_first else ["stalk"]
    assert {m["label"]: m["copyable"] for m in p["measures"]} == dict.fromkeys(loser_labels, False)
    # The preview wrote nothing.
    assert (await _get(db_session, loser["id"])).active is True

    r = await admin_client.post("/api/v1/ingredients/merge", json=body | {"copy_measures": []})
    assert r.status_code == 200, r.text
    kept = await _get(db_session, survivor["id"])
    assert (kept.name, kept.slug, kept.reconcile_state) == ("scallion", "scallion", "linked")
    kept_id = kept.id
    gone = await _get(db_session, loser["id"])
    assert gone.active is False and gone.merged_into == kept_id
    assert gone.name == f"{loser['name']} (merged into scallion)"
    moved = (await admin_client.get(f"/api/v1/products/{onion_product['id']}")).json()
    assert moved["ingredient"]["id"] == survivor["id"]
    spellings = await _spellings(db_session, kept_id)
    for old in (loser["name"], survivor["name"]):
        if normalize_name(old) != "scallion":
            assert spellings[normalize_name(old)][0] == "legacy"
    refs = (
        (
            await db_session.execute(
                select(IngredientRef).where(IngredientRef.ingredient_id == kept_id)
            )
        )
        .scalars()
        .all()
    )
    assert [(x.external_id, x.is_preferred) for x in refs] == [("170005", True)]
    # Renormalized in the same commit: the bunch price now needs a bridge.
    observation = (await admin_client.get(f"/api/v1/price-observations/{by_bunch['id']}")).json()
    # The bunch price stays fine only when its own ingredient, counted in each, survives.
    assert (observation["norm"]["status"] == "ok") is (not survivor_first)
    listed = (await admin_client.get("/api/v1/price-book/needs-bridge")).json()["items"]
    # The one price in the other unit now waits for a bridge on the survivor.
    assert [i["ingredient"]["id"] for i in listed] == [survivor["id"]]
    assert (await admin_client.get("/api/v1/ingredients/link/summary")).json()["to_review"] == 0


async def test_merge_copies_only_ticked_measures_when_units_agree(
    admin_client, db_session, owner_conn
):
    a = await make_ingredient(admin_client, "Leek")
    b = await make_ingredient(admin_client, "Leeks, baby")
    for ing, label in ((b, "bunch"), (b, "stalk")):
        await admin_client.post(
            f"/api/v1/ingredients/{ing['id']}/measures",
            json={"label": label, "canonical_qty": "80", "source": "measured"},
        )
    body = {"survivor_id": a["id"], "loser_id": b["id"], "name": "leek"}
    p = (await admin_client.post("/api/v1/ingredients/merge/preview", json=body)).json()
    assert {m["label"]: (m["copyable"], m["suggested"]) for m in p["measures"]} == {
        "bunch": (True, True),
        "stalk": (True, True),
    }
    assert p["prices_needing_bridge"] == 0
    r = await admin_client.post(
        "/api/v1/ingredients/merge", json=body | {"copy_measures": ["bunch"]}
    )
    assert r.status_code == 200, r.text
    labels = (
        await db_session.execute(
            select(IngredientMeasure.label, IngredientMeasure.source).where(
                IngredientMeasure.ingredient_id == uuid.UUID(a["id"])
            )
        )
    ).all()
    assert labels == [("bunch", "measured")]
    kept = await _get(db_session, a["id"])
    assert kept.name == "leek" and kept.reconcile_state == "not_applicable"


async def test_merge_refuses_itself_and_a_missing_target(admin_client):
    a = await make_ingredient(admin_client, "Leek")
    b = await make_ingredient(admin_client, "Ramp")
    r = await admin_client.post(
        "/api/v1/ingredients/merge", json={"survivor_id": a["id"], "loser_id": a["id"], "name": "x"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "merge_self"
    r = await admin_client.post(
        "/api/v1/ingredients/merge",
        json={"survivor_id": a["id"], "loser_id": b["id"], "standard_key": "no-such"},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_standard_entry"
    r = await admin_client.post(
        "/api/v1/ingredients/merge", json={"survivor_id": a["id"], "loser_id": b["id"]}
    )
    assert r.status_code == 422


async def test_standard_entries_filter_by_name_or_spelling(admin_client):
    r = await admin_client.get("/api/v1/standard-ingredients", params={"q": "green onion"})
    assert [e["key"] for e in r.json()["items"]] == ["scallion"]
    r = await admin_client.get("/api/v1/standard-ingredients", params={"q": "sesame"})
    keys = [e["key"] for e in r.json()["items"]]
    assert keys[:2] == ["sesame-oil", "toasted-sesame-oil"] or set(keys) >= {
        "sesame-oil",
        "toasted-sesame-oil",
    }


async def test_a_former_name_another_ingredient_has_is_logged_not_kept(
    admin_client, db_session, owner_conn, caplog
):

    holder = await make_ingredient(admin_client, "Allium greens")
    ing = await make_ingredient(admin_client, "green onion")
    # The old name is already another ingredient's spelling, so it can't follow.
    await owner_conn.execute(
        "INSERT INTO ingredient_alias (id, name_norm, ingredient_id, kind, source) "
        "VALUES ($1, 'green onion', $2, 'synonym', 'manual')",
        new_id(),
        uuid.UUID(holder["id"]),
    )
    with caplog.at_level("INFO", logger="app.services.ingredient_reconcile"):
        r = await admin_client.post(
            f"/api/v1/ingredients/{ing['id']}/rename", json={"name": "scallion stalk"}
        )
    assert r.status_code == 200, r.text
    assert "skipped a former name another ingredient has" in caplog.text
    assert "green onion" not in await _spellings(db_session, uuid.UUID(ing["id"]))


# Found by /devex-review on 2026-10-06: typed-in ingredients named the USDA way
# ("butter, unsalted") never matched their standard name, and nothing offered the
# standard list to ingredients typed in after 1G.
@pytest.mark.parametrize(
    ("name", "key"), [("butter, unsalted", "unsalted-butter"), ("Salmon, smoked", "smoked-salmon")]
)
def test_a_noun_first_name_finds_its_standard_entry(name, key):
    from app.services.ingredient_reconcile import suggest_entry

    entry = suggest_entry(name)
    assert entry is not None and entry.key == key


async def test_recheck_offers_typed_in_ingredients_the_list_now_matches(
    admin_client, db_session, owner_conn
):
    from app.services.ingredient_reconcile import recheck

    butter = await make_ingredient(admin_client, "butter, unsalted")
    quince = await make_ingredient(admin_client, "Quince paste")
    await _not_applicable(owner_conn, butter["id"])  # typed in before #189
    assert (await _get(db_session, butter["id"])).reconcile_state == "not_applicable"
    assert await recheck(db_session) == ["butter, unsalted"]
    assert (await _get(db_session, butter["id"])).reconcile_state == "unreviewed"
    assert (await _get(db_session, quince["id"])).reconcile_state == "not_applicable"
    page = (await admin_client.get("/api/v1/ingredients/link")).json()
    [row] = page["to_review"]
    assert row["name"] == "butter, unsalted" and row["suggestion"]["key"] == "unsalted-butter"


# #189: an ingredient typed in after 1G was never offered its standard name.
async def test_a_typed_in_ingredient_the_list_knows_is_offered_at_once(admin_client, db_session):
    onions = await make_ingredient(admin_client, "Onions, red")
    quince = await make_ingredient(admin_client, "Quince paste")
    r = await admin_client.post(
        "/api/v1/products",
        json={"ingredient": {"name": "large eggs", "canonical_unit": "each"}, "name": "Hen eggs"},
    )
    assert r.status_code == 201, r.text
    eggs = r.json()["ingredient"]
    assert (await _get(db_session, onions["id"])).reconcile_state == "unreviewed"
    assert (await _get(db_session, eggs["id"])).reconcile_state == "unreviewed"
    assert (await _get(db_session, quince["id"])).reconcile_state == "not_applicable"
    page = (await admin_client.get("/api/v1/ingredients/link")).json()
    offered = {row["name"]: row["suggestion"]["key"] for row in page["to_review"]}
    assert offered == {"Onions, red": "red-onion", "large eggs": "egg"}
    # Offered, never linked: the names are still the ones typed.
    assert (await _get(db_session, onions["id"])).slug.startswith("local.")
