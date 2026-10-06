"""Review's "Correct the lines" (#182): every line of a draft saved in one request.

A badly read receipt was corrected one line at a time, several steps each, and
its garbled wording could not be fixed, so aliases learned it. The table sends
the lines as they should read, matched to saved lines by id.
"""

from __future__ import annotations

import pytest

from app.services.resolution import set_ranker
from tests.pricebook_helpers import make_location, make_product
from tests.resolution_helpers import make_receipt_purchase


@pytest.fixture(autouse=True)
def _no_ranker():
    set_ranker(None)
    yield
    set_ranker(None)


async def _draft(admin_client, admin, lines, total, name="Kestrel Pantry"):
    loc = await make_location(admin_client, name, f"{name} Harbour")
    pid = await make_receipt_purchase(admin.id, loc["id"], lines, total=total)
    body = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()
    return loc, pid, sorted(body["lines"], key=lambda x: x["seq"])


def _row(line: dict, **change) -> dict:
    """A saved line as the table sends it back, with any changes."""
    row = {
        "id": line["id"],
        "raw_text": line["raw_text"],
        "line_kind": line["line_kind"],
        "qty": line["qty"],
        "unit": line["unit"],
        "line_total": line["line_total"],
    }
    row.update(change)
    return row


async def _save(admin_client, pid, rows):
    return await admin_client.put(f"/api/v1/purchases/{pid}/lines", json={"lines": rows})


def _ordered(body: dict) -> list[dict]:
    return sorted(body["lines"], key=lambda x: x["seq"])


async def test_a_receipt_is_retyped_in_one_request(admin_client, admin):
    _, pid, garbled = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "M1LK 0AT 3", "line_total": "349.00"},
            {"raw_text": "~~ :: ..", "line_total": "0.00"},
        ],
        total="61.00",
    )
    rows = [
        {"raw_text": f"PANTRY ITEM {n:02d}", "line_total": "3.05", "qty": "1", "unit": "each"}
        for n in range(1, 21)
    ]
    r = await _save(admin_client, pid, rows)
    assert r.status_code == 200, r.text
    body = r.json()
    lines = _ordered(body)
    assert [ln["raw_text"] for ln in lines] == [f"PANTRY ITEM {n:02d}" for n in range(1, 21)]
    assert [ln["seq"] for ln in lines] == list(range(1, 21))
    assert body["computed_total"] == "61.0000"
    assert "total_mismatch" not in body["flags"]
    assert body["trust"] == "adds_up"
    assert not {ln["id"] for ln in lines} & {ln["id"] for ln in garbled}


async def test_unchanged_lines_keep_their_product_and_flags(admin_client, admin):
    rice = await make_product(admin_client, "Rice", "Bayleaf jasmine rice")
    _, pid, (first, second) = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "BAYLEAF JASMINE 4.10", "line_total": "4.10", "flags": ["qty_assumed"]},
            {"raw_text": "FENNEL BULB 199", "line_total": "199.00", "flags": ["decimal_missing"]},
        ],
        total="6.09",
    )
    chosen = await admin_client.post(
        f"/api/v1/purchases/{pid}/lines/{first['id']}/resolve", json={"product_id": rice["id"]}
    )
    assert chosen.status_code == 200, chosen.text
    r = await _save(
        admin_client, pid, [_row(first), _row(second, line_total="1.99", raw_text="FENNEL BULB")]
    )
    assert r.status_code == 200, r.text
    kept, fixed = _ordered(r.json())
    assert kept["product"]["id"] == rice["id"] and kept["resolution"] == "manual"
    assert kept["flags"] == ["qty_assumed"]  # nobody touched its quantity
    assert fixed["line_total"] == "1.9900" and fixed["flags"] == []
    assert fixed["raw_text"] == "FENNEL BULB" and fixed["raw_text_norm"] == "FENNEL BULB"
    assert "total_mismatch" not in r.json()["flags"]


async def test_corrected_wording_finds_the_alias_the_garbled_one_missed(admin_client, admin):
    oats = await make_product(admin_client, "Oats", "Larkfield rolled oats")
    loc, earlier, (seen,) = await _draft(
        admin_client, admin, [{"raw_text": "LARKFIELD OATS 4.25", "line_total": "4.25"}], "4.25"
    )
    taught = await admin_client.post(
        f"/api/v1/purchases/{earlier}/lines/{seen['id']}/resolve", json={"product_id": oats["id"]}
    )
    assert taught.status_code == 200, taught.text
    pid = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "LARKF1ELD 0ATS 4.25", "line_total": "4.25"}]
    )
    (line,) = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()["lines"]
    r = await _save(admin_client, pid, [_row(line, raw_text="LARKFIELD OATS 4.25")])
    assert r.status_code == 200, r.text
    (fixed,) = r.json()["lines"]
    assert fixed["resolution"] == "alias" and fixed["product"]["id"] == oats["id"]


