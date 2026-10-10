"""Recipe costing (07, 3D; package 6a): criteria 19 to 23, 26, 27 and 27a.

Every figure is hand-computed below from invented prices. Vendors, products and
recipes are invented; the geography is synthetic.

Unit prices: a 1 kg bag at $4.00 is $0.004000 per g; a 1 L carton at $3.00 is
$0.003000 per ml; a box of 20 dates at $5.00 is $0.250000 each. Costs are
quantized to four places, ROUND_HALF_EVEN.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import httpx
import pytest

from app.core.db import dispose_engine, get_sessionmaker
from app.services import recipe_costing
from tests.catalog_helpers import make_product as make_sibling_product
from tests.conftest import BACKEND_ROOT, run_alembic
from tests.pricebook_helpers import make_location, make_product, shelf
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_posted_prices import post_listing_price

D = Decimal

PORRIDGE = """---
title: Harbor porridge
servings: 4
---

Simmer @barley{200%g} in @oat milk{500%ml} and top with @dates{4}.
"""

SLAW = """---
title: Harbor slaw
---

Grate @carrot{1%cup} and slice @carrot{1} with @parsnip{100%g}.
"""

BROTH = """---
title: Harbor broth
---

Simmer @barley{200-300%g} with @thyme{a few sprigs}.
"""

GAPS = """---
title: Harbor gaps
---

Combine @barley{200%g}, @leek{1%cup}, @fennel{100%g} and @dragon root{2}.
"""

GLAZE = """---
title: Harbor glaze
---

Warm @honey{2%tbsp} over @barley{200%g}.
"""

BOWL = """---
title: Harbor bowl
---

Serve @barley{200%g}.
"""


# --- helpers ------------------------------------------------------------------------------


async def rescan(client: httpx.AsyncClient) -> dict:
    r = await client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200, r.text
    return r.json()


async def recipe_by_path(client: httpx.AsyncClient, path: str) -> dict:
    return {i["path"]: i for i in (await client.get("/api/v1/recipes")).json()["items"]}[path]


async def indexed(client: httpx.AsyncClient, repo: TempRepo, path: str, text: str) -> dict:
    """Write, commit and index one recipe; the row as the list shows it."""
    repo.write(path, text)
    repo.commit()
    await rescan(client)
    return await recipe_by_path(client, path)


async def cost(client: httpx.AsyncClient, recipe_id: str, **params) -> dict:
    r = await client.get(f"/api/v1/recipes/{recipe_id}/cost", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def recost(recipe_id: str) -> None:
    """What package 6b's triggers will do: recompute every snapshot the recipe has."""
    async with get_sessionmaker()() as db:
        await recipe_costing.recompute_recipe(db, uuid.UUID(recipe_id))


def lines_by_name(body: dict) -> dict[str, dict]:
    return {line["line"]["name_norm"]: line for line in body["lines"]}


def lines_by_text(body: dict) -> dict[tuple[str, str | None], dict]:
    return {(line["line"]["name_norm"], line["line"]["unit_text"]): line for line in body["lines"]}


async def market(client: httpx.AsyncClient) -> dict:
    return await make_location(client, "Harbor Market", "Harbor Market")


