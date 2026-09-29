"""Removing a line from a purchase (#72).

A line that never reached the price book is deleted. One that did is kept as
its price's provenance: the price is voided ("line removed") and the line is
marked removed, so it no longer shows or counts. The manual edit form matches
lines by id, so removing one from the middle touches only that line.
"""

import uuid
from datetime import UTC, datetime

from tests.pricebook_helpers import make_location, make_product


async def _purchase(client, n: int = 3, stall: str = "Harbor"):
    loc = await make_location(client, f"{stall} Stall", f"{stall} Stall", kind="stand")
    fruit = [f"{stall} {name}" for name in ("figs", "plums", "quince")][:n]
    lines = []
    for i, name in enumerate(fruit, start=1):
        product = await make_product(client, name, name)
        price = f"{i}.00"
        lines.append({"product_id": product["id"], "qty": "1", "unit": "lb", "unit_price": price})
    body = {
        "vendor_location_id": loc["id"],
        "purchased_at": datetime(2026, 5, 2, 16, 0, tzinfo=UTC).isoformat(),
        "lines": lines,
    }
    r = await client.post("/api/v1/purchases", json=body)
    assert r.status_code == 201, r.text
    return r.json(), body


async def _observations(client) -> dict[str, dict]:
    """Every observation by id, voided ones included."""
    r = await client.get("/api/v1/price-observations", params={"include_voided": "true"})
    return {o["id"]: o for o in r.json()["items"]}


def _as_edit(p: dict, body: dict, keep: list[int]) -> dict:
    """The edit form's body: the kept lines, each with its id."""
    lines = [{**body["lines"][i], "id": p["lines"][i]["id"]} for i in keep]
    return {**body, "lines": lines}


async def test_removing_the_middle_line_voids_only_its_price(admin_client):
    p, body = await _purchase(admin_client)
    obs = [ln["observation_id"] for ln in p["lines"]]

    r = await admin_client.put(f"/api/v1/purchases/{p['id']}", json=_as_edit(p, body, [0, 2]))
    assert r.status_code == 200, r.text
    after = r.json()
    assert [ln["id"] for ln in after["lines"]] == [p["lines"][0]["id"], p["lines"][2]["id"]]
    # The two kept lines keep the very prices they had.
    assert [ln["observation_id"] for ln in after["lines"]] == [obs[0], obs[2]]
    assert after["computed_total"] == "4.0000"

    everything = await _observations(admin_client)
    assert everything[obs[1]]["voided"] and everything[obs[1]]["void_reason"] == "line removed"
    assert not everything[obs[0]]["voided"] and not everything[obs[2]]["voided"]


async def test_the_removed_line_is_kept_marked(admin_client, owner_conn):
    p, body = await _purchase(admin_client)
    r = await admin_client.put(f"/api/v1/purchases/{p['id']}", json=_as_edit(p, body, [0, 2]))
    assert r.status_code == 200, r.text
    row = await owner_conn.fetchrow(
        "SELECT removed_at, removed_by FROM purchase_line WHERE id = $1",
        uuid.UUID(p["lines"][1]["id"]),
    )
    assert row["removed_at"] is not None and row["removed_by"] is not None


