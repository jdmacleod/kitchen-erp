"""Lines read from the second transcript when the vision transcript gives none (04, 2O; #490).

On a till that prints two price columns the transcriber can lose the amounts,
and the reader then finds no items. The lines are read again from the
Tesseract text kept beside it, and every check reads the text they came from.
Every receipt here is a synthetic fixture; the transcriber's reply is scripted.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.ingest.stages import lines_text
from app.models import IngestJob, Purchase
from app.services import reread, row_boxes
from tests import ingest_helpers as ih
from tests.ingest_helpers import SpyAdapter, load_fixture, run_job, stage_outputs, upload_fixture
from tests.test_ingest_ocr_vision import VISION, _transcript, transcriber

receipts_dir = ih.receipts_dir
ocr_registry = ih.ocr_registry
__all__ = ["transcriber"]

# independent_minimal as a two-column till's transcript might come out: the names
# kept, every amount lost.
NAMES_ONLY = (
    "Rosalind's Corner Grocery\nThank you!\n3/22/2026 4:05 PM\nSourdough loaf\n"
    "Local honey 12oz\nButter 1lb\nApples\nBag fee\nTotal\nCash\nChange\n"
)
NO_LINES: dict[str, Any] = {"lines": []}


def _fixture(*lines_replies: Any) -> SimpleNamespace:
    """independent_minimal, with the lines reader's replies in order."""
    fixture = load_fixture("independent_minimal")
    responses = {**fixture.llm_responses, "lines": list(lines_replies)}
    return SimpleNamespace(
        name=fixture.name, ocr_text=fixture.ocr_text, llm_responses=responses, real=fixture
    )


async def _read(
    client: httpx.AsyncClient, transcriber, fixture: SimpleNamespace
) -> tuple[dict[str, Any], list[str]]:
    transcriber(fixture, _transcript(NAMES_ONLY))
    _, job = await upload_fixture(client, fixture.real, client_ocr=False)
    final = await run_job(job["id"], ocr_adapters=VISION)
    assert final.stage == "review"
    outputs = await stage_outputs(client, job["id"])
    async with get_sessionmaker()() as db:
        purchase = await db.get(Purchase, final.purchase_id)
        assert purchase is not None
        flags = list(purchase.flags)
    return outputs, flags


async def test_the_lines_come_from_the_second_text(
    admin_client: httpx.AsyncClient, receipts_dir: Path, transcriber
):
    fixture = _fixture(NO_LINES, load_fixture("independent_minimal").llm_responses["lines"])
    outputs, flags = await _read(admin_client, transcriber, fixture)

    lines = outputs["lines"]
    assert lines["text_source"] == "second_text" and lines["line_count"] == 5
    assert "read_fallback" in flags
    # The checks read the text the lines came from: every amount is in it.
    assert "total_not_in_scan" not in flags and "lines_unparsed" not in flags
    assert not any("not_in_scan" in line["flags"] for line in lines["lines"])


async def test_without_a_second_text_the_receipt_has_no_lines(
    admin_client: httpx.AsyncClient, receipts_dir: Path, transcriber, ocr_registry
):
    fixture = _fixture(NO_LINES)
    transcriber(fixture, _transcript(NAMES_ONLY))
    ocr_registry["tesseract"] = SpyAdapter("tesseract", text=None).factory()
    _, job = await upload_fixture(admin_client, fixture.real, client_ocr=False)
    final = await run_job(job["id"], ocr_adapters=VISION)

    lines = (await stage_outputs(admin_client, job["id"]))["lines"]
    assert final.stage == "review" and lines["line_count"] == 0
    assert "text_source" not in lines


async def test_when_the_second_text_gives_none_either_the_first_reading_stands(
    admin_client: httpx.AsyncClient, receipts_dir: Path, transcriber
):
    outputs, flags = await _read(admin_client, transcriber, _fixture(NO_LINES, NO_LINES))
    assert outputs["lines"]["line_count"] == 0 and "text_source" not in outputs["lines"]
    assert "read_fallback" not in flags


