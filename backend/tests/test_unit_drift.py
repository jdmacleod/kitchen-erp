"""Unit drift (#527): an ingredient linked to a standard entry in another unit is
offered the entry's unit in Tidy the catalog, on the link page and on Needs a
bridge. Stores, products and recipes are invented; the geography is synthetic.

whiskey is measured in ml on the standard list; the tests store it in g, as the
cocktail review found linked ingredients. A 750 ml bottle at $30.00 is $0.04 a ml.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest

from app.core.db import get_sessionmaker
from app.core.ids import new_id
from app.services import catalog, spellings
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.pricebook_helpers import shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_catalog_cleanup import by_kind, suggestions
from tests.test_products_helper import helper  # noqa: F401  a products-only token client
from tests.test_recipe_costing import cost, indexed, lines_by_name, market

DRAM = "---\ntitle: Harbor dram\n---\n\nPour @whiskey{30%g}.\n"


async def drifted(client, db_session, owner_conn, key: str = "whiskey", unit: str = "g") -> dict:
    """The entry's ingredient, then stored in another unit, as a link left it."""
    await seed_units_via_service(db_session)
    from app.catalog import standard

    ingredient = await make_ingredient(client, standard.by_key()[key].name, standard_key=key)
    await owner_conn.execute(
        "UPDATE ingredient SET canonical_unit = $1 WHERE id = $2", unit, uuid.UUID(ingredient["id"])
    )
    return ingredient


async def measure(owner_conn, ingredient: dict, label: str, qty: str, source: str = "manual"):
    await owner_conn.execute(
        "INSERT INTO ingredient_measure"
        " (id, ingredient_id, label, canonical_qty, source, confirmed)"
        " VALUES ($1, $2, $3, $4::numeric, $5, false)",
        new_id(), uuid.UUID(ingredient["id"]), label, qty, source,
    )  # fmt: skip


async def switches(client) -> list[dict]:
    return by_kind(await suggestions(client), "unit_mismatch")


async def accept(client, s: dict, fingerprint: str | None = None) -> httpx.Response:
    return await client.post(
        "/api/v1/catalog/cleanup/apply",
        json={"keys": [s["key"]], "fingerprints": {s["key"]: fingerprint or s["fingerprint"]}},
    )


async def test_a_g_whiskey_is_offered_ml_and_the_switch_renormalizes_its_prices(
    admin_client, db_session, owner_conn: asyncpg.Connection
):
    loc = await market(admin_client)
    whiskey = await drifted(admin_client, db_session, owner_conn)
    bottle = await make_product(
        admin_client, whiskey["id"], "Harbor Distillers Whiskey", pack_qty="750", pack_unit="ml"
    )
    await shelf(admin_client, bottle["id"], loc["id"], "30.00")
    [s] = await switches(admin_client)
    assert (s["subject_id"], s["before"], s["after"]) == (whiskey["id"], "g", "ml")
    assert (
        s["reason"] == "the standard list measures whiskey in ml; 1 price fails to normalize in g"
    )
    assert s["confidence"] == "sure" and s["consequences"] == []

    r = await accept(admin_client, s)
    assert r.status_code == 200, r.text
    [result] = r.json()["results"]
    assert (result["prices_renormalized"], result["measures_dropped"]) == (1, 0)
    after = (await admin_client.get(f"/api/v1/ingredients/{whiskey['id']}")).json()
    assert after["canonical_unit"] == "ml"
    norm = await owner_conn.fetchrow(
        "SELECT n.status, n.norm_unit, n.norm_unit_price FROM price_norm n"
        " JOIN price_observation o ON o.id = n.observation_id WHERE o.product_id = $1",
        uuid.UUID(bottle["id"]),
    )
    assert (norm["status"], norm["norm_unit"], str(norm["norm_unit_price"])) == (
        "ok",
        "ml",
        "0.040000",
    )
    assert await switches(admin_client) == []


async def test_a_switch_adds_the_entrys_measures(admin_client, db_session, owner_conn):
    peel = await drifted(admin_client, db_session, owner_conn, key="orange-peel", unit="each")
    [s] = await switches(admin_client)
    assert {"text": "1 twist = 3 g is added from the standard list", "recipe_id": None} in s[
        "consequences"
    ]
    assert (await accept(admin_client, s)).status_code == 200
    after = (await admin_client.get(f"/api/v1/ingredients/{peel['id']}")).json()
    assert after["canonical_unit"] == "g"
    assert [(m["label"], m["canonical_qty"]) for m in after["measures"]] == [("twist", "3")]


