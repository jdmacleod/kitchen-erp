import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import httpx

from app.core.ids import new_id
from tests.pricebook_helpers import SYNTH, make_location, make_product, shelf

D = Decimal


async def three_locations(client: httpx.AsyncClient):
    chain_a = await make_location(
        client, "Big Chain", "Big Chain North", kind="chain", price_scope="chain", coords=SYNTH[0]
    )
    chain_b = await make_location(
        client, None, "Big Chain South", vendor_id=chain_a["vendor"]["id"], coords=SYNTH[1]
    )
    indie = await make_location(client, "Indie Grocer", "Indie Grocer", coords=SYNTH[2])
    return chain_a, chain_b, indie


async def test_product_history_series_and_promo_marks(admin_client):
    chain_a, chain_b, indie = await three_locations(admin_client)
    box = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    await shelf(admin_client, box["id"], chain_a["id"], "3.49", observed_at=old)
    await shelf(admin_client, box["id"], chain_b["id"], "2.99", is_promo=True)
    await shelf(admin_client, box["id"], indie["id"], "4.29")
    h = (await admin_client.get(f"/api/v1/products/{box['id']}/prices")).json()
    series = {p["series"] for p in h["points"]}
    assert series == {chain_a["vendor"]["id"], indie["id"]}
    assert [p["is_promo"] for p in h["points"]] == [False, True, False]
    latest = {row["location_id"]: row for row in h["latest"]}
    assert set(latest) == {chain_a["id"], chain_b["id"], indie["id"]}
    assert latest[chain_a["id"]]["price"] == "2.9900"
    assert latest[chain_a["id"]]["stale"] is False and D(latest[indie["id"]]["age_days"]) < 1


async def test_ingredient_offers_min_quality_filter(admin_client):
    _, _, indie = await three_locations(admin_client)
    good = await make_product(
        admin_client,
        "Olive oil",
        "Estate oil",
        canonical_unit="ml",
        pack_qty="500",
        pack_unit="ml",
        quality_rating=5,
    )
    r = await admin_client.post(
        "/api/v1/products",
        json={
            "ingredient_id": good["ingredient"]["id"],
            "name": "Bargain oil",
            "pack_qty": "1",
            "pack_unit": "l",
            "quality_rating": 2,
        },
    )
    cheap = r.json()
    await shelf(admin_client, good["id"], indie["id"], "12.00")
    await shelf(admin_client, cheap["id"], indie["id"], "6.00")
    iid = good["ingredient"]["id"]
    offers = (await admin_client.get(f"/api/v1/ingredients/{iid}/offers")).json()
    assert [o["product_id"] for o in offers["items"]] == [cheap["id"], good["id"]]
    assert offers["items"][0]["norm_unit_price"] == "0.006000"
    filtered = (
        await admin_client.get(f"/api/v1/ingredients/{iid}/offers", params={"min_quality": 4})
    ).json()
    assert [o["product_id"] for o in filtered["items"]] == [good["id"]]
    assert offers["stale_thresholds"] == {"fresh": 14, "refrigerated": 45, "shelf_stable": 120}


async def test_compare_matrix_highlights_cheapest_and_leaves_unknown_empty(admin_client):
    chain_a, chain_b, indie = await three_locations(admin_client)
    rig = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    oil = await make_product(
        admin_client, "Olive oil", "Estate oil", canonical_unit="ml", pack_qty="500", pack_unit="ml"
    )
    await shelf(admin_client, rig["id"], chain_a["id"], "3.49")
    await shelf(admin_client, rig["id"], indie["id"], "2.99")
    await shelf(admin_client, oil["id"], chain_b["id"], "12.00")
    r = await admin_client.post(
        "/api/v1/price-book/compare",
        json={"ingredient_ids": [rig["ingredient"]["id"], oil["ingredient"]["id"]]},
    )
    assert r.status_code == 200, r.text
    matrix = r.json()
    assert {v["name"] for v in matrix["vendors"]} == {"Big Chain", "Indie Grocer"}
    rows = {row["ingredient_name"]: row for row in matrix["rows"]}
    rig_cells = rows["Rigatoni"]["cells"]
    assert rig_cells[indie["vendor"]["id"]]["cheapest"] is True
    assert rig_cells[chain_a["vendor"]["id"]]["cheapest"] is False
    oil_cells = rows["Olive oil"]["cells"]
    assert set(oil_cells) == {chain_a["vendor"]["id"]}, "unknown cells are absent, never zero"
    assert oil_cells[chain_a["vendor"]["id"]]["cheapest"] is True