async def test_an_id_from_elsewhere_is_422_and_changes_nothing(admin_client):
    p, body = await _purchase(admin_client, 2)
    other, _ = await _purchase(admin_client, 1, stall="Pier")
    edit = _as_edit(p, body, [0, 1])
    edit["lines"][1]["id"] = other["lines"][0]["id"]
    r = await admin_client.put(f"/api/v1/purchases/{p['id']}", json=edit)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "unknown_line"
    again = (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()
    assert again["lines"] == p["lines"]


async def test_an_edit_naming_no_saved_line_is_refused(admin_client):
    # A client that still matches lines by position must not replace them all.
    p, body = await _purchase(admin_client, 2)
    body["lines"][1]["unit_price"] = "9.00"
    r = await admin_client.put(f"/api/v1/purchases/{p['id']}", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "line_ids_required"
    assert (await admin_client.get(f"/api/v1/purchases/{p['id']}")).json()["lines"] == p["lines"]


async def test_a_new_line_after_a_removal_gets_a_fresh_number(admin_client):
    p, body = await _purchase(admin_client)
    # Remove the last line, then add one: it must not reuse number 3.
    edit = _as_edit(p, body, [0, 1])
    edit["lines"].append(dict(body["lines"][2]))
    r = await admin_client.put(f"/api/v1/purchases/{p['id']}", json=edit)
    assert r.status_code == 200, r.text
    assert [ln["seq"] for ln in r.json()["lines"]] == [1, 2, 4]


async def test_review_deletes_a_recorded_line_by_voiding_its_price(admin_client):
    p, _ = await _purchase(admin_client, 2)
    assert (await admin_client.post(f"/api/v1/purchases/{p['id']}/reopen")).status_code == 200
    line = p["lines"][1]
    r = await admin_client.delete(f"/api/v1/purchases/{p['id']}/lines/{line['id']}")
    assert r.status_code == 200, r.text
    assert [ln["id"] for ln in r.json()["lines"]] == [p["lines"][0]["id"]]
    assert (await _observations(admin_client))[line["observation_id"]]["voided"]

    # Recommitting does not bring it back.
    r = await admin_client.post(f"/api/v1/purchases/{p['id']}/commit")
    assert r.status_code == 200, r.text
    assert len(r.json()["lines"]) == 1

    # A line added in review never reached the price book and is deleted outright.
    await admin_client.post(f"/api/v1/purchases/{p['id']}/reopen")
    r = await admin_client.post(
        f"/api/v1/purchases/{p['id']}/lines", json={"line_kind": "item", "line_total": "1.00"}
    )
    added = next(ln for ln in r.json()["lines"] if ln["seq"] == 3)
    r = await admin_client.delete(f"/api/v1/purchases/{p['id']}/lines/{added['id']}")
    assert r.status_code == 200, r.text
    assert [ln["seq"] for ln in r.json()["lines"]] == [1]


async def test_inserting_after_a_line_shifts_removed_lines_too(admin_client, owner_conn):
    p, _ = await _purchase(admin_client)
    await admin_client.post(f"/api/v1/purchases/{p['id']}/reopen")
    await admin_client.delete(f"/api/v1/purchases/{p['id']}/lines/{p['lines'][1]['id']}")
    r = await admin_client.post(
        f"/api/v1/purchases/{p['id']}/lines",
        json={"line_kind": "item", "line_total": "0.50", "after_seq": 1},
    )
    assert r.status_code == 201, r.text
    assert [ln["seq"] for ln in r.json()["lines"]] == [1, 2, 4]
    seqs = await owner_conn.fetch(
        "SELECT seq, removed_at IS NOT NULL AS removed FROM purchase_line "
        "WHERE purchase_id = $1 ORDER BY seq",
        uuid.UUID(p["id"]),
    )
    got = [(s["seq"], s["removed"]) for s in seqs]
    assert got == [(1, False), (2, False), (3, True), (4, False)]


async def test_removing_a_line_detaches_its_discount(admin_client):
    p, _ = await _purchase(admin_client, 2)
    await admin_client.post(f"/api/v1/purchases/{p['id']}/reopen")
    parent = p["lines"][1]
    r = await admin_client.post(
        f"/api/v1/purchases/{p['id']}/lines",
        json={"line_kind": "discount", "line_total": "-0.25", "parent_line_id": parent["id"]},
    )
    assert r.status_code == 201, r.text
    r = await admin_client.delete(f"/api/v1/purchases/{p['id']}/lines/{parent['id']}")
    assert r.status_code == 200, r.text
    discount = next(ln for ln in r.json()["lines"] if ln["line_kind"] == "discount")
    assert discount["parent_line_id"] is None