async def test_a_chosen_product_stays_and_its_alias_learns_the_corrected_wording(
    admin_client, admin
):
    tahini = await make_product(admin_client, "Tahini", "Saltmarsh tahini")
    loc, pid, (line,) = await _draft(
        admin_client, admin, [{"raw_text": "SALTMRSH TAH1NI 6.40", "line_total": "6.40"}], "6.40"
    )
    await admin_client.post(
        f"/api/v1/purchases/{pid}/lines/{line['id']}/resolve", json={"product_id": tahini["id"]}
    )
    r = await _save(admin_client, pid, [_row(line, raw_text="SALTMARSH TAHINI 6.40")])
    assert r.status_code == 200, r.text
    (fixed,) = r.json()["lines"]
    assert fixed["product"]["id"] == tahini["id"] and fixed["resolution"] == "manual"
    # The next receipt printing the clean wording resolves on its own.
    later = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "SALTMARSH TAHINI 6.40", "line_total": "6.40"}]
    )
    resolved = await admin_client.post(
        f"/api/v1/purchases/{later}/lines/"
        f"{(await admin_client.get(f'/api/v1/purchases/{later}')).json()['lines'][0]['id']}"
        "/re-resolve"
    )
    assert resolved.json()["lines"][0]["resolution"] == "alias"


async def test_rows_insert_anywhere_and_a_discount_attaches_by_position(admin_client, admin):
    _, pid, (bread, jam) = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "SEEDED LOAF 5.20", "line_total": "5.20"},
            {"raw_text": "QUINCE JAM 4.80", "line_total": "4.80"},
        ],
        total="9.50",
    )
    rows = [
        _row(bread),
        {"raw_text": "LOAF SAVING", "line_kind": "discount", "line_total": "0.50", "attach_to": 0},
        _row(jam),
    ]
    r = await _save(admin_client, pid, rows)
    assert r.status_code == 200, r.text
    lines = _ordered(r.json())
    assert [ln["raw_text"] for ln in lines] == [
        "SEEDED LOAF 5.20",
        "LOAF SAVING",
        "QUINCE JAM 4.80",
    ]
    assert [ln["seq"] for ln in lines] == [1, 2, 3]
    assert lines[1]["parent_line_id"] == lines[0]["id"]
    assert r.json()["computed_total"] == "9.5000"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"attach_to": 1}, "not_attachable"),
        ({"attach_to": 5}, "bad_attach"),
        ({"unit": "furlong", "qty": "1"}, "unknown_unit"),
    ],
)
async def test_a_bad_row_changes_nothing(admin_client, admin, change, code):
    _, pid, (first, second) = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "CAPERS 2.75", "line_total": "2.75"},
            {"raw_text": "ANCHOVY 3.95", "line_total": "3.95"},
        ],
        total="6.70",
    )
    r = await _save(admin_client, pid, [_row(first, raw_text="CAPERS", **change), _row(second)])
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code
    after = _ordered((await admin_client.get(f"/api/v1/purchases/{pid}")).json())
    assert [ln["raw_text"] for ln in after] == ["CAPERS 2.75", "ANCHOVY 3.95"]


async def test_ids_name_lines_of_this_purchase_once(admin_client, admin):
    _, pid, (line,) = await _draft(
        admin_client, admin, [{"raw_text": "SUMAC 3.10", "line_total": "3.10"}], "3.10"
    )
    _, other, (stranger,) = await _draft(
        admin_client,
        admin,
        [{"raw_text": "ZAATAR 2.90", "line_total": "2.90"}],
        "2.90",
        "Wren Deli",
    )
    twice = await _save(admin_client, pid, [_row(line), _row(line)])
    assert twice.status_code == 422
    foreign = await _save(admin_client, pid, [_row(stranger)])
    assert foreign.status_code == 422 and foreign.json()["error"]["code"] == "unknown_line"


async def test_a_committed_purchase_is_refused(admin_client, admin):
    _, pid, (line,) = await _draft(
        admin_client, admin, [{"raw_text": "MISO 5.60", "line_total": "5.60"}], "5.60"
    )
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).status_code == 200
    r = await _save(admin_client, pid, [_row(line, line_total="5.70")])
    assert r.status_code == 409 and r.json()["error"]["code"] == "committed"


async def test_a_reopened_purchase_voids_a_dropped_price_and_numbers_live_lines_first(
    admin_client, admin
):
    leeks = await make_product(admin_client, "Leeks", "Loose leeks")
    _, pid, (first, second) = await _draft(
        admin_client,
        admin,
        [
            {"raw_text": "LEEKS 2.40", "line_total": "2.40"},
            {"raw_text": "PARSNIPS 1.85", "line_total": "1.85"},
        ],
        total="4.25",
    )
    await admin_client.post(
        f"/api/v1/purchases/{pid}/lines/{first['id']}/resolve", json={"product_id": leeks["id"]}
    )
    committed = (await admin_client.post(f"/api/v1/purchases/{pid}/commit")).json()
    assert _ordered(committed)[0]["observation_id"] is not None
    assert (await admin_client.post(f"/api/v1/purchases/{pid}/reopen")).status_code == 200
    rows = [
        {"raw_text": "SHALLOTS 2.40", "line_total": "2.40"},
        _row(second),
    ]
    r = await _save(admin_client, pid, rows)
    assert r.status_code == 200, r.text
    body = r.json()
    lines = _ordered(body)
    assert [(ln["seq"], ln["raw_text"]) for ln in lines] == [
        (1, "SHALLOTS 2.40"),
        (2, "PARSNIPS 1.85"),
    ]
    assert body["removed_line_count"] == 1
    prices = await admin_client.get(
        "/api/v1/price-observations", params={"product_id": leeks["id"]}
    )
    assert prices.json()["items"] == []
