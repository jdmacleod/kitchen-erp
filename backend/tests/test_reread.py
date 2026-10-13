"""Re-reading a receipt's lines through the API (04, 2S; criteria 108–114).

The fixture is a till that prints quantities on rows of their own. Its first
reading here runs with the pass that joins those rows switched off, as if the
receipt had been read before that pass existed; the re-check then runs today's
passes. Every vendor, product and wording is invented.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from app.ingest import structure
from tests import ingest_helpers as ih
from tests.ingest_helpers import load_fixture, run_job, stage_outputs, upload_fixture
from tests.pricebook_helpers import make_location, make_product

receipts_dir = ih.receipts_dir
recorded = ih.recorded

FIXTURE = "two_line_quantities"


def _no_joining(lines, *_, **__):
    return lines, []


async def _first_reading(
    client: httpx.AsyncClient, recorded, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, Any]:
    """Upload and read the fixture as an older pipeline would have: rows unjoined."""
    fixture = load_fixture(FIXTURE)
    transport = recorded(fixture)
    _, job = await upload_fixture(client, fixture)
    with monkeypatch.context() as m:
        m.setattr(structure, "merge_quantity_lines", _no_joining)
        await run_job(job["id"])
    outputs = await stage_outputs(client, job["id"])
    return outputs["lines"]["purchase_id"], job["id"], transport


async def _purchase(client: httpx.AsyncClient, purchase_id: str) -> dict[str, Any]:
    r = await client.get(f"/api/v1/purchases/{purchase_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _live(purchase: dict[str, Any]) -> list[dict[str, Any]]:
    return [line for line in purchase["lines"] if not line.get("removed_at")]


def _shape(line: dict[str, Any]) -> tuple:
    return (line["raw_text"], line["line_kind"], line["qty"], line["unit"], line["line_total"])


async def _check(client: httpx.AsyncClient, purchase_id: str) -> httpx.Response:
    return await client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "check"})


async def test_recheck_proposes_todays_lines_and_applies_them(
    admin_client, receipts_dir, recorded, monkeypatch
):
    purchase_id, job_id, transport = await _first_reading(admin_client, recorded, monkeypatch)
    first = await stage_outputs(admin_client, job_id)
    assert first["lines"]["read_lines"]["lines"]  # 108: the reader's answer is kept
    before = await _purchase(admin_client, purchase_id)
    assert len(_live(before)) == 7
    calls = len(transport.requests)

    r = await _check(admin_client, purchase_id)
    assert r.status_code == 201, r.text
    reread = r.json()
    assert len(transport.requests) == calls  # 108: no model call
    assert reread["status"] == "ready"
    proposal = reread["proposal"]
    assert proposal["before"]["reconciliation"]["mismatch"] is True
    assert proposal["after"]["reconciliation"]["mismatch"] is False
    assert "rows" not in proposal and "read_lines" not in proposal

    r = await admin_client.post(f"/api/v1/rereads/{reread['id']}/apply")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"

    # 109, 113: the lines today's stage would write; the header as it was.
    expected = [
        (
            line["raw_text"],
            line["line_kind"],
            line["qty"],
            line["unit"],
            line["line_total"],
        )
        for line in load_fixture(FIXTURE).expected_lines
    ]
    after = await _purchase(admin_client, purchase_id)
    got = [
        (x["raw_text"], x["line_kind"], x["qty"], x["unit"], x["line_total"]) for x in _live(after)
    ]
    norm = [(t, k, None if q is None else str(float(q)), u, str(float(v))) for t, k, q, u, v in got]
    want = [
        (t, k, None if q is None else str(float(q)), u, str(float(v))) for t, k, q, u, v in expected
    ]
    assert norm == want
    assert [x["seq"] for x in _live(after)] == [1, 2, 3, 4]
    for key in ("purchased_at", "total", "tax", "subtotal", "status"):
        assert after[key] == before[key]
    assert "reconcile_mismatch" not in after["flags"]

    latest = await stage_outputs(admin_client, job_id)
    assert latest["lines"]["reread_id"] == reread["id"]

    # 112: the same re-check again has nothing to change.
    again = await _check(admin_client, purchase_id)
    assert again.status_code == 201, again.text
    assert again.json()["status"] == "no_changes"


async def test_a_persons_lines_are_kept_and_products_stay(
    admin_client, receipts_dir, recorded, monkeypatch
):
    purchase_id, _, _ = await _first_reading(admin_client, recorded, monkeypatch)
    lines = _live(await _purchase(admin_client, purchase_id))
    oat = next(x for x in lines if x["raw_text"].startswith("OAT MILK"))
    product = await make_product(admin_client, "Oat milk", "Fernhill oat milk")
    r = await admin_client.post(
        f"/api/v1/purchases/{purchase_id}/lines/{oat['id']}/resolve",
        json={"product_id": product["id"]},
    )
    assert r.status_code == 200, r.text

    # A person corrects the bananas' amount, adds a bag fee, and removes the
    # avocado count row, all in one Correct the lines.
    rows = []
    for x in _live(await _purchase(admin_client, purchase_id)):
        if x["raw_text"] == "5 @ 0.79":
            continue
        row = {
            "id": x["id"],
            "raw_text": x["raw_text"],
            "line_kind": x["line_kind"],
            "qty": x["qty"],
            "unit": x["unit"],
            "line_total": x["line_total"],
        }
        if x["raw_text"] == "BANANAS":
            row["line_total"] = "1.60"
        rows.append(row)
    rows.append({"raw_text": "BAG FEE", "line_kind": "fee", "line_total": "0.10"})
    r = await admin_client.put(f"/api/v1/purchases/{purchase_id}/lines", json={"lines": rows})
    assert r.status_code == 200, r.text
    edited = {x["raw_text"]: x for x in _live(r.json())}

    r = await _check(admin_client, purchase_id)
    assert r.status_code == 201, r.text
    reread = r.json()
    kept = {
        (c.get("before") or c.get("reading"))["raw_text"]: c["why"]
        for c in reread["proposal"]["changes"]
        if c["action"] == "kept"
    }
    # The removed count row is not listed: today's reading joins it to its item,
    # so the reading agrees it is gone.
    assert kept == {"BANANAS": "edited"}

    r = await admin_client.post(f"/api/v1/rereads/{reread['id']}/apply")
    assert r.status_code == 200, r.text
    after = {x["raw_text"]: x for x in _live(await _purchase(admin_client, purchase_id))}
    # 111: exactly as the person left them.
    assert after["BANANAS"]["id"] == edited["BANANAS"]["id"]
    assert after["BANANAS"]["line_total"] == edited["BANANAS"]["line_total"]
    assert after["BAG FEE"]["id"] == edited["BAG FEE"]["id"]
    assert "5 @ 0.79" not in after
    # A paired line nobody edited keeps its id and its product.
    oat_after = next(x for k, x in after.items() if k.startswith("OAT MILK"))
    assert oat_after["id"] == oat["id"]
    assert oat_after["product"]["id"] == product["id"]


async def test_open_stale_and_closed(admin_client, receipts_dir, recorded, monkeypatch):
    purchase_id, _, _ = await _first_reading(admin_client, recorded, monkeypatch)
    first = (await _check(admin_client, purchase_id)).json()
    assert first["status"] == "ready"

    # 112: one open at a time.
    r = await _check(admin_client, purchase_id)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "reread_open")
    latest = await admin_client.get(f"/api/v1/purchases/{purchase_id}/rereads/latest")
    assert latest.json()["id"] == first["id"]

    # 112: lines changed since the proposal.
    line = _live(await _purchase(admin_client, purchase_id))[-1]
    r = await admin_client.patch(
        f"/api/v1/purchases/{purchase_id}/lines/{line['id']}", json={"line_total": "3.59"}
    )
    assert r.status_code == 200, r.text
    r = await admin_client.post(f"/api/v1/rereads/{first['id']}/apply")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "reread_stale")

    r = await admin_client.post(f"/api/v1/rereads/{first['id']}/dismiss")
    assert r.json()["status"] == "dismissed"
    r = await admin_client.post(f"/api/v1/rereads/{first['id']}/apply")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "reread_closed")
    assert (await _check(admin_client, purchase_id)).status_code == 201


async def test_a_reading_without_its_answer_kept_cannot_be_rechecked(
    admin_client, receipts_dir, recorded, monkeypatch, owner_conn
):
    purchase_id, job_id, _ = await _first_reading(admin_client, recorded, monkeypatch)
    # A reading from before 2S: a later lines result without read_lines.
    await owner_conn.execute(
        "INSERT INTO ingest_stage_result (id, job_id, stage, adapter, adapter_version, output, "
        "duration_ms) SELECT $1, job_id, stage, adapter, adapter_version, "
        "output - 'read_lines', duration_ms FROM ingest_stage_result "
        "WHERE job_id = $2 AND stage = 'lines'",
        uuid.uuid4(),
        uuid.UUID(job_id),
    )
    r = await _check(admin_client, purchase_id)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "not_stored")
    # Read again is queued for the worker instead.
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "read"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"


async def _observations(conn) -> dict[str, list[tuple[str, bool]]]:
    rows = await conn.fetch(
        "SELECT pl.raw_text, o.id, EXISTS (SELECT 1 FROM price_observation_void v "
        "WHERE v.observation_id = o.id) AS voided FROM price_observation o "
        "JOIN purchase_line pl ON pl.id = o.purchase_line_id ORDER BY o.created_at"
    )
    out: dict[str, list[tuple[str, bool]]] = {}
    for row in rows:
        out.setdefault(row["raw_text"], []).append((str(row["id"]), row["voided"]))
    return out


async def test_a_committed_purchase_is_reopened_and_prices_move_on_recommit(
    admin_client, receipts_dir, recorded, monkeypatch, owner_conn
):
    purchase_id, _, _ = await _first_reading(admin_client, recorded, monkeypatch)
    location = await make_location(admin_client, "Lantern Row Grocery", "Lantern Row Grocery")
    r = await admin_client.patch(
        f"/api/v1/purchases/{purchase_id}", json={"vendor_location_id": location["id"]}
    )
    assert r.status_code == 200, r.text
    names = {"OAT MILK": "Fernhill oat milk", "AVOCADO": "Moss Bay avocado"}
    for x in _live(r.json()):
        for start, name in names.items():
            if x["raw_text"].startswith(start) and x["line_kind"] == "item":
                product = await make_product(admin_client, name, name)
                r = await admin_client.post(
                    f"/api/v1/purchases/{purchase_id}/lines/{x['id']}/resolve",
                    json={"product_id": product["id"]},
                )
                assert r.status_code == 200, r.text
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/commit")
    assert r.status_code == 200, r.text
    committed = await _observations(owner_conn)
    oat = next(k for k in committed if k.startswith("OAT MILK"))
    avocado = next(k for k in committed if k.startswith("AVOCADO"))

    reread = (await _check(admin_client, purchase_id)).json()
    r = await admin_client.post(f"/api/v1/rereads/{reread['id']}/apply")
    assert r.status_code == 200, r.text
    # 114: reopened, and nothing emitted or voided yet.
    assert (await _purchase(admin_client, purchase_id))["status"] == "reviewed"
    assert await _observations(owner_conn) == committed

    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/commit")
    assert r.status_code == 200, r.text
    now = await _observations(owner_conn)
    assert now[oat] == committed[oat]  # unchanged line: the same live price
    assert now[avocado][0] == (committed[avocado][0][0], True)  # its count changed: voided
    assert now[avocado][1][1] is False and len(now[avocado]) == 2  # and recorded again


async def test_a_voided_purchase_cannot_be_reread(
    admin_client, receipts_dir, recorded, monkeypatch
):
    purchase_id, _, _ = await _first_reading(admin_client, recorded, monkeypatch)
    location = await make_location(admin_client, "Lantern Row Grocery", "Lantern Row Grocery")
    await admin_client.patch(
        f"/api/v1/purchases/{purchase_id}", json={"vendor_location_id": location["id"]}
    )
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/commit")
    assert r.status_code == 200, r.text
    oat = next(x for x in _live(r.json()) if x["raw_text"].startswith("OAT MILK"))
    product = await make_product(admin_client, "Oat milk", "Fernhill oat milk")
    await admin_client.post(
        f"/api/v1/purchases/{purchase_id}/lines/{oat['id']}/resolve",
        json={"product_id": product["id"]},
    )
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/remove")
    assert r.status_code == 200, r.text
    r = await _check(admin_client, purchase_id)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "voided")


async def test_the_purchase_says_what_rereading_can_offer(
    admin_client, receipts_dir, recorded, monkeypatch, owner_conn
):
    purchase_id, job_id, _ = await _first_reading(admin_client, recorded, monkeypatch)
    assert (await _purchase(admin_client, purchase_id))["reread"] == {
        "can_recheck": True,
        "open": None,
    }
    made = (await _check(admin_client, purchase_id)).json()
    assert (await _purchase(admin_client, purchase_id))["reread"]["open"] == {
        "id": made["id"],
        "status": "ready",
    }
    await admin_client.post(f"/api/v1/rereads/{made['id']}/dismiss")
    await owner_conn.execute(
        "INSERT INTO ingest_stage_result (id, job_id, stage, adapter, adapter_version, output, "
        "duration_ms) SELECT $1, job_id, stage, adapter, adapter_version, "
        "output - 'read_lines', duration_ms FROM ingest_stage_result "
        "WHERE job_id = $2 AND stage = 'lines'",
        uuid.uuid4(),
        uuid.UUID(job_id),
    )
    assert (await _purchase(admin_client, purchase_id))["reread"] == {
        "can_recheck": False,
        "open": None,
    }
