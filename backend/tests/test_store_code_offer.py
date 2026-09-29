"""Remembering a receipt's store code from review (1F, criterion 61; eng review R3).

The receipts are the synthetic fixtures under tests/fixtures/receipts; the
vendors and branches are invented.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.services.store_codes import MASK, mask_line, store_line
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.geo_helpers import HOME_A, HOME_B, make_location, make_vendor
from tests.ingest_helpers import load_fixture, run_job, stage_outputs, upload_fixture

clean_geo = gh.clean_geo
no_network = gh.no_network
receipts_dir = ih.receipts_dir
recorded = ih.recorded


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


async def _branches(client: httpx.AsyncClient, **codes: list[str]) -> tuple[str, str]:
    vendor = await make_vendor(client, "Harborview Market", kind="chain", price_scope="chain")
    marisol = await make_location(
        client,
        "Harborview San Marisol",
        HOME_A,
        vendor_id=vendor["id"],
        receipt_identifiers=codes.get("marisol", []),
    )
    bayside = await make_location(
        client,
        "Harborview Bayside",
        HOME_B,
        vendor_id=vendor["id"],
        receipt_identifiers=codes.get("bayside", []),
    )
    return marisol["id"], bayside["id"]


async def _read(client: httpx.AsyncClient, recorded, name: str) -> str:
    fixture = load_fixture(name)
    recorded(fixture)
    _, job = await upload_fixture(client, fixture)
    final = await run_job(job["id"])
    assert final.purchase_id is not None
    return str(final.purchase_id)


def offer_url(purchase_id: str) -> str:
    return f"/api/v1/purchases/{purchase_id}/store-code-offer"


def remember_url(purchase_id: str) -> str:
    return f"/api/v1/purchases/{purchase_id}/remember-store-code"


async def test_remembered_code_matches_the_next_receipt(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded
):
    """Criterion 61."""
    marisol, _ = await _branches(admin_client)
    purchase = await _read(admin_client, recorded, "supermarket_loyalty")

    # Two branches with the same name tie, so review picks the location by hand.
    assert (await admin_client.get(offer_url(purchase))).json() == {"offer": None}
    r = await admin_client.patch(
        f"/api/v1/purchases/{purchase}", json={"vendor_location_id": marisol}
    )
    assert r.status_code == 200, r.text

    offer = (await admin_client.get(offer_url(purchase))).json()["offer"]
    assert offer["code"] == "0412" and offer["location_id"] == marisol
    assert offer["location_name"] == "Harborview San Marisol"
    # The street number on the same line is masked; the code itself is shown.
    assert offer["printed_line"] == f"STORE #0412   {MASK} HARBOR VISTA BLVD"

    r = await admin_client.post(remember_url(purchase), json={"code": "0412"})
    assert r.status_code == 200, r.text
    location = (await admin_client.get(f"/api/v1/vendor-locations/{marisol}")).json()
    assert location["receipt_identifiers"] == ["0412"]
    assert (await admin_client.get(offer_url(purchase))).json() == {"offer": None}

    # The next receipt printing the code (misread by OCR as #O412) matches on its own.
    fixture = load_fixture("poor_ocr")
    recorded(fixture)
    _, job = await upload_fixture(admin_client, fixture)
    await run_job(job["id"])
    matched = (await stage_outputs(admin_client, job["id"]))["header"]["location"]
    assert matched["matched"] is True and matched["vendor_location_id"] == marisol


async def test_no_offer_when_a_sibling_branch_has_the_code(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded
):
    marisol, bayside = await _branches(admin_client, bayside=["0412"])
    purchase = await _read(admin_client, recorded, "supermarket_loyalty")
    # The receipt matched Bayside by its code; a person moves it to San Marisol.
    await admin_client.patch(f"/api/v1/purchases/{purchase}", json={"vendor_location_id": marisol})
    assert (await admin_client.get(offer_url(purchase))).json() == {"offer": None}
    refused = await admin_client.post(remember_url(purchase), json={"code": "0412"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "store_code_not_offered"
    unchanged = (await admin_client.get(f"/api/v1/vendor-locations/{marisol}")).json()
    assert unchanged["receipt_identifiers"] == []
    assert bayside  # the sibling keeps its code


async def test_only_the_offered_code_is_accepted(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded
):
    marisol, _ = await _branches(admin_client)
    purchase = await _read(admin_client, recorded, "supermarket_loyalty")
    await admin_client.patch(f"/api/v1/purchases/{purchase}", json={"vendor_location_id": marisol})
    refused = await admin_client.post(remember_url(purchase), json={"code": "4471"})
    assert refused.status_code == 409
    location = (await admin_client.get(f"/api/v1/vendor-locations/{marisol}")).json()
    assert location["receipt_identifiers"] == []


# --- which printed lines read like a store number ------------------------------------

RECEIPT = """INVENTED MARKET
STORE 0217  TERM 0042
4 PIER LANE
SEASIDE
03/04/2026 17:42
MILK 2.99
BREAD 3.49
EGGS 4.99
SUBTOTAL 11.47
TOTAL 11.47
MEMBER 55501234
THANK YOU"""


@pytest.mark.parametrize(
    "line", ["STORE #0217", "Store No. 0217", "STR 0217", "BRANCH: 0217", "LOC#0217"]
)
def test_a_code_after_a_store_label_is_offered(line: str):
    assert store_line("0217", RECEIPT.replace("STORE 0217  TERM 0042", line)) == line


def test_a_code_near_the_top_is_offered_with_other_numbers_masked():
    line = store_line("0217", RECEIPT)
    assert line == "STORE 0217  TERM 0042"
    assert mask_line(line, "0217") == f"STORE 0217  TERM {MASK}"


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("55501234", RECEIPT),  # a member number, printed low and labelled
        ("0217", RECEIPT.replace("STORE 0217", "CARD 0217")),  # labelled as a card
        ("0217", RECEIPT.replace("STORE 0217", "TEL 0217")),  # labelled as a phone
        ("0217", "\n".join(RECEIPT.splitlines()[3:] + ["STORE 0217"])),  # printed at the foot
        ("0999", RECEIPT),  # not printed at all
        ("0042", RECEIPT),  # the terminal number on the store's own line
        ("0217", RECEIPT.replace("STORE 0217", "INVENTED 0217")),  # no store label
    ],
)
def test_codes_that_do_not_read_like_a_store_number_are_not_offered(code: str, text: str):
    assert store_line(code, text) is None
