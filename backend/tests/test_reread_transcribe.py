"""Transcribe again: read the stored receipt image again, as a proposal (04, 2S; #491).

A transcript that was cut short leaves a draft with too few lines, and Read
again only reads that same text. Transcribe again runs the OCR stage's
adapters on the stored image, then the header and lines, and nothing changes
until the proposal is applied; the header fills only what the purchase lacks.
Every receipt here is a synthetic fixture and the transcriber's replies are
scripted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.services import reread
from tests import ingest_helpers as ih
from tests.conftest import ADMIN, BACKEND_ROOT
from tests.geo_helpers import HOME_A, make_location
from tests.ingest_helpers import load_fixture, run_job, stage_outputs, upload_fixture
from tests.pricebook_helpers import make_product
from tests.test_ingest_ocr_vision import VISION, _transcript, transcriber
from tests.test_reread import _live, _purchase

receipts_dir = ih.receipts_dir
ocr_registry = ih.ocr_registry
__all__ = ["transcriber"]
CLI = "from app.cli import cli; cli()"

FULL = load_fixture("independent_minimal")
# The first transcript stopped after two items; it never reached the total.
SHORT = "\n".join(FULL.ocr_text.splitlines()[:5]) + "\n"


def _replies(*texts: str) -> Callable[[], httpx.Response]:
    """The transcriber's replies in order; the last repeats."""
    queue = [_transcript(t) for t in texts]

    def reply() -> httpx.Response:
        return (queue.pop(0) if len(queue) > 1 else queue[0])()

    return reply


def _fixture() -> SimpleNamespace:
    header = FULL.llm_responses["header"]
    lines = FULL.llm_responses["lines"]["lines"]
    responses = {
        **FULL.llm_responses,
        "header": [{**header, "total": None}, header],
        "lines": [{"lines": lines[:2]}, {"lines": lines}],
    }
    return SimpleNamespace(name=FULL.name, ocr_text=FULL.ocr_text, llm_responses=responses)


@pytest.fixture
def vision_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ocr_adapters", VISION)


async def _short_draft(client: httpx.AsyncClient, transcriber) -> tuple[str, str]:
    transcriber(_fixture(), _replies(SHORT, FULL.ocr_text))
    _, job = await upload_fixture(client, FULL, client_ocr=False)
    final = await run_job(job["id"], ocr_adapters=VISION)
    assert final.stage == "review"
    return str(final.purchase_id), job["id"]


async def _work() -> bool:
    async with get_sessionmaker()() as db:
        return await reread.run_read_once(db, locked_by="test")


async def _ocr_results(owner_conn, job_id: str) -> int:
    return await owner_conn.fetchval(
        "SELECT count(*) FROM ingest_stage_result WHERE job_id = $1 AND stage = 'ocr'",
        uuid.UUID(job_id),
    )


