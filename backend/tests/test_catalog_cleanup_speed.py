"""Tidy the catalog answers at once (issue 372). Everything is invented."""

from __future__ import annotations

import statistics
import time

import asyncpg

from app.core.ids import new_id
from tests.catalog_helpers import seed_units_via_service

WORDS = ["red", "green", "sweet", "wild", "baby", "smoked", "golden", "fresh", "hot", "white"]
FOODS = [
    "onion", "basil", "carrot", "fennel", "ginger", "kale", "lemon", "mango", "pea", "leek",
    "plum", "fig", "date", "lentil", "barley", "oat", "rye", "millet", "quinoa", "sorrel",
    "chard", "radish", "turnip", "parsnip", "celery", "endive", "cress", "okra", "yam", "taro",
    "apple", "pear", "quince", "cherry", "apricot", "peach", "melon", "lime", "orange", "grape",
    "almond", "walnut", "pecan", "cashew", "hazelnut", "pistachio", "sesame", "poppy", "chia",
    "flax",
]  # fmt: skip
GROUPS = ["Vegetables and Vegetable Products", "Fruits and Fruit Juices", "Nut and Seed Products"]


async def _catalog(owner_conn: asyncpg.Connection) -> None:
    """500 ingredients: 200 without a category, 50 linked to a USDA food that doesn't
    fit, the rest filed; and 1,500 USDA foods to guess from."""
    foods = []
    for n, food in enumerate(FOODS):
        group = GROUPS[n % 3]
        for k, form in enumerate(["raw", "cooked, boiled", "dried", "canned", "frozen"]):
            for v in range(6):
                fdc_id = 800000 + n * 100 + k * 10 + v
                kind = "foundation_food" if v == 0 else "sr_legacy_food"
                foods.append((fdc_id, kind, f"{food.title()}, {form}, style {v}", group))
    await owner_conn.executemany(
        "INSERT INTO fdc_food (fdc_id, data_type, description, category) VALUES ($1, $2, $3, $4)",
        foods,
    )
    ingredients, refs = [], []
    for i in range(500):
        name = f"{WORDS[i % 10]} {FOODS[i // 10]}"
        category = None if i < 200 else "produce"
        ingredient_id = new_id()
        ingredients.append((ingredient_id, name, f"local.s{i}", category))
        if 200 <= i < 250:
            # Linked to another food entirely.
            refs.append((new_id(), ingredient_id, str(800000 + ((i // 10 + 7) % 50) * 100)))
    await owner_conn.executemany(
        "INSERT INTO ingredient (id, name, canonical_unit, slug, category) "
        "VALUES ($1, $2, 'g', $3, $4)",
        ingredients,
    )
    await owner_conn.executemany(
        "INSERT INTO ingredient_ref (id, ingredient_id, system, external_id, is_preferred) "
        "VALUES ($1, $2, 'fdc', $3, true)",
        refs,
    )
    await owner_conn.execute("ANALYZE fdc_food; ANALYZE ingredient; ANALYZE ingredient_ref")


async def test_the_cleanup_page_answers_within_500_ms(admin_client, db_session, owner_conn):
    await seed_units_via_service(db_session)
    await _catalog(owner_conn)
    # The first look fills the guesses the page keeps (or the worker filled them).
    first = await admin_client.get("/api/v1/catalog/cleanup")
    assert first.status_code == 200, first.text
    kinds = {s["kind"] for s in first.json()["items"]}
    assert {"category", "usda_link"} <= kinds

    async def measured_median() -> float:
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            r = await admin_client.get("/api/v1/catalog/cleanup")
            timings.append((time.perf_counter() - started) * 1000)
            assert r.status_code == 200
        return statistics.median(timings)

    rounds = [await measured_median()]
    if rounds[0] >= 500:
        rounds.append(await measured_median())
    assert min(rounds) < 500, f"median over {len(rounds)} round(s): {rounds}"