async def test_stale_marking_and_exclusion(admin_client, owner_conn: asyncpg.Connection):
    _, _, indie = await three_locations(admin_client)
    r = await admin_client.post(
        "/api/v1/products",
        json={
            "ingredient": {"name": "Strawberries", "perishability": "fresh"},
            "name": "Berries",
            "pack_qty": "1",
            "pack_unit": "lb",
        },
    )
    berries = r.json()
    iid = berries["ingredient"]["id"]
    stale_at = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    await shelf(admin_client, berries["id"], indie["id"], "4.99", observed_at=stale_at)
    offers = (await admin_client.get(f"/api/v1/ingredients/{iid}/offers")).json()
    assert offers["items"][0]["stale"] is True and D(offers["items"][0]["age_days"]) > 19
    excluded = (
        await admin_client.get(
            f"/api/v1/ingredients/{iid}/offers", params={"exclude_stale": "true"}
        )
    ).json()
    assert excluded["items"] == []
    body = {"ingredient_ids": [iid], "exclude_stale": True}
    matrix = (await admin_client.post("/api/v1/price-book/compare", json=body)).json()
    assert matrix["rows"][0]["cells"] == {}
    body = {"ingredient_ids": [iid]}
    matrix = (await admin_client.post("/api/v1/price-book/compare", json=body)).json()
    assert next(iter(matrix["rows"][0]["cells"].values()))["stale"] is True


async def test_cheapest_layer_respects_chain_scope(admin_client):
    chain_a, chain_b, indie = await three_locations(admin_client)
    rig = await make_product(admin_client, "Rigatoni", "Rigatoni box", pack_qty="1", pack_unit="lb")
    await shelf(admin_client, rig["id"], chain_a["id"], "3.49")
    await shelf(admin_client, rig["id"], indie["id"], "2.99")
    layer = (
        await admin_client.get(
            "/api/v1/price-book/cheapest", params={"ingredient_id": rig["ingredient"]["id"]}
        )
    ).json()
    assert layer["unit"] == "g"
    by_loc = {i["location_id"]: i for i in layer["items"]}
    assert set(by_loc) == {chain_a["id"], chain_b["id"], indie["id"]}
    assert by_loc[chain_b["id"]]["norm_unit_price"] == by_loc[chain_a["id"]]["norm_unit_price"]
    assert D(by_loc[indie["id"]]["norm_unit_price"]) < D(by_loc[chain_a["id"]]["norm_unit_price"])
    assert by_loc[indie["id"]]["norm_unit"] == "g" and by_loc[indie["id"]]["lat"] == "33.700000"


async def test_needs_bridge_clears_and_price_appears_in_compare(admin_client):
    _, _, indie = await three_locations(admin_client)
    flour = await make_product(admin_client, "Flour", "Bulk flour")
    await shelf(admin_client, flour["id"], indie["id"], "0.89", qty="1", unit="cup")
    iid = flour["ingredient"]["id"]
    body = {"ingredient_ids": [iid]}
    matrix = (await admin_client.post("/api/v1/price-book/compare", json=body)).json()
    assert matrix["rows"][0]["cells"] == {}
    assert len((await admin_client.get("/api/v1/price-book/needs-bridge")).json()["items"]) == 1
    await admin_client.patch(
        f"/api/v1/ingredients/{iid}", json={"density_g_per_ml": "0.593", "density_source": "usda"}
    )
    assert (await admin_client.get("/api/v1/price-book/needs-bridge")).json()["items"] == []
    matrix = (await admin_client.post("/api/v1/price-book/compare", json=body)).json()
    cells = matrix["rows"][0]["cells"]
    assert len(cells) == 1 and next(iter(cells.values()))["norm_unit"] == "g"


async def test_location_panel(admin_client, admin):
    _, _, indie = await three_locations(admin_client)
    rig = await make_product(admin_client, "Rigatoni", "Rigatoni box")
    body = {
        "vendor_location_id": indie["id"],
        "purchased_at": datetime.now(UTC).isoformat(),
        "lines": [{"product_id": rig["id"], "qty": "2", "unit": "each", "unit_price": "2.49"}],
    }
    await admin_client.post("/api/v1/purchases", json=body)
    panel = (
        await admin_client.get(
            f"/api/v1/vendor-locations/{indie['id']}/price-panel", params={"days": 30}
        )
    ).json()
    assert panel["visits"] == 1 and panel["spend"] == "4.9800" and panel["last_visit"]
    assert panel["recent"][0]["product_name"] == "Rigatoni box"