async def test_transcribe_again_proposes_the_full_receipt_and_applies_it(
    admin_client, receipts_dir, transcriber, vision_configured, owner_conn
):
    purchase_id, job_id = await _short_draft(admin_client, transcriber)
    before = await _purchase(admin_client, purchase_id)
    assert len(_live(before)) == 2 and "total_missing" in before["flags"]

    r = await admin_client.post(
        f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "transcribe"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"
    assert await _work()

    got = (await admin_client.get(f"/api/v1/rereads/{r.json()['id']}")).json()
    assert got["status"] == "ready" and got["mode"] == "transcribe"
    proposal = got["proposal"]
    assert proposal["counts"]["added"] == 3
    assert proposal["fill"]["total"] == "24.41"
    assert proposal["after"]["reconciliation"]["mismatch"] is False
    # Nothing is recorded until it is applied.
    assert await _ocr_results(owner_conn, job_id) == 1
    assert (await stage_outputs(admin_client, job_id))["ocr"]["text"] == SHORT

    r = await admin_client.post(f"/api/v1/rereads/{got['id']}/apply")
    assert r.status_code == 200, r.text
    after = await _purchase(admin_client, purchase_id)
    assert len(_live(after)) == 5
    assert Decimal(after["total"]) == Decimal("24.41")
    assert "total_missing" not in after["flags"]
    assert await _ocr_results(owner_conn, job_id) == 2
    outputs = await stage_outputs(admin_client, job_id)
    assert outputs["ocr"]["text"] == FULL.ocr_text
    assert outputs["lines"]["mode"] == "transcribe"


async def test_a_new_transcript_since_makes_it_stale(
    admin_client, receipts_dir, transcriber, vision_configured, owner_conn
):
    purchase_id, job_id = await _short_draft(admin_client, transcriber)
    r = await admin_client.post(
        f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "transcribe"}
    )
    assert await _work()
    await owner_conn.execute(
        "INSERT INTO ingest_stage_result (id, job_id, stage, adapter, adapter_version, output, "
        "duration_ms) SELECT $1, job_id, stage, adapter, adapter_version, output, duration_ms "
        "FROM ingest_stage_result "
        "WHERE job_id = $2 AND stage = 'ocr' LIMIT 1",
        uuid.uuid4(),
        uuid.UUID(job_id),
    )

    r = await admin_client.post(f"/api/v1/rereads/{r.json()['id']}/apply")

    assert r.status_code == 409 and r.json()["error"]["code"] == "reread_stale"


def _purchase_like(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"vendor_location_id": None, "flags": [], "tax": None, **kw})


def test_the_header_fills_only_what_is_missing():
    location = str(uuid.uuid4())
    header = {
        "purchased_at": "2026-03-22T23:05:00+00:00",
        "total": "24.41",
        "tax": "0.00",
        "location": {
            "vendor_location_id": location,
            "candidates": [
                {
                    "vendor_location_id": location,
                    "vendor_name": "Rosalind's",
                    "location_name": "Corner",
                }
            ],
        },
    }
    missing = _purchase_like(flags=["total_missing", "purchased_at_missing"])
    assert reread.header_fill(missing, header) == {
        "vendor_location_id": location,
        "location_name": "Rosalind's, Corner",
        "purchased_at": "2026-03-22T23:05:00+00:00",
        "total": "24.41",
        "tax": "0.00",
    }
    alone = {**header["location"]["candidates"][0], "location_name": "Rosalind's"}
    named_once = {**header, "location": {**header["location"], "candidates": [alone]}}
    assert reread.header_fill(missing, named_once)["location_name"] == "Rosalind's"
    complete = _purchase_like(vendor_location_id=uuid.uuid4(), tax="1.00")
    assert reread.header_fill(complete, header) == {}


async def test_kerp_receipts_retranscribe_queues_one(
    admin_client, receipts_dir, transcriber, vision_configured, owner_conn
):
    purchase_id, _ = await _short_draft(admin_client, transcriber)
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            CLI,
            "receipts",
            "retranscribe",
            purchase_id,
            "--as",
            ADMIN["email"],
        ],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.startswith("Queued:"), result.stdout
    rows = await owner_conn.fetch("SELECT mode, status FROM line_reread")
    assert [(r["mode"], r["status"]) for r in rows] == [("transcribe", "pending")]


async def _transcribed(client: httpx.AsyncClient, purchase_id: str) -> dict[str, Any]:
    r = await client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "transcribe"})
    assert r.status_code == 201, r.text
    assert await _work()
    return (await client.get(f"/api/v1/rereads/{r.json()['id']}")).json()