async def test_a_recheck_reads_against_the_second_text(
    admin_client: httpx.AsyncClient, receipts_dir: Path, transcriber
):
    fixture = _fixture(NO_LINES, load_fixture("independent_minimal").llm_responses["lines"])
    outputs, _ = await _read(admin_client, transcriber, fixture)
    purchase_id = outputs["lines"]["purchase_id"]

    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "check"})

    assert r.status_code == 201, r.text
    proposal = r.json()["proposal"]
    assert r.json()["status"] == "no_changes"
    assert "read_fallback" in proposal["after"]["flags"]
    assert "total_not_in_scan" not in proposal["after"]["flags"]


def test_lines_text():
    ocr = {"text": "FIRST", "second_text": {"adapter": "tesseract", "text": "SECOND"}}
    assert lines_text(ocr, {"text_source": "second_text"}) == "SECOND"
    assert lines_text(ocr, {}) == "FIRST"
    assert lines_text(ocr, None) == "FIRST"
    assert lines_text({"text": "FIRST"}, {"text_source": "second_text"}) == "FIRST"
    assert lines_text(None, None) is None


async def test_read_again_from_the_transcript_moves_the_reading_back(
    admin_client: httpx.AsyncClient, receipts_dir: Path, transcriber
):
    lines = load_fixture("independent_minimal").llm_responses["lines"]
    # The first reading fell back; Read again finds the same lines in the transcript.
    outputs, _ = await _read(admin_client, transcriber, _fixture(NO_LINES, lines, lines))
    purchase_id = outputs["lines"]["purchase_id"]
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "read"})
    async with get_sessionmaker()() as db:
        assert await reread.run_read_once(db, locked_by="test")

    got = (await admin_client.get(f"/api/v1/rereads/{r.json()['id']}")).json()
    assert got["status"] == "ready"  # the same lines, from the other text
    assert "read_fallback" not in got["proposal"]["after"]["flags"]
    applied = await admin_client.post(f"/api/v1/rereads/{got['id']}/apply")
    assert applied.status_code == 200, applied.text
    async with get_sessionmaker()() as db:
        purchase = await db.get(Purchase, uuid.UUID(purchase_id))
        assert purchase is not None and "read_fallback" not in purchase.flags
        job_id = await db.scalar(select(IngestJob.id).where(IngestJob.purchase_id == purchase.id))
    assert "text_source" not in (await stage_outputs(admin_client, str(job_id)))["lines"]


async def test_boxes_found_against_the_other_text_are_found_again(
    admin_client: httpx.AsyncClient, receipts_dir: Path, transcriber, owner_conn
):
    lines = load_fixture("independent_minimal").llm_responses["lines"]
    outputs, _ = await _read(admin_client, transcriber, _fixture(NO_LINES, lines))
    async with get_sessionmaker()() as db:
        job_id = await db.scalar(
            select(IngestJob.id).where(
                IngestJob.purchase_id == uuid.UUID(outputs["lines"]["purchase_id"])
            )
        )
        assert job_id is not None
        assert job_id in await row_boxes.jobs_needing_boxes(db, limit=10)
    ocr_id = await owner_conn.fetchval(
        "SELECT id FROM ingest_stage_result WHERE job_id = $1 AND stage = 'ocr'", job_id
    )
    for source in ("text", "second_text"):
        await owner_conn.execute(
            "INSERT INTO ingest_stage_result (id, job_id, stage, adapter, adapter_version, "
            "output, duration_ms) VALUES ($1, $2, 'row_boxes', 'tesseract', '1', $3::jsonb, 0)",
            uuid.uuid4(),
            job_id,
            json.dumps({"ocr_result_id": str(ocr_id), "text_source": source, "rows": []}),
        )
        async with get_sessionmaker()() as db:
            needing = await row_boxes.jobs_needing_boxes(db, limit=10)
        # Boxes against the transcript's rows don't serve lines read from the second text.
        assert (job_id in needing) is (source == "text")
