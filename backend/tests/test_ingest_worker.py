"""Worker claiming, backoff, model outages, retry, and conversion to manual (19, 25)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.ingest import llm
from app.ingest.stages import backoff_seconds, run_stage
from app.models import IngestJob, IngestStageResult, Purchase, PurchaseLine
from app.worker import claim_job, run_once
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.ingest_helpers import (
    SpyAdapter,
    get_job,
    load_fixture,
    load_job,
    run_job,
    stage_outputs,
    upload_fixture,
)

no_network = gh.no_network
receipts_dir = ih.receipts_dir
recorded = ih.recorded
ocr_registry = ih.ocr_registry


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


async def _set(job_id: str, **fields) -> None:
    async with get_sessionmaker()() as db:
        job = await db.get(IngestJob, uuid.UUID(job_id))
        assert job is not None
        for key, value in fields.items():
            setattr(job, key, value)
        await db.commit()


def test_backoff_schedule(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ingest_backoff_base_seconds", 5.0)
    monkeypatch.setattr(settings, "ingest_backoff_factor", 4.0)
    monkeypatch.setattr(settings, "ingest_backoff_max_seconds", 300.0)
    assert [backoff_seconds(n) for n in (1, 2, 3, 4, 5)] == [5.0, 20.0, 80.0, 300.0, 300.0]


async def test_run_once_claims_and_runs_one_stage(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    recorded,
):
    fixture = load_fixture("independent_minimal")
    recorded(fixture)
    _, job = await upload_fixture(admin_client, fixture)
    async with get_sessionmaker()() as db:
        assert await run_once(db, locked_by="test-worker") is True
        assert await run_once(db, locked_by="test-worker") is True  # ocr
        assert await run_once(db, locked_by="test-worker") is True  # header
        assert await run_once(db, locked_by="test-worker") is True  # lines
        assert await run_once(db, locked_by="test-worker") is True  # resolve
        assert await run_once(db, locked_by="test-worker") is False  # review: a person's turn
    final = await load_job(job["id"])
    assert (final.stage, final.status) == ("review", "needs_review")
    assert final.locked_by is None


async def test_claim_skips_locked_future_and_running_jobs(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
):
    fixture = load_fixture("independent_minimal")
    _, job = await upload_fixture(admin_client, fixture)
    # Held under FOR UPDATE by another session: SKIP LOCKED passes it by.
    async with get_sessionmaker()() as holder, get_sessionmaker()() as other:
        held = (
            await holder.execute(
                select(IngestJob).where(IngestJob.id == uuid.UUID(job["id"])).with_for_update()
            )
        ).scalar_one()
        assert held.status == "pending"
        assert await claim_job(other, "other") is None
        await holder.rollback()
    # A future next_attempt_at is not claimable; a past one is.
    await _set(job["id"], next_attempt_at=datetime.now(UTC) + timedelta(minutes=5))
    async with get_sessionmaker()() as db:
        assert await claim_job(db, "w") is None
    await _set(job["id"], next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
    async with get_sessionmaker()() as db:
        claimed = await claim_job(db, "w")
        assert claimed is not None and claimed.status == "running" and claimed.locked_by == "w"
        await db.rollback()
    # Running with a fresh lock: not claimable. Locked past the timeout: abandoned, claimable.
    async with get_sessionmaker()() as db:
        assert await claim_job(db, "w2") is None
    stale = datetime.now(UTC) - timedelta(seconds=get_settings().ingest_lock_timeout_seconds + 5)
    await _set(job["id"], locked_at=stale)
    async with get_sessionmaker()() as db:
        reclaimed = await claim_job(db, "w3")
        assert reclaimed is not None and reclaimed.locked_by == "w3"


async def test_model_unreachable_waits_with_backoff_and_resumes(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    recorded,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ingest_max_attempts", 3)
    monkeypatch.setattr(settings, "ingest_backoff_base_seconds", 5.0)
    monkeypatch.setattr(settings, "ingest_backoff_factor", 4.0)
    fixture = load_fixture("supermarket_loyalty")
    monkeypatch.setattr(llm, "http_transport", None)  # nothing listens on ollama_base_url
    _, job = await upload_fixture(admin_client, fixture)
    state = await run_job(job["id"])  # captured, ocr, then header stalls
    assert (state.stage, state.status) == ("header", "pending")
    assert state.attempts == 1 and state.last_error == "model_unavailable"
    assert state.next_attempt_at is not None
    wait = (state.next_attempt_at - datetime.now(UTC)).total_seconds()
    assert 2 < wait <= 5.5
    # Not claimable until the backoff elapses; the rest of the API is unaffected.
    async with get_sessionmaker()() as db:
        assert await run_once(db) is False
    health = await admin_client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["checks"]["model_server"]["status"] == "degraded"
    listed = await admin_client.get("/api/v1/ingest-jobs", params={"status": "pending"})
    assert [j["id"] for j in listed.json()["items"]] == [job["id"]]

    # Attempts past the limit never fail the job while the model is unreachable.
    for expected_attempts in (2, 3, 4):
        await _set(job["id"], next_attempt_at=None)
        state = await run_job(job["id"])
        assert (state.stage, state.status) == ("header", "pending")
        assert state.attempts == expected_attempts
    wait = (state.next_attempt_at - datetime.now(UTC)).total_seconds()
    assert wait > 60  # 5 * 4^3 = 320 capped at 300
    detail = await get_job(admin_client, job["id"], include_output=True)
    failures = [r for r in detail["stage_results"] if r["stage"] == "header"]
    assert len(failures) == 1  # latest per stage is shown ...
    async with get_sessionmaker()() as db:
        n = (
            await db.execute(
                text(
                    "SELECT count(*) FROM ingest_stage_result "
                    "WHERE job_id = :j AND stage = 'header'"
                ),
                {"j": uuid.UUID(job["id"])},
            )
        ).scalar_one()
    assert n == 4  # ... but every attempt is on record
    assert failures[0]["output"] == {"ok": False, "error": "model_unavailable", "retryable": True}

    # The model comes back: the job resumes from the header stage.
    recorded(fixture)
    await _set(job["id"], next_attempt_at=None)
    state = await run_job(job["id"])
    assert (state.stage, state.status) == ("review", "needs_review")
    assert state.attempts == 0 and state.last_error is None


async def test_stage_failure_retries_then_fails_and_can_be_retried(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    recorded,
    ocr_registry,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ingest_max_attempts", 2)
    monkeypatch.setattr(settings, "ingest_backoff_base_seconds", 0.0)
    fixture = load_fixture("discount_grocer_terse")
    recorded(fixture)
    _, job = await upload_fixture(admin_client, fixture, client_ocr=False)
    state = await run_job(job["id"], ocr_adapters=["client"])
    assert (state.stage, state.status) == ("ocr", "failed")
    assert state.attempts == 2 and state.last_error == "no_ocr_text"
    assert state.next_attempt_at is None
    outputs = await stage_outputs(admin_client, job["id"])
    assert outputs["ocr"] == {"ok": False, "error": "no_ocr_text", "retryable": False}

    not_failed = await admin_client.post(f"/api/v1/ingest-jobs/{uuid.uuid4()}/retry")
    assert not_failed.status_code == 404
    r = await admin_client.post(f"/api/v1/ingest-jobs/{job['id']}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending" and r.json()["stage"] == "ocr"
    assert r.json()["attempts"] == 0 and r.json()["last_error"] is None
    again = await admin_client.post(f"/api/v1/ingest-jobs/{job['id']}/retry")
    assert again.status_code == 409 and again.json()["error"]["code"] == "job_not_failed"

    spy = SpyAdapter("tesseract", text=fixture.ocr_text)
    ocr_registry["tesseract"] = spy.factory()
    state = await run_job(job["id"], ocr_adapters=["client", "tesseract"])
    assert (state.stage, state.status) == ("review", "needs_review")
    assert spy.calls


async def test_last_error_never_carries_receipt_text(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """An unexpected exception whose message quotes the receipt is reduced to a code."""
    from app.ingest import stages

    fixture = load_fixture("prompt_injection")

    async def boom(ctx):
        raise RuntimeError(f"could not parse: {ctx.receipt_text()}")

    monkeypatch.setitem(stages.STAGE_FUNCTIONS, "header", boom)
    _, job = await upload_fixture(admin_client, fixture)
    state = await run_job(job["id"])
    assert state.stage == "header" and state.status == "pending"
    assert state.last_error == "unexpected_error"
    outputs = await stage_outputs(admin_client, job["id"])
    assert (
        "IGNORE" not in str(outputs["header"]) and outputs["header"]["error"] == "unexpected_error"
    )


async def test_convert_failed_job_to_manual_keeps_document_link(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(get_settings(), "ingest_max_attempts", 1)
    fixture = load_fixture("warehouse_codes")
    document, job = await upload_fixture(admin_client, fixture, client_ocr=False)
    state = await run_job(job["id"], ocr_adapters=["client"])
    assert state.status == "failed"
    r = await admin_client.post(f"/api/v1/ingest-jobs/{job['id']}/to-manual")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"]["stage"] == "committed" and body["job"]["status"] == "done"
    assert body["job"]["purchase_id"] == body["purchase_id"]
    async with get_sessionmaker()() as db:
        purchase = await db.get(Purchase, uuid.UUID(body["purchase_id"]))
        assert purchase is not None
        assert purchase.source == "manual" and purchase.status == "draft"
        assert purchase.receipt_document_id == uuid.UUID(document["id"])
        assert purchase.vendor_location_id is None
        assert purchase.entered_by == uuid.UUID(document["uploaded_by"])
        lines = (await db.execute(select(PurchaseLine))).scalars().all()
    assert lines == []
    # Done jobs cannot be converted twice or retried.
    assert (
        await admin_client.post(f"/api/v1/ingest-jobs/{job['id']}/to-manual")
    ).status_code == 409
    assert (await admin_client.post(f"/api/v1/ingest-jobs/{job['id']}/retry")).status_code == 409
    async with get_sessionmaker()() as db:
        assert await run_once(db) is False  # nothing claimable at 'committed'


async def test_convert_reviewed_job_reuses_its_draft_purchase(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    recorded,
):
    fixture = load_fixture("supermarket_loyalty")
    recorded(fixture)
    document, job = await upload_fixture(admin_client, fixture)
    state = await run_job(job["id"])
    assert state.status == "needs_review" and state.purchase_id is not None
    r = await admin_client.post(f"/api/v1/ingest-jobs/{job['id']}/to-manual")
    assert r.status_code == 200, r.text
    assert r.json()["purchase_id"] == str(state.purchase_id)
    async with get_sessionmaker()() as db:
        purchases = (await db.execute(select(Purchase))).scalars().all()
        lines = (await db.execute(select(PurchaseLine))).scalars().all()
        results = (await db.execute(select(IngestStageResult))).scalars().all()
    assert len(purchases) == 1 and purchases[0].source == "manual"
    assert purchases[0].receipt_document_id == uuid.UUID(document["id"])
    assert purchases[0].total == Decimal("30.39")
    assert lines == []
    assert len(results) == 5  # the audit trail stays


async def test_run_stage_is_noop_at_review(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    recorded,
):
    fixture = load_fixture("independent_minimal")
    recorded(fixture)
    _, job = await upload_fixture(admin_client, fixture)
    await run_job(job["id"])
    async with get_sessionmaker()() as db:
        current = await db.get(IngestJob, uuid.UUID(job["id"]))
        assert current is not None
        assert await run_stage(db, current) is None
