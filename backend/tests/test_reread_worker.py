"""Read again through the worker, the inbox, and `kerp receipts reread` (04, 2S).

Criteria 110 and 115. The fixture and its older reading are test_reread's.
Every vendor, product and wording is invented.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest

from app.core.db import get_sessionmaker
from app.ingest import llm
from app.ingest.replay import RecordedTransport
from app.services import reread
from tests import ingest_helpers as ih
from tests.conftest import ADMIN, BACKEND_ROOT
from tests.ingest_helpers import load_fixture, run_job, stage_outputs, upload_fixture
from tests.test_reread import _first_reading, _live, _purchase

receipts_dir = ih.receipts_dir
recorded = ih.recorded
CLI = "from app.cli import cli; cli()"


async def _forget_answer(owner_conn, job_id: str) -> None:
    """Make the latest reading one from before 2S: no read_lines."""
    await owner_conn.execute(
        "INSERT INTO ingest_stage_result (id, job_id, stage, adapter, adapter_version, output, "
        "duration_ms) SELECT $1, job_id, stage, adapter, adapter_version, "
        "output - 'read_lines', duration_ms FROM ingest_stage_result "
        "WHERE job_id = $2 AND stage = 'lines' ORDER BY created_at DESC LIMIT 1",
        uuid.uuid4(),
        uuid.UUID(job_id),
    )


async def _work(**kw) -> bool:
    async with get_sessionmaker()() as db:
        return await reread.run_read_once(db, locked_by="test", **kw)


async def test_read_again_answers_an_older_reading_through_the_reader(
    admin_client, receipts_dir, recorded, monkeypatch, owner_conn
):
    purchase_id, job_id, transport = await _first_reading(admin_client, recorded, monkeypatch)
    await _forget_answer(owner_conn, job_id)
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "read"})
    assert r.status_code == 201, r.text
    reread_id = r.json()["id"]
    inbox = (await admin_client.get("/api/v1/inbox")).json()
    assert inbox["reading"]["count"] == 1  # read again counts as being read

    calls = len(transport.requests)
    assert await _work()
    assert [x["stage"] for x in transport.requests[calls:]] == ["lines"]  # 110: the reader

    got = (await admin_client.get(f"/api/v1/rereads/{reread_id}")).json()
    assert got["status"] == "ready"
    assert got["proposal"]["after"]["reconciliation"]["mismatch"] is False
    inbox = (await admin_client.get("/api/v1/inbox")).json()
    rows = [x for x in inbox["items"] if x["kind"] == "reread"]
    assert [x["title"] for x in rows] == ["1 re-read receipt to review"]
    assert rows[0]["action_route"] == f"/shop/purchases/{purchase_id}/reread"
    assert inbox["reading"]["count"] == 0

    r = await admin_client.post(f"/api/v1/rereads/{reread_id}/apply")
    assert r.status_code == 200, r.text
    assert len(_live(await _purchase(admin_client, purchase_id))) == 4
    # The applied reading keeps the answer, so the next re-read can be a re-check.
    outputs = await stage_outputs(admin_client, job_id)
    assert outputs["lines"]["read_lines"]["lines"]
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "check"})
    assert r.json()["status"] == "no_changes"
    assert await _work() is False  # nothing left to read


async def test_an_unreachable_model_leaves_read_again_waiting(
    admin_client, receipts_dir, recorded, monkeypatch
):
    purchase_id, _, _ = await _first_reading(admin_client, recorded, monkeypatch)
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "read"})
    reread_id = r.json()["id"]
    monkeypatch.setattr(llm, "http_transport", None)  # nothing listens (receipts_dir)
    for _ in range(4):
        assert await _work()
        async with get_sessionmaker()() as db:
            row = await db.get(reread.LineReread, uuid.UUID(reread_id))
            assert row is not None
            assert row.status == "pending"  # 110: never failed for it
            row.next_attempt_at = None
            await db.commit()


async def test_an_answer_that_never_reads_fails_with_the_reason(
    admin_client, receipts_dir, recorded, monkeypatch
):
    purchase_id, _, _ = await _first_reading(admin_client, recorded, monkeypatch)
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/rereads", json={"mode": "read"})
    reread_id = r.json()["id"]
    monkeypatch.setattr(llm, "http_transport", RecordedTransport({"lines": "not json"}))
    assert await _work()
    got = (await admin_client.get(f"/api/v1/rereads/{reread_id}")).json()
    assert got["status"] == "failed"
    assert got["reason"] == "invalid_model_output"


async def test_kerp_receipts_reread_proposes_only_what_would_change(
    admin_client, receipts_dir, recorded, monkeypatch, owner_conn
):
    # One receipt read by an older pipeline, which would change.
    older, _, _ = await _first_reading(admin_client, recorded, monkeypatch)
    # One read as today reads it: re-checking it changes nothing.
    current = load_fixture("supermarket_loyalty")
    recorded(current)
    _, job = await upload_fixture(admin_client, current)
    await run_job(job["id"])
    # And one read before its answer was kept.
    third = load_fixture("independent_minimal")
    recorded(third)
    _, kept_job = await upload_fixture(admin_client, third)
    await run_job(kept_job["id"])
    await _forget_answer(owner_conn, kept_job["id"])

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", CLI, "receipts", "reread", "--as", ADMIN["email"]],
        cwd=BACKEND_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout
    assert "1 receipt(s) would change" in out, out
    assert "1 already read as today's passes read them." in out, out
    assert "1 skipped: read before the reader's answer was kept." in out, out
    rows = await owner_conn.fetch("SELECT purchase_id, status FROM line_reread")
    assert {(str(r["purchase_id"]), r["status"]) for r in rows if r["status"] == "ready"} == {
        (older, "ready")
    }
    inbox = (await admin_client.get("/api/v1/inbox")).json()
    assert [x["title"] for x in inbox["items"] if x["kind"] == "reread"] == [
        "1 re-read receipt to review"
    ]


@pytest.mark.parametrize("name", ih.fixture_names())
async def test_rechecking_a_fresh_reading_changes_nothing(
    name, admin_client, receipts_dir, recorded
):
    """Today's reading, re-checked by today's passes, is already what they make."""
    fixture = load_fixture(name)
    recorded(fixture)
    _, job = await upload_fixture(admin_client, fixture)
    await run_job(job["id"])
    outputs = await stage_outputs(admin_client, job["id"])
    if not outputs["lines"].get("parsed"):
        pytest.skip("the fixture's lines do not parse")
    r = await admin_client.post(
        f"/api/v1/purchases/{outputs['lines']['purchase_id']}/rereads", json={"mode": "check"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "no_changes", r.json()["proposal"]["changes"]
