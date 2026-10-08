"""Review candidates labelled by sameness (04, 2P): criteria 101 and 102.

Vendors, pages and products are invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.catalog.proposals import Candidate
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.services import proposals
from app.services.proposals import Evidence
from tests.pricebook_helpers import make_location, make_product

PAGE = "https://shop.example.test/p/fernhill-plum-jam-8oz"


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


@pytest.fixture
async def store(admin_client) -> dict:
    return await make_location(admin_client, "Juniper Market", "Juniper Market")


@pytest.fixture
async def jams(admin_client) -> dict[str, dict]:
    """Plum jam in two sizes, a spicy variant and an unrelated chutney, all Fernhill."""
    first = await make_product(
        admin_client, "Plum jam", "Plum Jam", brand="Fernhill", pack_qty="8", pack_unit="oz"
    )
    ing = first["ingredient"]["id"]
    out = {"same": first}
    for key, name, qty in [
        ("other_size", "Plum Jam", "16"),
        ("variant", "Spicy Plum Jam", "8"),
        ("similar", "Plum Chutney", "8"),
    ]:
        body = {"ingredient_id": ing, "name": name, "brand": "Fernhill"}
        r = await admin_client.post(
            "/api/v1/products", json={**body, "pack_qty": qty, "pack_unit": "oz"}
        )
        assert r.status_code == 201, r.text
        out[key] = r.json()
    return out


async def clip(admin, store, title: str):
    evidence = Evidence(
        candidates=[
            Candidate("title", title, "page_data"),
            Candidate("brand", "Fernhill", "page_data"),
            Candidate("pack", {"qty": "8", "unit": "oz"}, "page_data"),
        ],
        listing={"vendor_id": store["vendor"]["id"], "canonical_url": PAGE, "title": title},
    )
    async with get_sessionmaker()() as db:
        result = await proposals.create_capture(
            db,
            user=admin,
            channel="clip",
            payload={"page": PAGE, "dom_text": f"{title}. $4.29."},
            evidence=evidence,
            source_url=PAGE,
        )
    return result.proposal


async def test_a_long_page_title_still_finds_the_shorter_catalog_name(
    admin_client, admin, store, jams
):
    """A title with brand and size ("Fernhill Plum Jam, 8 oz") is searched by its
    identifying words too, so the catalog's "Plum Jam" is a candidate."""
    proposal = await clip(admin, store, "Fernhill Plum Jam, 8 oz")
    ids = {c["product_id"] for c in proposal.match["candidates"]}
    assert jams["same"]["id"] in ids and jams["other_size"]["id"] in ids


async def test_candidates_carry_verdicts_in_order_and_preselect_nothing(
    admin_client, admin, store, jams
):
    """Criteria 101 and 102."""
    proposal = await clip(admin, store, "Fernhill Plum Jam, 8 oz")
    r = await admin_client.get(f"/api/v1/product-proposals/{proposal.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    verdicts = {c["product_id"]: c["verdict"] for c in body["candidates"]}
    for key in ("same", "other_size", "variant", "similar"):
        assert verdicts.get(jams[key]["id"]) == key, (key, body["candidates"])
    order = [c["verdict"] for c in body["candidates"]]
    assert order == sorted(order, key=["same", "similar", "other_size", "variant"].index)
    first = body["candidates"][0]
    assert first["product_id"] == jams["same"]["id"]
    assert (first["pack_qty"], first["pack_unit"]) == ("8", "oz")
    spicy = next(c for c in body["candidates"] if c["product_id"] == jams["variant"]["id"])
    assert (spicy["only_here"], spicy["only_there"]) == ([], ["spicy"])
    # A verdict labels; nothing is preselected without a strong match (criterion 74).
    assert body["match"]["preselect"] is None and body["match"]["strong"] is None


async def test_a_deactivated_candidate_is_left_out(admin_client, admin, store, jams):
    proposal = await clip(admin, store, "Fernhill Plum Jam, 8 oz")
    r = await admin_client.post(f"/api/v1/products/{jams['similar']['id']}/deactivate")
    assert r.status_code == 200, r.text
    body = (await admin_client.get(f"/api/v1/product-proposals/{proposal.id}")).json()
    assert jams["similar"]["id"] not in {c["product_id"] for c in body["candidates"]}
