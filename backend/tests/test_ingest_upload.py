"""Receipt upload and document endpoints (criteria 16 and the capture contract)."""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.models import IngestJob, ReceiptDocument
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.ingest_helpers import png_bytes, render_receipt_as, upload

no_network = gh.no_network
receipts_dir = ih.receipts_dir


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


async def _counts() -> tuple[int, int]:
    async with get_sessionmaker()() as db:
        docs = (await db.execute(select(func.count()).select_from(ReceiptDocument))).scalar_one()
        jobs = (await db.execute(select(func.count()).select_from(IngestJob))).scalar_one()
    return int(docs), int(jobs)


async def test_upload_creates_document_and_job(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
):
    data = png_bytes("first")
    r = await upload(
        admin_client,
        data,
        ocr_text="HELLO 1.00",
        captured_at=datetime(2026, 3, 4, 17, 42, tzinfo=UTC),
        lat="33.500000",
        lon="-120.500000",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    document, job = body["document"], body["job"]
    assert document["sha256"] == hashlib.sha256(data).hexdigest()
    assert document["mime"] == "image/png"
    assert document["bytes"] == len(data)
    assert document["has_client_ocr_text"] is True
    assert document["has_capture_geo"] is True
    assert document["captured_at"].startswith("2026-03-04T17:42:00")
    assert job["stage"] == "captured" and job["status"] == "pending"
    assert job["receipt_document_id"] == document["id"]
    assert job["attempts"] == 0 and job["purchase_id"] is None
    sha = document["sha256"]
    stored = receipts_dir / sha[:2] / sha[2:4] / f"{sha}.png"
    assert stored.read_bytes() == data


async def test_duplicate_upload_returns_same_rows(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
):
    data = png_bytes("dup")
    first = await upload(admin_client, data, ocr_text="A")
    assert first.status_code == 201
    second = await upload(admin_client, data, filename="other.png")
    assert second.status_code == 200, second.text
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    assert await _counts() == (1, 1)


async def test_idempotency_key_replays(admin_client: httpx.AsyncClient, receipts_dir: Path):
    headers = {"Idempotency-Key": "receipt-1"}
    data = png_bytes("idem")
    first = await upload(admin_client, data, headers=headers)
    second = await upload(admin_client, data, headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert first.json() == second.json()
    different = await upload(admin_client, png_bytes("other"), headers=headers)
    assert different.status_code == 422
    assert different.json()["error"]["code"] == "idempotency_key_reused"


async def test_mime_is_sniffed_not_trusted(admin_client: httpx.AsyncClient, receipts_dir: Path):
    r = await upload(admin_client, b"GIF89a not a receipt", content_type="image/png")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    r = await upload(admin_client, jpeg, content_type="text/plain", filename="x.txt")
    assert r.status_code == 201, r.text
    assert r.json()["document"]["mime"] == "image/jpeg"
    pdf = b"%PDF-1.4\n%synthetic\n"
    r = await upload(admin_client, pdf, content_type="application/octet-stream")
    assert r.status_code == 201 and r.json()["document"]["mime"] == "application/pdf"


async def test_size_limit(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(get_settings(), "receipt_max_bytes", 200)
    r = await upload(admin_client, png_bytes("big") + b"\x00" * 400)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
    assert await _counts() == (0, 0)


async def test_lat_without_lon_is_rejected(admin_client: httpx.AsyncClient, receipts_dir: Path):
    r = await upload(admin_client, png_bytes("half"), lat="33.5")
    assert r.status_code == 422
    r = await upload(admin_client, png_bytes("half"), lat="abc", lon="-120.5")
    assert r.status_code == 422


async def test_upload_requires_auth(client: httpx.AsyncClient, receipts_dir: Path):
    r = await upload(client, png_bytes("anon"))
    assert r.status_code == 401


async def test_image_download(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient, receipts_dir: Path
):
    data = png_bytes("download")
    document = (await upload(admin_client, data)).json()["document"]
    r = await admin_client.get(f"/api/v1/receipts/{document['id']}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == data
    meta = await admin_client.get(f"/api/v1/receipts/{document['id']}")
    assert meta.status_code == 200 and meta.json()["sha256"] == document["sha256"]
    missing = await admin_client.get(f"/api/v1/receipts/{uuid.uuid4()}/image")
    assert missing.status_code == 404


async def test_job_list_filters_by_status(admin_client: httpx.AsyncClient, receipts_dir: Path):
    for seed in ("a", "b", "c"):
        uploaded = await upload(admin_client, png_bytes(seed))
        assert uploaded.status_code == 201
    r = await admin_client.get("/api/v1/ingest-jobs", params={"status": "pending", "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2 and body["next_cursor"] is not None
    r2 = await admin_client.get(
        "/api/v1/ingest-jobs",
        params={"status": "pending", "limit": 2, "cursor": body["next_cursor"]},
    )
    assert len(r2.json()["items"]) == 1 and r2.json()["next_cursor"] is None
    assert (await admin_client.get("/api/v1/ingest-jobs", params={"status": "failed"})).json()[
        "items"
    ] == []


# --- showing the receipt (#30, #28) ----------------------------------------------


@pytest.mark.parametrize("mime", ["application/pdf", "image/heic"])
async def test_a_receipt_a_browser_cannot_show_is_served_as_png(
    admin_client: httpx.AsyncClient, receipts_dir: Path, tmp_path: Path, mime: str
):
    """A PDF, or HEIC outside Safari, in an <img> is a blank panel on review."""
    source = render_receipt_as(mime, "SYNTHETIC GROCER\nOATS 1KG  3.29", tmp_path / "receipt")
    document = (await upload(admin_client, source.read_bytes(), content_type=mime)).json()[
        "document"
    ]
    assert document["mime"] == mime

    r = await admin_client.get(f"/api/v1/receipts/{document['id']}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as shown:
        assert shown.format == "PNG" and shown.width > 100
    # The original is untouched; the rendering is not stored beside it.
    assert (await admin_client.get(f"/api/v1/receipts/{document['id']}")).json()[
        "sha256"
    ] == document["sha256"]


async def test_a_thumbnail_is_a_scaled_png(admin_client: httpx.AsyncClient, receipts_dir: Path):
    document = (await upload(admin_client, png_bytes("thumb"))).json()["document"]
    r = await admin_client.get(f"/api/v1/receipts/{document['id']}/image", params={"width": 48})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as thumb:
        assert thumb.width == 48 and thumb.height == 12  # 96x24 scaled by half
    too_big = await admin_client.get(
        f"/api/v1/receipts/{document['id']}/image", params={"width": 5000}
    )
    assert too_big.status_code == 422


async def test_a_pdf_that_cannot_be_rendered_says_so(
    admin_client: httpx.AsyncClient, receipts_dir: Path
):
    pdf = b"%PDF-1.4\n%synthetic, and not a document\n"
    document = (await upload(admin_client, pdf, content_type="application/pdf")).json()["document"]
    r = await admin_client.get(f"/api/v1/receipts/{document['id']}/image")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "image_unreadable"