async def test_a_measure_that_converts_is_rewritten_and_one_that_cant_is_dropped_and_listed(
    admin_client, db_session, owner_conn
):
    whiskey = await drifted(admin_client, db_session, owner_conn)
    await measure(owner_conn, whiskey, "shot", "42")
    for source in ("manual", "measured", "label", "usda"):
        await measure(owner_conn, whiskey, f"bottle by {source}", "700", source)
    # Without a density nothing in g converts to ml: every measure drops.
    [s] = await switches(admin_client)
    assert s["confidence"] == "guess"
    texts = [c["text"] for c in s["consequences"]]
    assert "1 bottle by manual = 700 g is dropped (entered by hand)" in texts
    assert "1 bottle by measured = 700 g is dropped (entered by hand)" in texts
    assert "1 bottle by label = 700 g is dropped (entered by hand)" in texts
    assert "1 bottle by usda = 700 g is dropped" in texts
    assert s["reason"].endswith("; 5 measures drop")

    # With a density, they convert: kept, rewritten in ml, and the switch is sure.
    r = await admin_client.patch(
        f"/api/v1/ingredients/{whiskey['id']}",
        json={"density_g_per_ml": "0.95", "density_source": "manual"},
    )
    assert r.status_code == 200, r.text
    [s] = await switches(admin_client)
    assert s["confidence"] == "sure"
    assert "1 shot = 42 g becomes 44.2105 ml" in [c["text"] for c in s["consequences"]]
    r = await accept(admin_client, s)
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["measures_rewritten"] == 5
    after = (await admin_client.get(f"/api/v1/ingredients/{whiskey['id']}")).json()
    assert {m["label"]: m["canonical_qty"] for m in after["measures"]}["shot"] == "44.2105"


async def test_a_switch_that_unprices_or_unconverts_a_line_is_a_guess_naming_it(
    admin_client,
    db_session,
    owner_conn,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    whiskey = await drifted(admin_client, db_session, owner_conn)
    # Sold by weight: its price normalizes in g now, and wouldn't in ml without a density.
    jug = await make_product(
        admin_client, whiskey["id"], "Cove whiskey jug", pack_qty="700", pack_unit="g"
    )
    await shelf(admin_client, jug["id"], loc["id"], "28.00")
    dram = await indexed(admin_client, recipes_repo, "harbor-dram.cook", DRAM)
    assert lines_by_name(await cost(admin_client, dram["id"]))["whiskey"]["status"] == "priced"
    [s] = await switches(admin_client)
    assert s["confidence"] == "guess"
    assert s["consequences"] == [
        {"text": "Harbor dram: whiskey can no longer convert", "recipe_id": dram["id"]},
        {"text": "Harbor dram: whiskey loses its price", "recipe_id": dram["id"]},
    ]
    assert s["reason"] == (
        "the standard list measures whiskey in ml; 1 recipe line can no longer convert; "
        "1 recipe line loses its price"
    )
    # A guess named with others is held, never applied.
    r = await admin_client.post(
        "/api/v1/catalog/cleanup/apply", json={"keys": [s["key"], "product_name:x:y"]}
    )
    assert r.status_code == 200 and r.json()["held"] == [s["key"]]


async def test_a_matching_unit_is_not_listed(admin_client, db_session, owner_conn):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "whiskey", standard_key="whiskey")
    assert await switches(admin_client) == []


async def test_a_measure_edited_since_the_list_is_cleanup_changed(
    admin_client, db_session, owner_conn
):
    whiskey = await drifted(admin_client, db_session, owner_conn)
    await measure(owner_conn, whiskey, "shot", "42")
    [s] = await switches(admin_client)
    await owner_conn.execute(
        "UPDATE ingredient_measure SET canonical_qty = 44 WHERE ingredient_id = $1",
        uuid.UUID(whiskey["id"]),
    )
    r = await accept(admin_client, s)
    assert r.status_code == 409 and r.json()["error"]["code"] == "cleanup_changed"
    fresh = r.json()["error"]["details"]["suggestion"]
    assert "1 shot = 44 g is dropped (entered by hand)" in [
        c["text"] for c in fresh["consequences"]
    ]
    after = (await admin_client.get(f"/api/v1/ingredients/{whiskey['id']}")).json()
    assert after["canonical_unit"] == "g"


