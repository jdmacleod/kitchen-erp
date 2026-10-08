"""Review candidates labelled by sameness (04, 2P): criteria 101 to 104.

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


async def clip(admin, store, title: str, page: str = PAGE, gtin: str | None = None):
    evidence = Evidence(
        candidates=[
            Candidate("title", title, "page_data"),
            Candidate("brand", "Fernhill", "page_data"),
            Candidate("pack", {"qty": "8", "unit": "oz"}, "page_data"),
            *([Candidate("gtin", gtin, "page_data")] if gtin else []),
        ],
        listing={"vendor_id": store["vendor"]["id"], "canonical_url": page, "title": title},
    )
    async with get_sessionmaker()() as db:
        result = await proposals.create_capture(
            db,
            user=admin,
            channel="clip",
            payload={"page": page, "dom_text": f"{title}. $4.29."},
            evidence=evidence,
            source_url=page,
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


async def read(admin_client, proposal_id) -> dict:
    r = await admin_client.get(f"/api/v1/product-proposals/{proposal_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_product_created_later_becomes_a_candidate(admin_client, admin, store):
    """Criterion 103: matches are recomputed when a product is created."""
    proposal = await clip(admin, store, "Fernhill Damson Jam, 8 oz")
    assert (await read(admin_client, proposal.id))["candidates"] == []
    made = await make_product(
        admin_client, "Damson jam", "Damson Jam", brand="Fernhill", pack_qty="8", pack_unit="oz"
    )
    body = await read(admin_client, proposal.id)
    assert [(c["product_id"], c["verdict"]) for c in body["candidates"]] == [(made["id"], "same")]
    assert body["match"]["preselect"] is None


async def test_accepting_one_look_alike_gives_the_other_a_strong_match(admin_client, admin, store):
    """Criteria 103 and 104: two clips of one product through two pages list each other;
    accepting one as new gives the other its barcode match and "Update" preselected."""
    code = "00000000000017"
    first = await clip(admin, store, "Fernhill Quince Jam, 8 oz", gtin=code)
    second = await clip(
        admin, store, "Quince Jam", page="https://shop.example.test/p/quince", gtin=None
    )
    body = await read(admin_client, first.id)
    assert [a["id"] for a in body["look_alikes"]] == [str(second.id)]
    listed = (await admin_client.get("/api/v1/product-proposals")).json()["items"]
    assert {i["id"]: i["look_alikes"] for i in listed}[str(second.id)] == [str(first.id)]

    ing = (await admin_client.post("/api/v1/ingredients", json={"name": "Quince jam"})).json()
    r = await admin_client.post(
        f"/api/v1/product-proposals/{second.id}/accept",
        json={"action": "new", "ingredient_id": ing["id"]},
    )
    assert r.status_code == 200, r.text
    made = r.json()["product_id"]
    body = await read(admin_client, first.id)
    assert body["look_alikes"] == []
    assert body["candidates"][0]["product_id"] == made
    assert body["candidates"][0]["verdict"] == "same"
    assert body["match"]["preselect"] is None  # no barcode on the accepted one: fuzzy only

    # Now the first is accepted as an update and gives the product its barcode; a third
    # clip carrying that barcode is a strong match with "Update" preselected.
    r = await admin_client.post(
        f"/api/v1/product-proposals/{first.id}/accept",
        json={"action": "update", "product_id": made},
    )
    assert r.status_code == 200, r.text
    third = await clip(
        admin, store, "Quince Jam 8oz", page="https://shop.example.test/p/q2", gtin=code
    )
    assert third.match["preselect"] == f"update:{made}"


async def test_a_merge_rematches_onto_the_survivor(admin_client, admin, store):
    """Criterion 103: after a merge, waiting proposals see the survivor, not the loser."""
    keep = await make_product(
        admin_client, "Medlar jam", "Medlar Jam", brand="Fernhill", pack_qty="8", pack_unit="oz"
    )
    ing = keep["ingredient"]["id"]
    body = {"ingredient_id": ing, "name": "Medlar jam", "brand": "Fernhill"}
    loser = (await admin_client.post("/api/v1/products", json=body)).json()
    proposal = await clip(admin, store, "Fernhill Medlar Jam, 8 oz")
    before = {c["product_id"] for c in (await read(admin_client, proposal.id))["candidates"]}
    assert {keep["id"], loser["id"]} <= before
    r = await admin_client.post(
        f"/api/v1/products/{loser['id']}/merge", json={"survivor_id": keep["id"]}
    )
    assert r.status_code == 200, r.text
    after = await read(admin_client, proposal.id)
    assert [c["product_id"] for c in after["candidates"]] == [keep["id"]]
    assert loser["id"] not in {c["product_id"] for c in after["match"]["candidates"]}


async def test_rematching_everything_catches_up_proposals_from_before(
    admin_client, admin, store, monkeypatch
):
    """``kerp rematch-proposals``: a proposal whose product appeared without a rematch
    (as before 2P) gains it as a candidate."""
    monkeypatch.setattr(proposals, "rematch_pending", _noop_once(proposals.rematch_pending))
    proposal = await clip(admin, store, "Fernhill Sloe Jam, 8 oz")
    made = await make_product(admin_client, "Sloe jam", "Sloe Jam", brand="Fernhill")
    assert (await read(admin_client, proposal.id))["candidates"] == []
    async with get_sessionmaker()() as db:
        assert await proposals.rematch_pending(db, None) == 1
    body = await read(admin_client, proposal.id)
    assert [c["product_id"] for c in body["candidates"]] == [made["id"]]


def _noop_once(real):
    """The first call does nothing, standing in for a product made before 2P."""
    calls = []

    async def wrapper(db, product_id):
        calls.append(product_id)
        return 0 if len(calls) == 1 else await real(db, product_id)

    return wrapper
