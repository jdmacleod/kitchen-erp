"""A second transcript beside the vision transcriber's, for the header (04, 2O; #488).

The transcriber often leaves out a store's header. Tesseract's text is kept beside
it, and the header stage looks there for the store number and, when the model
read no phone, for the printed phone. Every receipt, branch and number here is
invented (the reserved 555 exchange).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ingest import header
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.geo_helpers import HOME_A, HOME_B, MID, make_location, make_vendor
from tests.ingest_helpers import SpyAdapter, load_fixture, run_job, stage_outputs, upload_fixture
from tests.test_ingest_ocr_vision import VISION, _transcript, transcriber

clean_geo = gh.clean_geo
no_network = gh.no_network
receipts_dir = ih.receipts_dir
ocr_registry = ih.ocr_registry
__all__ = ["transcriber"]

PHONE = "(555) 555-0142"
SECOND_TEXT = f"HARBORVIEW MARKET\n1200 HARBOR VISTA BLVD\n{PHONE}\nSTORE 0412\nKELP 1.00\n"


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


async def branches(client: httpx.AsyncClient, **by_branch: dict[str, Any]) -> list[str]:
    vendor = await make_vendor(client, "Harborview Market", kind="chain", price_scope="chain")
    ids = []
    for name, at in (("Bayside", HOME_B), ("San Marisol", HOME_A), ("Midtown", MID)):
        made = await make_location(
            client, f"Harborview {name}", at, vendor_id=vendor["id"], **by_branch.get(name, {})
        )
        ids.append(made["id"])
    return ids


async def match(db_session, **kw: Any) -> dict[str, Any]:
    import uuid

    found = await header.match_location(
        db_session,
        document_id=uuid.uuid4(),
        receipt_text=kw.pop("receipt_text", "KELP 1.00"),
        merchant_name=kw.pop("merchant_name", None),
        store_identifier=None,
        **kw,
    )
    return found.as_json()


# --- reading the numbers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("TEL (555) 555-0142", ["(555) 555-0142"]),
        ("555.555.0142 / 555-555-0107", ["555.555.0142", "555-555-0107"]),
        ("+1 555 555 0142", ["+1 555 555 0142"]),
        ("CARD ************1234  AUTH 0042", []),  # pii-scan: allow invented card tail
        ("11/10/26 10:42 TRANS 5550142", []),
        ("PHONE 555-555-0142 AGAIN 555-555-0142", ["555-555-0142"]),
    ],
)
def test_phones_in_text(text: str, found: list[str]):
    assert header.phones_in_text(text) == found


# --- matching ---------------------------------------------------------------------------


async def test_a_phone_in_the_second_text_places_the_branch(admin_client, db_session):
    _, marisol, _ = await branches(admin_client, **{"San Marisol": {"phone": "555-555-0142"}})
    location = await match(db_session, second_text=SECOND_TEXT)
    assert location["matched"] is True and location["vendor_location_id"] == marisol
    evidence = location["candidates"][0]["evidence"]
    assert evidence["phone"] == "555-555-0142" and evidence["from_second_text"] == ["phone"]


async def test_the_models_phone_comes_first(admin_client, db_session):
    bayside, _, _ = await branches(
        admin_client,
        Bayside={"phone": "555-555-0107"},
        **{"San Marisol": {"phone": "555-555-0142"}},
    )
    location = await match(db_session, phone_text="555-555-0107", second_text=SECOND_TEXT)
    assert location["vendor_location_id"] == bayside
    assert "from_second_text" not in location["candidates"][0]["evidence"]


async def test_a_shared_number_in_the_second_text_decides_nothing(admin_client, db_session):
    await branches(
        admin_client,
        Bayside={"phone": "555-555-0142"},
        **{"San Marisol": {"phone": "+1 555 555 0142"}},
    )
    location = await match(db_session, second_text=SECOND_TEXT)
    assert location["matched"] is False
    assert all(c["evidence"]["phone"] is None for c in location["candidates"])


async def test_a_store_number_in_the_second_text_places_the_branch(admin_client, db_session):
    _, _, midtown = await branches(admin_client, Midtown={"receipt_identifiers": ["0412"]})
    location = await match(db_session, second_text=SECOND_TEXT)
    assert location["vendor_location_id"] == midtown
    evidence = location["candidates"][0]["evidence"]
    assert evidence["identifier"] == "0412" and evidence["from_second_text"] == ["identifier"]


async def test_without_a_second_text_nothing_changes(admin_client, db_session):
    await branches(admin_client, **{"San Marisol": {"phone": "555-555-0142"}})
    assert (await match(db_session))["candidates"] == []


# --- through the pipeline -------------------------------------------------------------------


async def test_the_header_stage_reads_the_second_text(
    admin_client: httpx.AsyncClient, receipts_dir: Path, transcriber, ocr_registry
):
    fixture = load_fixture("independent_minimal")
    _, marisol, _ = await branches(admin_client, **{"San Marisol": {"phone": "555-555-0142"}})
    transcriber(fixture, _transcript(fixture.ocr_text))
    ocr_registry["tesseract"] = SpyAdapter("tesseract", text=SECOND_TEXT).factory()
    _, job = await upload_fixture(admin_client, fixture, client_ocr=False)

    final = await run_job(job["id"], ocr_adapters=VISION)

    assert final.stage == "review"
    outputs = await stage_outputs(admin_client, job["id"])
    assert outputs["ocr"]["second_text"]["text"] == SECOND_TEXT
    candidates = outputs["header"]["location"]["candidates"]
    placed = [c for c in candidates if c["vendor_location_id"] == marisol]
    assert placed and placed[0]["evidence"]["from_second_text"] == ["phone"]


async def test_a_second_reader_that_declines_leaves_no_second_text(
    admin_client: httpx.AsyncClient, receipts_dir: Path, transcriber, ocr_registry
):
    fixture = load_fixture("independent_minimal")
    transcriber(fixture, _transcript(fixture.ocr_text))
    ocr_registry["tesseract"] = SpyAdapter("tesseract", text=None).factory()
    _, job = await upload_fixture(admin_client, fixture, client_ocr=False)

    final = await run_job(job["id"], ocr_adapters=VISION)

    assert final.stage == "review"
    assert "second_text" not in (await stage_outputs(admin_client, job["id"]))["ocr"]


async def test_no_second_text_when_tesseract_is_the_reader(
    admin_client: httpx.AsyncClient, receipts_dir: Path, ocr_registry, transcriber
):
    fixture = load_fixture("independent_minimal")
    _, tesseract = transcriber(fixture, _transcript(fixture.ocr_text))
    _, job = await upload_fixture(admin_client, fixture, client_ocr=False)

    final = await run_job(job["id"], ocr_adapters=["client", "tesseract"])

    assert final.stage == "review" and len(tesseract.calls) == 1
    assert "second_text" not in (await stage_outputs(admin_client, job["id"]))["ocr"]
