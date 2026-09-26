import statistics
import time
import uuid
from decimal import Decimal

import asyncpg
import httpx

from app.core.ids import new_id
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service


def gs1(body: str) -> str:
    """Append the GS1 check digit so synthetic barcodes are structurally valid."""
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


BASE = 10_000_000_000  # eleven digits; the check digit makes twelve
SAMPLE_BARCODE = gs1(f"{BASE + 42:011d}")


async def test_search_ranks_barcode_first_and_matches_all_fields(
    admin_client: httpx.AsyncClient, db_session
):
    await seed_units_via_service(db_session)
    tomato = await make_ingredient(admin_client, "Tomatoes")
    pasta = await make_ingredient(admin_client, "Rigatoni")
    a = await make_product(admin_client, tomato["id"], "Heirloom tomatoes", brand="Sunny Stand")
    b = await make_product(
        admin_client, pasta["id"], "Rigatoni n.24", brand="De Cecco", barcode="024094070046"
    )
    c = await make_product(
        admin_client, pasta["id"], "Tomato basil rigatoni sauce", brand="Rao Bros"
    )

    hits = (await admin_client.get("/api/v1/products/search", params={"q": "024094070046"})).json()[
        "items"
    ]
    assert hits[0]["id"] == b["id"] and hits[0]["match"] == "barcode"
    assert Decimal(hits[0]["score"]) == 1

    hits = (await admin_client.get("/api/v1/products/search", params={"q": "de cecco"})).json()[
        "items"
    ]
    assert hits[0]["id"] == b["id"] and hits[0]["match"] == "brand"

    hits = (await admin_client.get("/api/v1/products/search", params={"q": "tomato"})).json()[
        "items"
    ]
    ids = [h["id"] for h in hits]
    assert a["id"] in ids and c["id"] in ids
    assert hits[0]["ingredient"]["name"] in {"Tomatoes", "Rigatoni"}

    hits = (await admin_client.get("/api/v1/products/search", params={"q": "rigat"})).json()[
        "items"
    ]
    assert {h["id"] for h in hits} >= {b["id"], c["id"]}
    assert isinstance(hits[0]["score"], str)


async def test_typeahead_under_100ms_with_5000_products(
    admin_client, db_session, owner_conn: asyncpg.Connection
):
    await seed_units_via_service(db_session)
    words = [
        "organic",
        "whole",
        "sliced",
        "roasted",
        "smoked",
        "sweet",
        "dried",
        "fresh",
        "wild",
        "baby",
    ]
    foods = [
        "almond",
        "basil",
        "carrot",
        "dates",
        "endive",
        "fennel",
        "ginger",
        "honey",
        "kale",
        "lemon",
        "mango",
        "nutmeg",
    ]
    brands = ["Sunny Stand", "Hilltop Farm", "Blue Barn", "Coastal Co", "Orchard Row", None]
    ingredient_ids = []
    for food in foods:
        ingredient_ids.append(
            await owner_conn.fetchval(
                "INSERT INTO ingredient (id, name, canonical_unit) "
                "VALUES ($1, $2, 'g') RETURNING id",
                new_id(),
                food.title(),
            )
        )
    rows = []
    for i in range(5000):
        food = foods[i % len(foods)]
        name = f"{words[i % len(words)]} {food} {words[(i * 7) % len(words)]} {i}"
        rows.append(
            (
                new_id(),
                ingredient_ids[i % len(ingredient_ids)],
                brands[i % len(brands)],
                name,
                gs1(f"{BASE + i:011d}"),
            )
        )
    await owner_conn.executemany(
        "INSERT INTO product (id, ingredient_id, brand, name, barcode) VALUES ($1, $2, $3, $4, $5)",
        rows,
    )
    await owner_conn.execute("ANALYZE product; ANALYZE ingredient")

    queries = [
        "org",
        "sweet basil",
        "hilltop",
        "ging",
        "baby lemon",
        "coastal",
        "roasted",
        "nutm",
        SAMPLE_BARCODE,
        "endive",
    ]
    # Correctness first, and apart from timing: every query finds something.
    # This pass also warms the connection pool and the planner's caches, so
    # the first request's setup is not measured as search latency.
    for q in queries:
        r = await admin_client.get("/api/v1/products/search", params={"q": q})
        assert r.status_code == 200 and r.json()["items"], q

    # Latency, measured so that load elsewhere on the machine cannot decide the
    # result (#36): each query is timed REPEATS times and judged by its median,
    # which one descheduled request cannot move, and the bar applies to the
    # slowest query's median. A real regression slows every repeat; a busy
    # Docker build in another terminal slows a few.
    repeats = 7
    medians = {}
    for q in queries:
        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            await admin_client.get("/api/v1/products/search", params={"q": q})
            samples.append((time.perf_counter() - started) * 1000)
        medians[q] = statistics.median(samples)
    slowest = max(medians, key=medians.get)
    assert medians[slowest] < 100, f"median {medians[slowest]:.1f} ms for {slowest!r}: {medians}"
    barcode = (
        await admin_client.get("/api/v1/products/search", params={"q": SAMPLE_BARCODE})
    ).json()["items"]
    assert barcode[0]["barcode"] == SAMPLE_BARCODE and barcode[0]["match"] == "barcode"


async def test_search_requires_query(admin_client):
    r = await admin_client.get("/api/v1/products/search", params={"q": ""})
    assert r.status_code == 422
    r = await admin_client.get(f"/api/v1/products/{uuid.uuid4()}")
    assert r.status_code == 404
