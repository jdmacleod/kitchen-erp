"""Upload batches (issue 122, ruling F2): what a batch of receipts came to, and how
long reading will take. Invented vendors and invented amounts only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
from sqlalchemy import update

from app.core.db import get_sessionmaker
from app.core.ids import new_id
from app.models import IngestJob, IngestStageResult
from app.services.upload_batches import minutes_left
from tests import ingest_helpers as ih
from tests.conftest import run_alembic
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase

receipts_dir = ih.receipts_dir


async def open_batch(client: httpx.AsyncClient, files: int) -> str:
    r = await client.post("/api/v1/upload-batches", json={"file_count": files})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def send(client: httpx.AsyncClient, seed: str, batch_id: str | None = None) -> dict:
    r = await ih.upload(client, ih.png_bytes(seed), batch_id=batch_id)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def batches(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get("/api/v1/upload-batches")
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def set_job(job_id: str, **values) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(
            update(IngestJob).where(IngestJob.id == uuid.UUID(job_id)).values(**values)
        )
        await db.commit()


async def test_a_single_upload_is_a_batch_of_one(admin_client, receipts_dir: Path):
    body = await send(admin_client, "alone")
    [batch] = await batches(admin_client)
    assert batch["id"] == body["batch_id"]
    assert (batch["file_count"], batch["uploaded"], batch["new"], batch["reading"]) == (1, 1, 1, 1)
    [receipt] = batch["receipts"]
    assert receipt["job"]["id"] == body["job"]["id"]
    assert receipt["outcome"] == "new" and receipt["trust"] is None


async def test_a_batch_counts_new_and_already_seen(admin_client, receipts_dir: Path):
    await send(admin_client, "kelp-crackers")
    batch_id = await open_batch(admin_client, 4)
    for seed in ("plum-jam", "rye-loaf", "kelp-crackers"):
        assert (await send(admin_client, seed, batch_id))["batch_id"] == batch_id
    newest = (await batches(admin_client))[0]
    assert newest["id"] == batch_id
    assert newest["file_count"] == 4  # one of the four chosen never arrived
    assert (newest["uploaded"], newest["new"], newest["already_seen"]) == (3, 2, 1)
    # The earlier receipt is not read again, so it is not counted as reading.
    assert newest["reading"] == 2


async def test_the_same_file_twice_in_one_batch_counts_once(admin_client, receipts_dir: Path):
    batch_id = await open_batch(admin_client, 2)
    await send(admin_client, "fig-bar", batch_id)
    await send(admin_client, "fig-bar", batch_id)
    [batch] = await batches(admin_client)
    assert (batch["uploaded"], batch["new"], batch["already_seen"]) == (1, 1, 0)


async def test_an_unknown_batch_is_refused(admin_client, receipts_dir: Path):
    r = await ih.upload(admin_client, ih.png_bytes("stray"), batch_id=str(uuid.uuid4()))
    assert r.status_code == 404 and r.json()["error"]["code"] == "batch_not_found"


async def test_a_batch_must_hold_a_file(admin_client):
    r = await admin_client.post("/api/v1/upload-batches", json={"file_count": 0})
    assert r.status_code == 422


async def test_each_receipt_carries_its_trust(admin_client, admin, receipts_dir: Path):
    loc = await make_location(admin_client, "Gullwing Grocer", "Gullwing Grocer")
    batch_id = await open_batch(admin_client, 3)
    good = await send(admin_client, "adds-up", batch_id)
    off = await send(admin_client, "far-off", batch_id)
    failed = await send(admin_client, "unreadable", batch_id)
    lines = [{"raw_text": "MILLET 1KG", "line_total": "4.20"}]
    for body, total in ((good, "4.20"), (off, "9.80")):
        pid = await make_receipt_purchase(admin.id, loc["id"], lines, total=total)
        await set_job(body["job"]["id"], status="needs_review", purchase_id=uuid.UUID(pid))
    await set_job(failed["job"]["id"], status="failed", last_error="no_ocr_text")

    [batch] = await batches(admin_client)
    assert (batch["adds_up"], batch["check_lines"], batch["couldnt_read"]) == (1, 1, 1)
    assert batch["reading"] == 0
    by_job = {r["job"]["id"]: r for r in batch["receipts"]}
    assert by_job[good["job"]["id"]]["trust"] == "adds_up"
    held = by_job[off["job"]["id"]]
    assert held["trust"] == "check_lines" and held["held"] is True
    assert (held["store"], held["total"], held["lines_total"]) == (
        "Gullwing Grocer",
        "9.8000",
        "4.2000",
    )
    assert held["item_lines"] == 1
    assert held["gap"] == "5.6000"
    assert by_job[good["job"]["id"]]["gap"] is None
    assert by_job[failed["job"]["id"]]["trust"] == "couldnt_read"


async def test_a_revived_receipt_shows_its_new_upload_date(admin_client, receipts_dir: Path):
    first = await send(admin_client, "came-back")
    job_id = first["job"]["id"]
    earlier = datetime.now(UTC) - timedelta(days=9)
    await set_job(job_id, status="discarded", created_at=earlier, uploaded_at=earlier)

    again = await send(admin_client, "came-back")
    assert again["revived"] is True
    assert again["job"]["created_at"].startswith(earlier.date().isoformat())
    uploaded = datetime.fromisoformat(again["job"]["uploaded_at"])
    assert uploaded > datetime.now(UTC) - timedelta(minutes=5)
    newest = (await batches(admin_client))[0]
    assert newest["id"] == again["batch_id"]
    assert newest["revived"] == 1


def test_minutes_left_needs_history_and_never_reads_zero():
    assert minutes_left(3, []) is None
    assert minutes_left(0, [60.0]) is None
    assert minutes_left(1, [5.0]) == 1
    # The median, not the mean: one slow read does not set the pace.
    assert minutes_left(4, [60.0, 90.0, 600.0]) == 6


async def test_home_says_how_far_a_batch_has_got(admin_client, receipts_dir: Path):
    batch_id = await open_batch(admin_client, 3)
    bodies = [await send(admin_client, seed, batch_id) for seed in ("oat-a", "oat-b", "oat-c")]
    done = bodies[0]["job"]["id"]
    await set_job(done, status="needs_review")
    async with get_sessionmaker()() as db:
        for stage, ms in (("ocr", 30_000), ("lines", 90_000)):
            db.add(
                IngestStageResult(
                    id=new_id(),
                    job_id=uuid.UUID(done),
                    stage=stage,
                    adapter="test",
                    adapter_version="1",
                    output={},
                    duration_ms=ms,
                )
            )
        await db.commit()

    reading = (await admin_client.get("/api/v1/inbox")).json()["reading"]
    assert reading["count"] == 2
    assert (reading["batch_done"], reading["batch_of"]) == (1, 3)
    assert reading["minutes_left"] == 4  # two left at two minutes each


async def test_no_estimate_before_any_read_has_finished(admin_client, receipts_dir: Path):
    await send(admin_client, "first-ever")
    reading = (await admin_client.get("/api/v1/inbox")).json()["reading"]
    assert (reading["batch_done"], reading["batch_of"]) == (0, 1)
    assert reading["minutes_left"] is None


async def test_migration_groups_earlier_uploads_into_batches(owner_conn: asyncpg.Connection):
    from app.core.db import dispose_engine

    run_alembic("downgrade", "0027")
    await dispose_engine()
    try:
        user = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, active) "
            "VALUES ($1, 'batches@example.com', 'Batches', 'x', 'admin', true)",
            user,
        )
        start = datetime(2026, 9, 1, 9, tzinfo=UTC)
        # Two within five minutes of each other, then one an hour later.
        for i, offset in enumerate((0, 3, 63)):
            doc, job = uuid.uuid4(), uuid.uuid4()
            await owner_conn.execute(
                "INSERT INTO receipt_document (id, sha256, image_path, mime, bytes, uploaded_by) "
                "VALUES ($1, $2, 'x.png', 'image/png', 1, $3)",
                doc,
                f"{i:064d}",
                user,
            )
            await owner_conn.execute(
                "INSERT INTO ingest_job (id, receipt_document_id, stage, status, attempts, "
                "created_at, updated_at) VALUES ($1, $2, 'captured', 'pending', 0, $3, $3)",
                job,
                doc,
                start + timedelta(minutes=offset),
            )
        run_alembic("upgrade", "0028")
        sizes = sorted(
            r["n"]
            for r in await owner_conn.fetch(
                "SELECT count(*) AS n FROM upload_batch_receipt GROUP BY batch_id"
            )
        )
        assert sizes == [1, 2]
        assert await owner_conn.fetchval(
            "SELECT bool_and(uploaded_at = created_at) FROM ingest_job"
        )
        run_alembic("downgrade", "0027")
        assert not await owner_conn.fetchval("SELECT to_regclass('upload_batch') IS NOT NULL")
    finally:
        run_alembic("upgrade", "head")
        await dispose_engine()


async def test_a_receipt_row_and_a_purchase_carry_trust_and_gap(admin_client, admin):
    loc = await make_location(admin_client, "Gullwing Grocer", "Gullwing Grocer")
    lines = [{"raw_text": "BARLEY 500G", "line_total": "2.10"}]
    near = await make_receipt_purchase(admin.id, loc["id"], lines, total="2.40")
    exact = await make_receipt_purchase(admin.id, loc["id"], lines, total="2.10")
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    rows = {i["action_route"].rsplit("/", 1)[-1]: i for i in items if i["kind"] == "receipt"}
    assert (rows[near]["trust"], rows[near]["gap"]) == ("check_lines", "0.3000")
    assert (rows[exact]["trust"], rows[exact]["gap"]) == ("adds_up", None)
    purchase = (await admin_client.get(f"/api/v1/purchases/{near}")).json()
    assert (purchase["trust"], purchase["gap"], purchase["held"]) == (
        "check_lines",
        "0.3000",
        False,
    )
