"""Posted prices with a basis (price-basis plan, PB1): "0.69 / lb" is not "0.69 each".

The storefront, its pages and its adapter are invented for these tests.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from app.catalog import proposals as merging
from app.core.config import get_settings
from app.schemas.products_interchange import HelperAnswer
from app.services import lookups, plugins
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests import test_page_captures as tpc
from tests.test_captures import work

no_network = gh.no_network
store = tpc.store  # the invented shop and its location
page = tpc.page
recorded = ih.recorded


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


# --- the value (pure) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3.49", {"amount": "3.49", "qty": "1", "unit": "each"}),
        (Decimal("0.10"), {"amount": "0.10", "qty": "1", "unit": "each"}),
        (
            {"amount": "0.69", "qty": "1", "unit": "lb"},
            {"amount": "0.69", "qty": "1", "unit": "lb"},
        ),
        (
            {"amount": Decimal("5.98"), "qty": Decimal("2"), "unit": "kg"},
            {"amount": "5.98", "qty": "2", "unit": "kg"},
        ),
    ],
)
def test_a_price_is_an_amount_for_a_quantity(value, expected):
    assert merging.price_basis(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        "free",
        "-1",
        "NaN",
        0.69,
        {"amount": "0.69", "unit": "lb"},
        {"amount": "0.69", "qty": "1", "unit": "parsec"},
        {"amount": "0.69", "qty": "0", "unit": "lb"},
        {"amount": "0.69", "qty": "1", "unit": "lb", "extra": "x"},
        {"amount": 0.69, "qty": "1", "unit": "lb"},
    ],
)
def test_anything_else_is_not_a_price(value):
    assert merging.price_basis(value) is None
    with pytest.raises(merging.UnknownCandidate):
        merging.merge([merging.Candidate("price", value, "adapter")])


def test_per_pound_against_each_is_a_conflict():
    fields = merging.merge(
        [
            merging.Candidate("price", {"amount": "0.69", "qty": "1", "unit": "lb"}, "adapter"),
            merging.Candidate("price", "0.69", "page_data"),
        ]
    )
    assert fields["price"]["value"] == {"amount": "0.69", "qty": "1", "unit": "lb"}
    assert fields["price"]["conflict"] is True


def test_the_same_dimension_is_not_a_conflict():
    fields = merging.merge(
        [
            merging.Candidate("price", {"amount": "1.52", "qty": "1", "unit": "kg"}, "adapter"),
            merging.Candidate("price", {"amount": "0.69", "qty": "1", "unit": "lb"}, "page_data"),
        ]
    )
    assert fields["price"]["conflict"] is False


def test_an_adapters_numbers_inside_a_price_never_stay_floats():
    candidate = plugins._candidate(
        {"field": "price", "value": {"amount": 0.69, "qty": 1, "unit": "lb"}}
    )
    assert candidate is not None
    assert candidate.value == {"amount": "0.69", "qty": 1, "unit": "lb"}
    assert merging.price_basis(candidate.value)["amount"] == "0.69"


def test_the_helper_contract_names_the_price_shape():
    answer = HelperAnswer.model_validate(
        {
            "format": "kitchen-erp-products/1",
            "request_id": str(uuid.uuid4()),
            "candidates": [
                {
                    "field": "price",
                    "value": {"amount": Decimal("0.69"), "qty": 1, "unit": "lb"},
                    "source": "adapter",
                },
                {
                    "field": "pack",
                    "value": {"qty": Decimal("2"), "unit": "lb"},
                    "source": "adapter",
                },
            ],
        }
    )
    price, pack = lookups._candidates(answer)
    assert price.value == {"amount": "0.69", "qty": "1", "unit": "lb"}
    assert pack.value == {"qty": "2", "unit": "lb"}


# --- a capture, through to the observation ----------------------------------------------------

ADAPTER = """
def read(page):
    return [{"field": "price", "value": {"amount": "0.69", "qty": "1", "unit": "lb"}}]
"""


@pytest.fixture
def per_pound_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "plugins"
    folder.mkdir()
    name = f"kerp_test_weigh_{uuid.uuid4().hex[:8]}"
    (folder / f"{name}.py").write_text(ADAPTER)
    monkeypatch.setattr(get_settings(), "plugins_path", str(folder))
    monkeypatch.setattr(get_settings(), "product_adapters", [f"{name}:read"])
    plugins._cache.clear()
    yield
    plugins._cache.clear()


async def accept(admin_client, store, recorded, canonical_unit: str) -> uuid.UUID:
    recorded({"ProductReading": {"name": "Bananas", "confidence": 0.3}})
    proposal = (await admin_client.post("/api/v1/product-captures", json=page())).json()
    await work()
    body = (await admin_client.get(f"/api/v1/product-proposals/{proposal['id']}")).json()
    assert body["price"] == {"amount": "0.69", "qty": "1", "unit": "lb", "is_promo": False}
    assert body["fields"]["price"]["conflict"] is True  # the page's JSON-LD said 3.49 each
    ing0 = (await admin_client.post("/api/v1/ingredients", json={"name": "Plantains"})).json()
    blocked = await admin_client.post(
        f"/api/v1/product-proposals/{proposal['id']}/accept",
        json={
            "action": "new",
            "ingredient_id": ing0["id"],
            "record_price": True,
            "vendor_location_id": store["id"],
        },
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "price_conflict"
    ing = (
        await admin_client.post(
            "/api/v1/ingredients", json={"name": "Bananas", "canonical_unit": canonical_unit}
        )
    ).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{proposal['id']}/accept",
        json={
            "action": "new",
            "ingredient_id": ing["id"],
            "record_price": True,
            "price": {"amount": "0.69", "qty": "1", "unit": "lb"},  # the reviewer says
            "vendor_location_id": store["id"],
        },
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["result"]["observation_id"])


async def test_a_per_pound_price_is_recorded_per_pound_and_normalized_to_grams(
    admin_client,
    store,
    per_pound_adapter,
    owner_conn: asyncpg.Connection,
    no_network,
    recorded,
):
    observation = await accept(admin_client, store, recorded, "g")
    row = await owner_conn.fetchrow(
        "SELECT o.price, o.qty, o.unit, n.status, n.norm_unit, n.norm_unit_price "
        "FROM price_observation o JOIN price_norm n ON n.observation_id = o.id WHERE o.id = $1",
        observation,
    )
    assert (row["price"], row["qty"], row["unit"]) == (Decimal("0.6900"), Decimal(1), "lb")
    assert (row["status"], row["norm_unit"]) == ("ok", "g")
    assert row["norm_unit_price"] == (Decimal("0.69") / Decimal("453.59237")).quantize(
        row["norm_unit_price"]
    )


async def test_a_per_pound_price_on_a_counted_product_waits_for_a_bridge(
    admin_client,
    store,
    per_pound_adapter,
    owner_conn: asyncpg.Connection,
    no_network,
    recorded,
):
    observation = await accept(admin_client, store, recorded, "each")
    status = await owner_conn.fetchval(
        "SELECT status FROM price_norm WHERE observation_id = $1", observation
    )
    assert status == "unknown_measure"