async def test_comparison_views_p95_under_500ms_with_20000_observations(
    admin_client, admin, owner_conn: asyncpg.Connection
):
    vendors, locations = [], []
    for v in range(5):
        vendors.append(
            await owner_conn.fetchval(
                "INSERT INTO vendor (id, name, kind, price_scope) VALUES ($1, $2, 'chain', $3) "
                "RETURNING id",
                new_id(),
                f"Perf Vendor {v}",
                "chain" if v % 2 == 0 else "location",
            )
        )
    for i in range(10):
        place = await owner_conn.fetchval(
            "INSERT INTO place (id, lat, lon, geom) "
            "VALUES ($1, CAST($2 AS numeric), CAST($3 AS numeric), ST_SetSRID(ST_MakePoint("
            "CAST(CAST($3 AS numeric) AS float8), CAST(CAST($2 AS numeric) AS float8)), 4326)"
            "::geography) RETURNING id",
            new_id(),
            D("33.1") + D(i) / 100,
            D("-120.9") + D(i) / 100,
        )
        locations.append(
            await owner_conn.fetchval(
                "INSERT INTO vendor_location (id, vendor_id, place_id, name) "
                "VALUES ($1, $2, $3, $4) RETURNING id",
                new_id(),
                vendors[i % 5],
                place,
                f"Perf Location {i}",
            )
        )
    ingredients = [
        await owner_conn.fetchval(
            "INSERT INTO ingredient (id, name, canonical_unit) VALUES ($1, $2, 'each') "
            "RETURNING id",
            new_id(),
            f"Perf ingredient {k}",
        )
        for k in range(20)
    ]
    products = []
    for k, iid in enumerate(ingredients):
        for j in range(10):
            products.append(
                await owner_conn.fetchval(
                    "INSERT INTO product (id, ingredient_id, name, quality_rating, pack_qty, "
                    "pack_unit) VALUES ($1, $2, $3, $4, 1, 'each') RETURNING id",
                    new_id(),
                    iid,
                    f"Perf product {k}-{j}",
                    (j % 5) + 1,
                )
            )
    obs_rows, norm_rows = [], []
    base = datetime.now(UTC)
    for n, pid in enumerate(products):  # 200 products x 100 observations = 20,000
        for t in range(100):
            oid = new_id()
            price = D("1.00") + D(n % 50) / 10 + D(t) / 100
            when = base - timedelta(days=t * 3)
            obs_rows.append(
                (
                    oid,
                    pid,
                    locations[(n + t) % 10],
                    when,
                    price,
                    D(1),
                    "each",
                    t % 4 == 0,
                    "shelf",
                    admin.id,
                )
            )
            norm_rows.append((oid, D(1), "each", price, "ok", "pack", "1"))
    await owner_conn.executemany(
        "INSERT INTO price_observation (id, product_id, vendor_location_id, observed_at, price, "
        "qty, unit, is_promo, source, entered_by) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        obs_rows,
    )
    await owner_conn.executemany(
        "INSERT INTO price_norm (observation_id, canonical_qty, norm_unit, norm_unit_price, "
        "status, bridge_kind, convert_version) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        norm_rows,
    )
    assert await owner_conn.fetchval("SELECT count(*) FROM price_observation") == 20000
    await owner_conn.execute(
        "ANALYZE price_observation; ANALYZE price_norm; ANALYZE product; ANALYZE vendor_location"
    )

    timings = []
    ids = [str(i) for i in ingredients]
    for k in range(40):
        started = time.perf_counter()
        if k % 2 == 0:
            body = {"ingredient_ids": ids[: 10 + k % 10], "min_quality": 3}
            r = await admin_client.post("/api/v1/price-book/compare", json=body)
            assert r.status_code == 200 and r.json()["rows"]
        else:
            r = await admin_client.get(f"/api/v1/ingredients/{ids[k % 20]}/offers")
            assert r.status_code == 200 and r.json()["items"]
        timings.append((time.perf_counter() - started) * 1000)
    p95 = statistics.quantiles(timings, n=20)[18]
    assert p95 < 500, f"p95 {p95:.0f} ms"
    layer = await admin_client.get("/api/v1/price-book/cheapest", params={"ingredient_id": ids[0]})
    assert layer.status_code == 200 and len(layer.json()["items"]) == 10
    assert uuid.UUID(layer.json()["items"][0]["location_id"])