async def test_a_failed_switch_changes_nothing(
    admin_client, db_session, owner_conn, monkeypatch: pytest.MonkeyPatch,
    recipes_repo: TempRepo,  # noqa: F811
):  # fmt: skip
    loc = await market(admin_client)
    whiskey = await drifted(admin_client, db_session, owner_conn)
    await measure(owner_conn, whiskey, "shot", "42")
    bottle = await make_product(
        admin_client, whiskey["id"], "Harbor Distillers Whiskey", pack_qty="750", pack_unit="ml"
    )
    await shelf(admin_client, bottle["id"], loc["id"], "30.00")
    dram = await indexed(admin_client, recipes_repo, "harbor-dram.cook", DRAM)
    before = await cost(admin_client, dram["id"])
    [s] = await switches(admin_client)

    async def breaks(*_args, **_kwargs):
        raise RuntimeError("the standard list broke")

    monkeypatch.setattr(catalog, "apply_standard_entry", breaks)
    r = await accept(admin_client, s)
    assert r.status_code == 500 and r.json()["error"]["code"] == "unit_switch_failed"
    assert r.json()["error"]["message"] == "The switch failed, and nothing changed."
    after = (await admin_client.get(f"/api/v1/ingredients/{whiskey['id']}")).json()
    assert after["canonical_unit"] == "g"
    assert [(m["label"], m["canonical_qty"]) for m in after["measures"]] == [("shot", "42")]
    status = await owner_conn.fetchval(
        "SELECT n.status FROM price_norm n JOIN price_observation o ON o.id = n.observation_id"
        " WHERE o.product_id = $1",
        uuid.UUID(bottle["id"]),
    )
    assert status == "no_density"
    assert await cost(admin_client, dram["id"]) == before
    assert [x["key"] for x in await switches(admin_client)] == [s["key"]]


async def test_the_endpoint_switches_even_after_a_skip_and_needs_a_session(
    admin_client,
    db_session,
    owner_conn,
    helper,  # noqa: F811
):
    whiskey = await drifted(admin_client, db_session, owner_conn)
    [s] = await switches(admin_client)
    r = await admin_client.post("/api/v1/catalog/cleanup/skip", json={"keys": [s["key"]]})
    assert r.status_code == 204
    shown = (await admin_client.get(f"/api/v1/ingredients/{whiskey['id']}/unit-switch")).json()
    assert shown["suggestion"]["key"] == s["key"]

    url = f"/api/v1/ingredients/{whiskey['id']}/unit-switch"
    r = await helper.post(url, json={}, headers=helper.suggest_headers)
    assert r.status_code == 403, r.text
    r = await admin_client.post(url, json={"fingerprint": shown["suggestion"]["fingerprint"]})
    assert r.status_code == 200, r.text
    assert r.json()["key"] == s["key"]
    assert (await admin_client.get(url)).json() == {"suggestion": None}
    r = await admin_client.post(url, json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "cleanup_gone"


async def test_needs_a_bridge_defers_to_the_standard_switch(admin_client, db_session, owner_conn):
    loc = await market(admin_client)
    whiskey = await drifted(admin_client, db_session, owner_conn)
    linked = await make_product(
        admin_client, whiskey["id"], "Harbor Distillers Whiskey", pack_qty="750", pack_unit="ml"
    )
    syrup = await make_ingredient(admin_client, "lantern syrup")
    unlinked = await make_product(
        admin_client, syrup["id"], "Lantern syrup bottle", pack_qty="500", pack_unit="ml"
    )
    await shelf(admin_client, linked["id"], loc["id"], "30.00")
    await shelf(admin_client, unlinked["id"], loc["id"], "6.00")
    [s] = await switches(admin_client)
    items = (await admin_client.get("/api/v1/price-book/needs-bridge")).json()["items"]
    rows = {i["ingredient"]["name"]: i for i in items}
    assert rows["whiskey"]["switch_to_ml"] is False
    assert rows["whiskey"]["standard_switch"] == {"unit": "ml", "key": s["key"], "skipped": False}
    assert rows["lantern syrup"]["switch_to_ml"] is True
    assert rows["lantern syrup"]["standard_switch"] is None


async def test_kerp_ingredients_check_lists_unit_drift(admin_client, db_session, owner_conn):
    whiskey = await drifted(admin_client, db_session, owner_conn)
    await measure(owner_conn, whiskey, "shot", "42")
    async with get_sessionmaker()() as db:
        lines = spellings.report_lines(await spellings.vocabulary_report(db))
    i = lines.index("Unit differs from the standard list: 1")
    assert (
        lines[i + 1] == "  whiskey: g -> ml (0 prices fail; 1 measures drop; 0 lines unconvertible)"
    )