async def porridge_pantry(client: httpx.AsyncClient) -> dict:
    """Barley, oat milk and dates with one shelf price each at the market."""
    loc = await market(client)
    barley = await make_product(client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    milk = await make_product(
        client, "Oat milk", "Oat milk carton", canonical_unit="ml", pack_qty="1", pack_unit="l"
    )
    dates = await make_product(
        client, "Dates", "Dates box", canonical_unit="each", pack_qty="20", pack_unit="each"
    )
    await shelf(client, barley["id"], loc["id"], "4.00")
    await shelf(client, milk["id"], loc["id"], "3.00")
    await shelf(client, dates["id"], loc["id"], "5.00")
    return {"loc": loc, "barley": barley, "milk": milk, "dates": dates}


async def barley_only(client: httpx.AsyncClient) -> dict:
    loc = await market(client)
    barley = await make_product(client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    await shelf(client, barley["id"], loc["id"], "4.00")
    return {"loc": loc, "barley": barley}


def days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat()


# --- 19: every line priced, three bases, exact figures ------------------------------------


async def test_19_all_lines_priced_exact_under_each_basis(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    pantry = await porridge_pantry(admin_client)
    # An older, cheaper barley price: latest and cheapest then differ, and the
    # 90-day average is their mean.
    await shelf(
        admin_client, pantry["barley"]["id"], pantry["loc"]["id"], "3.00", observed_at=days_ago(10)
    )
    recipe = await indexed(admin_client, recipes_repo, "harbor-porridge.cook", PORRIDGE)

    latest = await cost(admin_client, recipe["id"])
    assert latest["basis"] == "latest" and latest["window_days"] is None
    assert latest["provisional"] is False and latest["head_commit"] == recipes_repo.head()
    assert latest["content_hash"] == recipe["content_hash"]
    got = lines_by_name(latest)
    # 200 g × $0.004 = $0.80; one 1 kg pack at $4.00.
    assert got["barley"]["status"] == "priced"
    assert got["barley"]["consumed_cost"] == "0.8000" and got["barley"]["basket_cost"] == "4.0000"
    assert got["barley"]["packs"] == "1" and got["barley"]["price"]["norm_unit_price"] == "0.004000"
    # 500 ml × $0.003 = $1.50; one 1 L carton at $3.00.
    assert got["oat milk"]["consumed_cost"] == "1.5000"
    assert got["oat milk"]["basket_cost"] == "3.0000"
    # 4 dates × $0.25 = $1.00; one box of 20 at $5.00. Four each is four pieces, not four boxes.
    assert got["dates"]["quantity"]["canonical_qty"] == "4"
    assert got["dates"]["consumed_cost"] == "1.0000" and got["dates"]["basket_cost"] == "5.0000"
    assert latest["totals"] == {
        "consumed_cost": "3.3000",
        "consumed_cost_high": "3.3000",
        "basket_cost": "12.0000",
        "basket_cost_high": "12.0000",
        "per_serving": "0.8250",
    }
    assert latest["completeness"] == {
        "lines_total": 3,
        "lines_priced": 3,
        "lines_unpriced": 0,
        "lines_unconvertible": 0,
        "lines_unmapped": 0,
        "lines_negligible": 0,
    }
    assert latest["unconfirmed_share"] == "0.0000"

    cheapest = await cost(admin_client, recipe["id"], basis="cheapest")
    got = lines_by_name(cheapest)
    assert got["barley"]["price"]["norm_unit_price"] == "0.003000"
    assert got["barley"]["consumed_cost"] == "0.6000" and got["barley"]["basket_cost"] == "3.0000"
    assert cheapest["totals"]["consumed_cost"] == "3.1000"
    assert cheapest["totals"]["basket_cost"] == "11.0000"
    assert cheapest["totals"]["per_serving"] == "0.7750"

    average = await cost(admin_client, recipe["id"], basis="average")
    assert average["window_days"] == 90, "the default window is stale_after_days"
    got = lines_by_name(average)
    assert got["barley"]["price"]["norm_unit_price"] == "0.003500"
    assert got["barley"]["consumed_cost"] == "0.7000" and got["barley"]["basket_cost"] == "3.5000"
    assert average["totals"]["consumed_cost"] == "3.2000"
    assert average["totals"]["basket_cost"] == "11.5000"
    assert average["totals"]["per_serving"] == "0.8000"

    # A shorter window that holds only the latest price.
    narrow = await cost(admin_client, recipe["id"], basis="average", window_days=5)
    assert narrow["window_days"] == 5
    assert lines_by_name(narrow)["barley"]["price"]["norm_unit_price"] == "0.004000"


async def test_lines_show_prices_and_quantities_in_display_units(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    pantry = await porridge_pantry(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "harbor-porridge.cook", PORRIDGE)
    got = lines_by_name(await cost(admin_client, recipe["id"]))
    barley = got["barley"]
    # $0.004/g × 453.59237 g/lb = $1.81/lb; 200 g = 0.441 lb (three places below 1).
    assert barley["price"]["display_unit_price"] == "1.81"
    assert barley["price"]["display_unit"] == "lb"
    assert barley["quantity"]["display_qty"] == "0.441"
    assert barley["quantity"]["display_unit"] == "lb"
    assert barley["price"]["product_name"] == "Barley bag"
    assert barley["price"]["vendor_name"] == "Harbor Market"
    assert barley["price"]["location_id"] == pantry["loc"]["id"]
    assert barley["price"]["stale"] is False and barley["price"]["observed_at"]
    milk = got["oat milk"]
    assert milk["price"]["display_unit"] == "fl oz"
    assert milk["price"]["display_unit_price"] == "0.089"
    assert milk["quantity"]["display_qty"] == "16.91"
    dates = got["dates"]
    assert dates["price"]["display_unit"] == "each"
    assert dates["price"]["display_unit_price"] == "0.250"
    assert dates["quantity"]["display_qty"] == "4.00"
    assert got["barley"]["line"]["raw_name"] == "barley"
    assert got["barley"]["line"]["ingredient_name"] == "Barley"
    assert got["barley"]["pinned"] is False and got["barley"]["yield_mode"] == "auto"


# --- 20: yield by heuristic and by override --------------------------------------------------


async def test_20_yield_grosses_up_volume_not_count_and_overrides_flip_it(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    carrot = await make_product(admin_client, "Carrot", "Carrot bag", pack_qty="1", pack_unit="kg")
    parsnip = await make_product(
        admin_client, "Parsnip", "Parsnip bag", pack_qty="500", pack_unit="g"
    )
    # Priced by weight: an "each" measure on the ingredient would otherwise read a
    # "1 each" shelf price as one carrot (the price book's own rule).
    await shelf(admin_client, carrot["id"], loc["id"], "2.00", qty="1", unit="kg")  # $0.002/g
    await shelf(admin_client, parsnip["id"], loc["id"], "1.00")  # $0.002/g
    cid = carrot["ingredient"]["id"]
    r = await admin_client.patch(
        f"/api/v1/ingredients/{cid}",
        json={"density_g_per_ml": "0.5", "density_source": "manual", "yield_pct": "0.8"},
    )
    assert r.status_code == 200, r.text
    confirmed = await admin_client.post(f"/api/v1/ingredients/{cid}/density/confirm")
    assert confirmed.status_code == 200, confirmed.text
    r = await admin_client.post(
        f"/api/v1/ingredients/{cid}/measures",
        json={"label": "each", "canonical_qty": "60", "source": "manual", "confirmed": True},
    )
    assert r.status_code == 201, r.text
    recipe = await indexed(admin_client, recipes_repo, "harbor-slaw.cook", SLAW)

    got = lines_by_text(await cost(admin_client, recipe["id"]))
    cup, each, by_mass = got[("carrot", "cup")], got[("carrot", None)], got[("parsnip", "g")]
    # 1 cup = 236.5882365 ml × 0.5 g/ml = 118.29411825 g edible; ÷ 0.8 = 147.8676478125 g
    # as purchased; × $0.002 = $0.2957.
    assert cup["quantity"]["canonical_qty"] == "147.8676478125"
    assert cup["yield_applied"] == "0.8000" and cup["yield_assumed"] is False
    assert cup["consumed_cost"] == "0.2957" and cup["basket_cost"] == "2.0000"
    assert cup["bridge_kind"] == "density" and cup["bridge_confirmed"] is True
    # One carrot is the "each" measure, 60 g, as purchased: no yield.
    assert each["quantity"]["canonical_qty"] == "60" and each["yield_applied"] is None
    assert each["consumed_cost"] == "0.1200"
    assert each["bridge_kind"] == "measure"
    # No yield on parsnip yet: grossed up at 100%, and the line says so.
    assert by_mass["yield_applied"] == "1.0000" and by_mass["yield_assumed"] is True
    assert by_mass["consumed_cost"] == "0.2000" and by_mass["basket_cost"] == "1.0000"

    # Per-line overrides flip either way (the 3E screen sets these; here by row).
    await owner_conn.execute(
        "UPDATE recipe_ingredient SET yield_mode = 'as_purchased' WHERE id = $1",
        uuid.UUID(cup["line"]["id"]),
    )
    await owner_conn.execute(
        "UPDATE recipe_ingredient SET yield_mode = 'edible' WHERE id = $1",
        uuid.UUID(each["line"]["id"]),
    )
    await recost(recipe["id"])
    got = lines_by_text(await cost(admin_client, recipe["id"]))
    cup, each = got[("carrot", "cup")], got[("carrot", None)]
    assert cup["yield_mode"] == "as_purchased" and cup["yield_applied"] is None
    assert cup["consumed_cost"] == "0.2366"  # 118.29411825 g × $0.002
    assert each["yield_mode"] == "edible" and each["yield_applied"] == "0.8000"
    assert each["consumed_cost"] == "0.1500"  # 60 g ÷ 0.8 = 75 g × $0.002


# --- 21: ranges and text quantities ----------------------------------------------------------


async def test_21_range_gives_low_and_high_and_text_quantity_is_negligible(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await barley_only(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "harbor-broth.cook", BROTH)
    body = await cost(admin_client, recipe["id"])
    got = lines_by_name(body)
    barley = got["barley"]
    assert barley["quantity"]["canonical_qty"] == "200"
    assert barley["quantity"]["canonical_qty_high"] == "300"
    assert barley["consumed_cost"] == "0.8000" and barley["consumed_cost_high"] == "1.2000"
    assert barley["basket_cost"] == "4.0000" and barley["basket_cost_high"] == "4.0000"
    assert barley["packs"] == "1" and barley["packs_high"] == "1"
    thyme = got["thyme"]
    assert thyme["status"] == "negligible" and thyme["line"]["qty_kind"] == "text"
    assert thyme["quantity"] is None and thyme["price"] is None and thyme["consumed_cost"] is None
    assert body["totals"] == {
        "consumed_cost": "0.8000",
        "consumed_cost_high": "1.2000",
        "basket_cost": "4.0000",
        "basket_cost_high": "4.0000",
        "per_serving": None,
    }
    c = body["completeness"]
    assert c["lines_total"] == 2 and c["lines_priced"] == 1 and c["lines_negligible"] == 1
    assert c["lines_priced"] + c["lines_negligible"] == c["lines_total"], "complete"


# --- 22: completeness buckets --------------------------------------------------------------


async def test_22_unpriced_unconvertible_and_unmapped_are_counted_and_left_out_of_totals(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    pantry = await barley_only(admin_client)
    leek = await make_product(admin_client, "Leek", "Leek bunch", pack_qty="1", pack_unit="kg")
    await shelf(admin_client, leek["id"], pantry["loc"]["id"], "3.00")  # priced, but no density
    await make_product(admin_client, "Fennel", "Fennel bulb", pack_qty="1", pack_unit="kg")
    recipe = await indexed(admin_client, recipes_repo, "harbor-gaps.cook", GAPS)
    body = await cost(admin_client, recipe["id"])
    got = lines_by_name(body)
    assert got["barley"]["status"] == "priced"
    leek_line = got["leek"]
    assert leek_line["status"] == "unconvertible" and leek_line["failure_code"] == "no_density"
    assert leek_line["quantity"] is None and leek_line["consumed_cost"] is None
    assert leek_line["price"] is None, "a price is not 'used' when the quantity cannot convert"
    fennel = got["fennel"]
    assert fennel["status"] == "unpriced" and fennel["quantity"]["canonical_qty"] == "100"
    assert fennel["price"] is None and fennel["consumed_cost"] is None
    root = got["dragon root"]
    assert root["status"] == "unmapped" and root["line"]["ingredient_id"] is None
    assert body["completeness"] == {
        "lines_total": 4,
        "lines_priced": 1,
        "lines_unpriced": 1,
        "lines_unconvertible": 1,
        "lines_unmapped": 1,
        "lines_negligible": 0,
    }
    assert body["totals"]["consumed_cost"] == "0.8000"
    assert body["totals"]["basket_cost"] == "4.0000"


# --- 23: unconfirmed share ------------------------------------------------------------------


async def test_23_unconfirmed_share_is_the_unconfirmed_lines_cost_over_the_total(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    pantry = await barley_only(admin_client)
    honey = await make_product(admin_client, "Honey", "Honey jar", pack_qty="500", pack_unit="g")
    await shelf(admin_client, honey["id"], pantry["loc"]["id"], "6.00")  # $0.012/g
    hid = honey["ingredient"]["id"]
    r = await admin_client.patch(
        f"/api/v1/ingredients/{hid}", json={"density_g_per_ml": "1.4", "density_source": "manual"}
    )
    assert r.status_code == 200 and r.json()["density_confirmed"] is False
    recipe = await indexed(admin_client, recipes_repo, "harbor-glaze.cook", GLAZE)
    body = await cost(admin_client, recipe["id"])
    got = lines_by_name(body)
    # 2 tbsp = 29.5735295625 ml × 1.4 = 41.4029413875 g × $0.012 = $0.4968, on an
    # unconfirmed density; barley is $0.80 on no bridge at all.
    assert got["honey"]["consumed_cost"] == "0.4968"
    assert got["honey"]["bridge_kind"] == "density" and got["honey"]["bridge_confirmed"] is False
    assert got["barley"]["bridge_kind"] == "none" and got["barley"]["bridge_confirmed"] is None
    assert body["totals"]["consumed_cost"] == "1.2968"
    assert body["unconfirmed_share"] == str((D("0.4968") / D("1.2968")).quantize(D("0.0001")))
    assert body["unconfirmed_share"] == "0.3831"

    confirmed = await admin_client.post(f"/api/v1/ingredients/{hid}/density/confirm")
    assert confirmed.status_code == 200, confirmed.text
    await recost(recipe["id"])
    body = await cost(admin_client, recipe["id"])
    assert lines_by_name(body)["honey"]["bridge_confirmed"] is True
    assert body["unconfirmed_share"] == "0.0000"


# --- 27: pins -----------------------------------------------------------------------------------


async def test_27_a_pinned_line_is_costed_only_from_the_pin(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    bag = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    iid = bag["ingredient"]["id"]
    sack = await make_sibling_product(
        admin_client, iid, "Barley sack", pack_qty="5", pack_unit="kg"
    )
    premium = await make_sibling_product(
        admin_client, iid, "Barley premium", pack_qty="1", pack_unit="kg"
    )
    await shelf(admin_client, bag["id"], loc["id"], "4.00", observed_at=days_ago(5))  # $0.004/g
    await shelf(admin_client, sack["id"], loc["id"], "15.00")  # $0.003/g, and the latest
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)

    unpinned = lines_by_name(await cost(admin_client, recipe["id"]))["barley"]
    assert unpinned["price"]["product_id"] == sack["id"]
    assert unpinned["consumed_cost"] == "0.6000" and unpinned["pinned"] is False

    r = await admin_client.put(
        f"/api/v1/recipes/{recipe['id']}/pins/barley", json={"product_id": bag["id"]}
    )
    assert r.status_code == 200, r.text
    await recost(recipe["id"])
    pinned = lines_by_name(await cost(admin_client, recipe["id"]))["barley"]
    assert pinned["pinned"] is True and pinned["price"]["product_id"] == bag["id"]
    assert pinned["consumed_cost"] == "0.8000" and pinned["basket_cost"] == "4.0000"
    # The cheapest basis is bound by the pin too.
    cheapest = lines_by_name(await cost(admin_client, recipe["id"], basis="cheapest"))["barley"]
    assert cheapest["price"]["product_id"] == bag["id"] and cheapest["consumed_cost"] == "0.8000"

    # Pinned to a product with no qualifying price: unpriced, though others have prices.
    r = await admin_client.put(
        f"/api/v1/recipes/{recipe['id']}/pins/barley", json={"product_id": premium["id"]}
    )
    assert r.status_code == 200, r.text
    await recost(recipe["id"])
    body = await cost(admin_client, recipe["id"])
    line = lines_by_name(body)["barley"]
    assert line["status"] == "unpriced" and line["price"] is None
    assert line["quantity"]["canonical_qty"] == "200", "the quantity still converts"
    assert body["completeness"]["lines_unpriced"] == 1
    assert body["totals"]["consumed_cost"] is None


# --- 27a: what never costs a line, and stale prices that do ---------------------------------


async def test_27a_posted_uncommitted_voided_and_inactive_prices_never_cost_a_line(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    loc = await market(admin_client)
    closed = await make_location(admin_client, "Cove Grocer", "Cove Grocer")
    bag = await make_product(admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg")
    # The only qualifying price is old: $4.00 a bag 100 days ago.
    stale = await shelf(admin_client, bag["id"], loc["id"], "4.00", observed_at=days_ago(100))
    # Cheaper and newer, every one of them, and none qualifies.
    await post_listing_price(loc, bag, "1.00")
    r = await admin_client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": loc["id"],
            "purchased_at": datetime.now(UTC).isoformat(),
            "lines": [{"product_id": bag["id"], "qty": "1", "unit": "each", "line_total": "1.50"}],
        },
        headers={"Idempotency-Key": "harbor-barley-1"},
    )
    assert r.status_code == 201, r.text
    reopened = await admin_client.post(f"/api/v1/purchases/{r.json()['id']}/reopen")
    assert reopened.status_code == 200, reopened.text
    voided = await shelf(admin_client, bag["id"], loc["id"], "1.00")
    r = await admin_client.post(
        f"/api/v1/price-observations/{voided['id']}/void", json={"reason": "test: mistyped"}
    )
    assert r.status_code == 200 and r.json()["voided"] is True, r.text
    await shelf(admin_client, bag["id"], closed["id"], "0.50")
    await owner_conn.execute(
        "UPDATE vendor_location SET active = false WHERE id = $1", uuid.UUID(closed["id"])
    )
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)

    for basis in ("latest", "cheapest", "average"):
        body = await cost(admin_client, recipe["id"], basis=basis)
        line = lines_by_name(body)["barley"]
        assert line["status"] == "priced", basis
        assert line["price"]["observation_id"] == stale["id"], basis
        assert line["price"]["norm_unit_price"] == "0.004000", basis
        assert line["price"]["stale"] is True, basis
        assert line["consumed_cost"] == "0.8000", basis
        assert body["completeness"]["lines_priced"] == 1, basis
    assert body["stale_after_days"] == 90


# --- 26: truncate and recompute-costs reproduces every figure --------------------------------


_SKIP = {"id", "snapshot_id", "computed_at"}


async def _rows(conn: asyncpg.Connection) -> tuple[list[dict], list[dict]]:
    snaps = [
        {k: v for k, v in dict(r).items() if k not in _SKIP}
        for r in await conn.fetch(
            "SELECT * FROM recipe_cost_snapshot ORDER BY recipe_id, basis, window_days, min_quality"
        )
    ]
    lines = [
        {k: v for k, v in dict(r).items() if k not in _SKIP}
        for r in await conn.fetch(
            "SELECT l.* FROM recipe_cost_line l "
            "JOIN recipe_cost_snapshot s ON s.id = l.snapshot_id "
            "WHERE s.basis = 'latest' AND s.window_days IS NULL AND s.min_quality IS NULL "
            "ORDER BY l.recipe_ingredient_id"
        )
    ]
    return snaps, lines


async def test_26_truncate_and_recompute_costs_reproduces_every_figure(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    await porridge_pantry(admin_client)
    porridge = await indexed(admin_client, recipes_repo, "harbor-porridge.cook", PORRIDGE)
    broth = await indexed(admin_client, recipes_repo, "harbor-broth.cook", BROTH)
    before = {r["id"]: await cost(admin_client, r["id"]) for r in (porridge, broth)}
    snaps_before, lines_before = await _rows(owner_conn)
    assert len(snaps_before) == 2 and len(lines_before) == 5
    computed_before = await owner_conn.fetch("SELECT computed_at FROM recipe_cost_snapshot")

    await owner_conn.execute("TRUNCATE recipe_cost_line, recipe_cost_snapshot")
    assert await owner_conn.fetchval("SELECT count(*) FROM recipe_cost_snapshot") == 0
    run = subprocess.run(
        [sys.executable, "-c", "from app.cli import main; main()", "recompute-costs"],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "recomputed costs for 2 recipes" in run.stdout

    snaps_after, lines_after = await _rows(owner_conn)
    assert snaps_after == snaps_before
    assert lines_after == lines_before
    computed_after = await owner_conn.fetch("SELECT computed_at FROM recipe_cost_snapshot")
    assert {r["computed_at"] for r in computed_after}.isdisjoint(
        {r["computed_at"] for r in computed_before}
    )
    for recipe_id, body in before.items():
        again = await cost(admin_client, recipe_id)
        assert again["totals"] == body["totals"]
        assert [line["consumed_cost"] for line in again["lines"]] == [
            line["consumed_cost"] for line in body["lines"]
        ]


# --- the snapshot key, the list, history and validation -------------------------------------


async def test_a_recompute_replaces_the_snapshot_in_place_and_keys_are_unique_with_nulls(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    owner_conn: asyncpg.Connection,
):
    await barley_only(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    first = await cost(admin_client, recipe["id"])
    await recost(recipe["id"])
    await recost(recipe["id"])
    second = await cost(admin_client, recipe["id"])
    assert second["id"] == first["id"] and second["computed_at"] > first["computed_at"]
    assert await owner_conn.fetchval("SELECT count(*) FROM recipe_cost_snapshot") == 1
    # A minimum quality is a different key; so is a window; a repeat of either is not.
    await cost(admin_client, recipe["id"], min_quality=3)
    await cost(admin_client, recipe["id"], min_quality=3)
    await cost(admin_client, recipe["id"], basis="average", window_days=30)
    await cost(admin_client, recipe["id"], basis="average")
    assert await owner_conn.fetchval("SELECT count(*) FROM recipe_cost_snapshot") == 4
    # Every key the recipe has is rebuilt by one recompute.
    async with get_sessionmaker()() as db:
        assert await recipe_costing.recompute_recipe(db, uuid.UUID(recipe["id"])) == 4
    assert await owner_conn.fetchval("SELECT count(*) FROM recipe_cost_snapshot") == 4


async def test_min_quality_narrows_the_qualifying_prices(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    loc = await market(admin_client)
    bag = await make_product(
        admin_client, "Barley", "Barley bag", pack_qty="1", pack_unit="kg", quality_rating=2
    )
    fine = await make_sibling_product(
        admin_client,
        bag["ingredient"]["id"],
        "Barley fine",
        pack_qty="1",
        pack_unit="kg",
        quality_rating=5,
    )
    await shelf(admin_client, bag["id"], loc["id"], "4.00")
    await shelf(admin_client, fine["id"], loc["id"], "6.00", observed_at=days_ago(1))
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    any_quality = lines_by_name(await cost(admin_client, recipe["id"], basis="cheapest"))["barley"]
    assert any_quality["price"]["product_id"] == bag["id"]
    good = lines_by_name(await cost(admin_client, recipe["id"], basis="cheapest", min_quality=4))[
        "barley"
    ]
    assert good["price"]["product_id"] == fine["id"] and good["consumed_cost"] == "1.2000"


async def test_the_list_carries_the_latest_cost_once_a_snapshot_exists(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await porridge_pantry(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "harbor-porridge.cook", PORRIDGE)
    assert recipe["cost"] is None, "not costed yet"
    await cost(admin_client, recipe["id"], basis="cheapest")
    assert (await recipe_by_path(admin_client, "harbor-porridge.cook"))["cost"] is None, (
        "only the default latest basis feeds the list"
    )
    await cost(admin_client, recipe["id"])
    listed = (await recipe_by_path(admin_client, "harbor-porridge.cook"))["cost"]
    assert listed == {
        "consumed_cost": "3.3000",
        "consumed_cost_high": "3.3000",
        "basket_cost": "12.0000",
        "basket_cost_high": "12.0000",
        "per_serving": "0.8250",
        "lines_priced": 3,
        "lines_total": 3,
        "provisional": False,
        "computed_at": listed["computed_at"],
    }
    # An edit changes the hash: the old snapshot no longer speaks for the file.
    recipes_repo.edit("harbor-porridge.cook", "\nFinish with @dates{2}.\n")
    await rescan(admin_client)
    edited = await recipe_by_path(admin_client, "harbor-porridge.cook")
    assert edited["dirty"] is True and edited["cost"] is None


async def test_history_lists_committed_snapshots_only(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    await barley_only(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    first = await cost(admin_client, recipe["id"])
    recipes_repo.edit("harbor-bowl.cook", "\nAdd @barley{100%g}.\n")
    await rescan(admin_client)
    dirty = await cost(admin_client, recipe["id"])
    assert dirty["provisional"] is True and dirty["content_hash"] != first["content_hash"]
    assert dirty["totals"]["consumed_cost"] == "1.2000"
    r = await admin_client.get(f"/api/v1/recipes/{recipe['id']}/cost/history")
    assert r.status_code == 200, r.text
    history = r.json()
    assert history["basis"] == "latest"
    assert [h["id"] for h in history["items"]] == [first["id"]]
    assert history["items"][0] == {
        "id": first["id"],
        "content_hash": first["content_hash"],
        "head_commit": first["head_commit"],
        "computed_at": first["computed_at"],
        "window_days": None,
        "min_quality": None,
        "totals": first["totals"],
        "lines_priced": 1,
        "lines_total": 1,
    }
    # Committed, the new version joins the history at its own hash.
    recipes_repo.commit("more barley")
    await rescan(admin_client)
    committed = await cost(admin_client, recipe["id"])
    assert committed["provisional"] is True, "the flag clears on the scan in package 6b"
    r = await admin_client.get(
        f"/api/v1/recipes/{recipe['id']}/cost/history", params={"basis": "cheapest"}
    )
    assert r.json() == {"basis": "cheapest", "items": []}


async def test_cost_endpoints_validate_their_parameters_and_the_recipe(
    admin_client: httpx.AsyncClient,
):
    missing = uuid.uuid4()
    assert (await admin_client.get(f"/api/v1/recipes/{missing}/cost")).status_code == 404
    assert (await admin_client.get(f"/api/v1/recipes/{missing}/cost/history")).status_code == 404
    for params in ({"basis": "median"}, {"window_days": 0}, {"min_quality": 6}):
        r = await admin_client.get(f"/api/v1/recipes/{missing}/cost", params=params)
        assert r.status_code == 422, params


async def test_cost_endpoints_need_a_session(client: httpx.AsyncClient):
    assert (await client.get(f"/api/v1/recipes/{uuid.uuid4()}/cost")).status_code == 401


# --- schema and grants ---------------------------------------------------------------------


async def test_migration_0045_round_trips(owner_conn: asyncpg.Connection):
    run_alembic("downgrade", "0044")
    tables = {
        r["tablename"]
        for r in await owner_conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    assert not ({"recipe_cost_snapshot", "recipe_cost_line"} & tables)
    assert "recipe" in tables
    run_alembic("upgrade", "head")
    tables = {
        r["tablename"]
        for r in await owner_conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    assert {"recipe_cost_snapshot", "recipe_cost_line"} <= tables
    nulls_not_distinct = await owner_conn.fetchval(
        "SELECT i.indnullsnotdistinct FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = 'uq_recipe_cost_snapshot_key'"
    )
    assert nulls_not_distinct is True and await owner_conn.fetchval(
        "SELECT indisunique FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = 'uq_recipe_cost_snapshot_key'"
    )
    # Pooled connections hold plans against the dropped tables.
    await dispose_engine()


async def test_the_runtime_role_may_truncate_the_cost_tables(app_conn: asyncpg.Connection):
    for table in ("recipe_cost_snapshot", "recipe_cost_line"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert await app_conn.fetchval(
                "SELECT has_table_privilege('kerp_app', $1, $2)", table, privilege
            ), (table, privilege)
    assert not await app_conn.fetchval(
        "SELECT has_table_privilege('kerp_app', 'price_observation', 'TRUNCATE')"
    )


@pytest.mark.parametrize("basis", ["latest", "average", "cheapest"])
async def test_each_basis_has_a_snapshot_with_its_own_key(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
    basis: str,
):
    await barley_only(admin_client)
    recipe = await indexed(admin_client, recipes_repo, "harbor-bowl.cook", BOWL)
    body = await cost(admin_client, recipe["id"], basis=basis)
    assert body["basis"] == basis
    assert body["window_days"] == (90 if basis == "average" else None)
    assert body["totals"]["consumed_cost"] == "0.8000"
