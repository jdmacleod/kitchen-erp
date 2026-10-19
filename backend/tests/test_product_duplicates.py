"""Possible duplicate products (2P, 03): criteria 106 and 107.

Every product here is invented.
"""

from __future__ import annotations

import uuid

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import event, select, text
from sqlalchemy.orm import selectinload

from app.catalog import sameness
from app.models import Product
from app.services import inbox, product_duplicates
from app.services.proposals import product_facts
from tests.pricebook_helpers import make_product


async def _product(client, ingredient_id: str, name: str, **extra) -> dict:
    r = await client.post(
        "/api/v1/products",
        json={"ingredient_id": ingredient_id, "name": name, "allow_duplicate": True, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _pairs(client) -> list[tuple[str, str]]:
    r = await client.get("/api/v1/products/duplicates")
    assert r.status_code == 200, r.text
    return [tuple(sorted((i["a"]["id"], i["b"]["id"]))) for i in r.json()["items"]]


async def test_only_the_same_product_is_offered(admin_client):
    """Criterion 106: a duplicate is offered; a size, a variant and a merged product never."""
    first = await make_product(
        admin_client, "Plum jam", "Plum Jam", brand="Fernhill", pack_qty="8", pack_unit="oz"
    )
    ing = first["ingredient"]["id"]
    twin = await _product(
        admin_client,
        ing,
        "Fernhill\u2122 plum jam",
        brand="Fernhill",
        pack_qty="227",
        pack_unit="g",
    )
    await _product(admin_client, ing, "Plum Jam", brand="Fernhill", pack_qty="16", pack_unit="oz")
    await _product(admin_client, ing, "Spicy Plum Jam", brand="Fernhill")
    assert await _pairs(admin_client) == [tuple(sorted((first["id"], twin["id"])))]

    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    [row] = [i for i in inbox if i["kind"] == "duplicates"]
    assert row["title"] == "1 possible duplicate product"
    assert row["action_route"] == "/catalog/products?duplicates=1"

    # Merging either product removes the pair (criterion 107).
    r = await admin_client.post(
        f"/api/v1/products/{twin['id']}/merge", json={"survivor_id": first["id"]}
    )
    assert r.status_code == 200, r.text
    assert await _pairs(admin_client) == []
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in inbox if i["kind"] == "duplicates"]


async def test_not_the_same_is_remembered_in_either_order(admin_client):
    """Criterion 107."""
    first = await make_product(admin_client, "Kelp chips", "Kelp Chips")
    second = await _product(admin_client, first["ingredient"]["id"], "kelp chips")
    assert len(await _pairs(admin_client)) == 1
    body = {"a": second["id"], "b": first["id"]}
    r = await admin_client.post("/api/v1/products/duplicates/distinct", json=body)
    assert r.status_code == 204, r.text
    assert await _pairs(admin_client) == []
    # Saying it again, in the other order, changes nothing.
    body = {"a": first["id"], "b": second["id"]}
    r = await admin_client.post("/api/v1/products/duplicates/distinct", json=body)
    assert r.status_code == 204, r.text


async def test_not_the_same_needs_two_existing_products(admin_client):
    first = await make_product(admin_client, "Kelp chips", "Kelp Chips")
    same = {"a": first["id"], "b": first["id"]}
    r = await admin_client.post("/api/v1/products/duplicates/distinct", json=same)
    assert r.status_code == 422 and r.json()["error"]["code"] == "same_product"
    missing = {"a": first["id"], "b": str(uuid.uuid4())}
    r = await admin_client.post("/api/v1/products/duplicates/distinct", json=missing)
    assert r.status_code == 404


# Before #327 every pair of active products went through pg_trgm, then the sameness
# rules: the reference the word index must agree with.
_EVERY_PAIR = text(
    """
    SELECT a.id AS a, b.id AS b
    FROM product a
    JOIN product b ON a.id < b.id AND lower(a.name) % lower(b.name)
    WHERE a.active AND b.active
    """
)

# Duplicates by a trademark sign, a brand in the name, a ", Brand" tail, a lost inch
# mark and a plural; and near misses: a variant, another word, another brand.
_CATALOG = [
    ("Plum jam", "Fernhill plum jam", "Fernhill"),
    ("Plum jam", "Fernhill™ Plum Jam", "Fernhill"),
    ("Plum jam", "Spicy plum jam", "Fernhill"),
    ("Tarn ham", "Smoked tarn ham, Copperleaf", None),
    ("Tarn ham", "Copperleaf smoked tarn ham", "Copperleaf"),
    ("Tarn ham", "Smoked tarn hams", "Copperleaf"),
    ("Kelp wraps", 'Kelp wraps 10"', None),
    ("Kelp wraps", "Kelp wraps 10", None),
    ("Kelp wraps", "Kelp wrap sheets", None),
    ("Oat drink", "Organic oat drink", "Tideline Organic"),
    ("Oat drink", "Oat drink", "Tideline Organic"),
    ("Oat drink", "Oat drink", "Larkspur"),
]


async def _catalog(client, rows=_CATALOG) -> None:
    ingredients: dict[str, str] = {}
    for ingredient, name, brand in rows:
        if ingredient in ingredients:
            await _product(client, ingredients[ingredient], name, brand=brand)
        else:
            made = await make_product(client, ingredient, name, brand=brand)
            ingredients[ingredient] = made["ingredient"]["id"]


async def test_the_word_index_finds_what_comparing_every_pair_finds(admin_client, db_session):
    """#327: duplicates are found without comparing every pair, and none is lost."""
    await _catalog(admin_client)
    every = [(r.a, r.b) for r in await db_session.execute(_EVERY_PAIR)]
    products = {
        p.id: p
        for p in (
            await db_session.execute(
                select(Product).options(
                    selectinload(Product.ingredient), selectinload(Product.identifiers)
                )
            )
        ).scalars()
    }
    expected = sorted(
        (a, b)
        for a, b in every
        if sameness.compare(product_facts(products[a]), product_facts(products[b])).kind == "same"
    )
    found = sorted((d.a.id, d.b.id) for d in await product_duplicates.find_duplicates(db_session))
    assert len(expected) >= 4
    assert found == expected


_index_word = st.sampled_from(["tarn", "kelp", "plum", "jam", "fernhill", "10"])


@given(
    st.lists(
        st.tuples(
            st.lists(_index_word, min_size=1, max_size=3).map(" ".join),
            st.sampled_from([None, "Fernhill", "Fernhill Plum"]),
            st.sampled_from(["", ", Copperleaf", ' 10"']),
        ),
        max_size=12,
    )
)
def test_the_word_index_misses_no_pair_whose_words_agree(rows):
    names = [
        (uuid.UUID(int=i + 1), name + tail, brand) for i, (name, brand, tail) in enumerate(rows)
    ]
    facts = {pid: sameness.Facts(name=n, brand=b) for pid, n, b in names}
    sets = {pid: sameness.word_sets(f) for pid, f in facts.items()}
    every = sorted(
        (a, b) for a in sets for b in sets if a < b and sameness.words_agree(sets[a], sets[b])
    )
    assert product_duplicates._word_pairs(facts) == every


async def _queries(db_session, compute) -> int:
    count = 0

    def tick(*_):
        nonlocal count
        count += 1

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", tick)
    try:
        await compute(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", tick)
    return count


async def test_the_inbox_row_costs_the_same_queries_for_a_few_products_or_many(
    admin_client, db_session
):
    """#327: the inbox's duplicates row doesn't cost more queries as the catalog grows."""
    await _catalog(admin_client)
    few = await _queries(db_session, inbox._duplicates)
    more = [("Reed syrup", f"Reed syrup grade {i}", None) for i in range(20)]
    await _catalog(admin_client, [*more, ("Reed syrup", "Reed syrup grade 3", None)])
    many = await _queries(db_session, inbox._duplicates)
    [row] = await inbox._duplicates(db_session)
    pairs = await product_duplicates.find_duplicates(db_session)
    assert row.title == f"{len(pairs)} possible duplicate products"
    # Produce bought by weight, names, the trigram check on the candidates, the products
    # and their two relations.
    assert many == few <= 6


# --- produce: organic and size grades -------------------------------------------------


async def test_produce_grades_merge_by_weight_and_organic_never(admin_client, db_session):
    from tests.catalog_helpers import make_ingredient, seed_units_via_service
    from tests.pricebook_helpers import make_location, shelf

    await seed_units_via_service(db_session)
    pears = (await make_ingredient(admin_client, "lantern pears", category="produce"))["id"]
    loc = await make_location(admin_client, "Quill Grocers", "Quill Grocers Uptown")
    # Bought by the pound: the grade says nothing, so these two are one product.
    weighed = await _product(admin_client, pears, "Large lantern pears")
    loose = await _product(admin_client, pears, "Lantern pears")
    for p in (weighed, loose):
        await shelf(admin_client, p["id"], loc["id"], "2.40", qty="1.2", unit="lb")
    # Organic is another product, by weight or not.
    await _product(admin_client, pears, "Organic lantern pears", kind="random_weight")
    # By the piece, a jumbo and a small onion are two products.
    onions = (await make_ingredient(admin_client, "lantern onions", category="produce"))["id"]
    await _product(admin_client, onions, "Jumbo lantern onion")
    await _product(admin_client, onions, "Small lantern onion")

    assert await _pairs(admin_client) == [tuple(sorted((weighed["id"], loose["id"])))]