async def test_a_detail_set_since_is_never_overwritten(
    admin_client, receipts_dir, transcriber, vision_configured
):
    purchase_id, _ = await _short_draft(admin_client, transcriber)
    got = await _transcribed(admin_client, purchase_id)
    assert got["proposal"]["fill"]["total"] == "24.41"
    # Someone types the total in before applying.
    r = await admin_client.patch(f"/api/v1/purchases/{purchase_id}", json={"total": "24.40"})
    assert r.status_code == 200, r.text

    r = await admin_client.post(f"/api/v1/rereads/{got['id']}/apply")

    assert r.status_code == 409 and r.json()["error"]["code"] == "reread_stale"
    assert Decimal((await _purchase(admin_client, purchase_id))["total"]) == Decimal("24.40")


async def test_a_new_transcript_with_the_same_lines_can_be_kept(
    admin_client, receipts_dir, transcriber, vision_configured, owner_conn
):
    lines = FULL.llm_responses["lines"]["lines"]
    fixture = SimpleNamespace(
        name=FULL.name,
        ocr_text=FULL.ocr_text,
        llm_responses={**FULL.llm_responses, "lines": {"lines": lines}},
    )
    garbled = FULL.ocr_text.replace("Rosalind's", "R0sal1nd's")
    transcriber(fixture, _replies(garbled, FULL.ocr_text))
    _, job = await upload_fixture(admin_client, FULL, client_ocr=False)
    final = await run_job(job["id"], ocr_adapters=VISION)

    got = await _transcribed(admin_client, str(final.purchase_id))

    assert got["status"] == "ready"
    assert got["proposal"]["counts"] == {"changed": 0, "added": 0, "removed": 0, "kept": 0}
    r = await admin_client.post(f"/api/v1/rereads/{got['id']}/apply")
    assert r.status_code == 200, r.text
    assert (await stage_outputs(admin_client, job["id"]))["ocr"]["text"] == FULL.ocr_text


async def test_a_filled_store_teaches_the_new_lines_its_aliases(
    admin_client, receipts_dir, transcriber, vision_configured, owner_conn
):
    location = await make_location(admin_client, "Rosalind's Corner Grocery", HOME_A)
    butter = await make_product(admin_client, "Butter", "Butter")
    vendor = await owner_conn.fetchval(
        "SELECT vendor_id FROM vendor_location WHERE id = $1", uuid.UUID(location["id"])
    )
    await owner_conn.execute(
        "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, product_id, "
        "confirmed_count, last_seen_at, created_at) VALUES ($1, $2, 'BUTTER 1LB', 'product', "
        "$3, 2, now(), now())",
        uuid.uuid4(),
        vendor,
        uuid.UUID(butter["id"]),
    )
    # The first reading had no header at all, so no store.
    header = FULL.llm_responses["header"]
    lines = FULL.llm_responses["lines"]["lines"]
    fixture = SimpleNamespace(
        name=FULL.name,
        ocr_text=FULL.ocr_text,
        llm_responses={
            **FULL.llm_responses,
            "header": [{**header, "merchant_name": None, "total": None}, header],
            "lines": [{"lines": lines[:2]}, {"lines": lines}],
        },
    )
    transcriber(fixture, _replies("Sourdough loaf 6.50\nLocal honey 12oz 9.00\n", FULL.ocr_text))
    _, job = await upload_fixture(admin_client, FULL, client_ocr=False)
    final = await run_job(job["id"], ocr_adapters=VISION)
    purchase_id = str(final.purchase_id)
    assert (await _purchase(admin_client, purchase_id))["vendor_location"] is None

    got = await _transcribed(admin_client, purchase_id)
    assert got["proposal"]["fill"]["vendor_location_id"] == location["id"]
    r = await admin_client.post(f"/api/v1/rereads/{got['id']}/apply")
    assert r.status_code == 200, r.text

    after = await _purchase(admin_client, purchase_id)
    assert after["vendor_location"]["id"] == location["id"]
    by_text = {line["raw_text"]: line for line in _live(after)}
    butter_line = by_text["Butter 1lb            5.75"]
    assert butter_line["resolution"] == "alias" and butter_line["product"]["id"] == butter["id"]
