"""Receipt photos are stored without their metadata (#221).

Every photo here is drawn at test time and given invented EXIF: a camera maker,
and GPS on the synthetic grid in the ocean (SECURITY.md), never a real place.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pillow_heif
import pytest
from PIL import Image, ImageDraw, ImageOps, PngImagePlugin
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.ids import new_id
from app.models import IngestJob, ReceiptDocument
from app.services import image_metadata, receipt_strip
from app.services.ingest import relative_path
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.ingest_helpers import upload

pillow_heif.register_heif_opener()

no_network = gh.no_network
receipts_dir = ih.receipts_dir
BACKEND_ROOT = Path(__file__).resolve().parent.parent

MAKER = "Inventco"  # an invented camera maker: its bytes must not survive
XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>'
)


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


def _picture(seed: str, size: tuple[int, int] = (160, 80)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((4, 4, 40, 20), fill="black")
    draw.text((50, 30), seed[:12], fill="black")
    return image


def _exif(orientation: int = 1) -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = MAKER
    exif[image_metadata.ORIENTATION] = orientation
    # Synthetic grid (SECURITY.md): ocean west of the coast, no home, no shop.
    exif[image_metadata.GPS_IFD] = {1: "N", 2: (33.0, 30.0, 0.0), 3: "W", 4: (120.0, 30.0, 0.0)}
    return exif


def _photo(fmt: str, seed: str = "receipt", orientation: int = 1) -> bytes:
    out = io.BytesIO()
    kw: dict = {"exif": _exif(orientation)}
    if fmt == "JPEG":
        kw.update(comment=b"invented comment", xmp=XMP, quality=90)
    elif fmt == "PNG":
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "invented comment")
        kw["pnginfo"] = info
    else:
        kw["xmp"] = XMP
    _picture(seed).save(out, format=fmt, **kw)
    return out.getvalue()


def _has_gps(data: bytes) -> bool:
    with Image.open(io.BytesIO(data)) as image:
        exif = image.getexif()
        return image_metadata.GPS_IFD in exif or bool(exif.get_ifd(image_metadata.GPS_IFD))


def _pixels(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGB").tobytes()


# --- the stripper -------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "mime"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp"), ("HEIF", "image/heic")],
)
def test_metadata_goes_and_the_picture_stays(fmt: str, mime: str):
    data = _photo(fmt)
    assert _has_gps(data) and MAKER.encode() in data
    result = image_metadata.strip(data, mime)
    assert not result.reencoded and result.mime == mime
    assert not _has_gps(result.data)
    assert MAKER.encode() not in result.data
    assert b"xmpmeta" not in result.data and b"invented comment" not in result.data
    # Not re-encoded: the decoded pixels are the uploaded ones, bit for bit.
    assert _pixels(result.data) == _pixels(data)


@pytest.mark.parametrize(("fmt", "mime"), [("JPEG", "image/jpeg"), ("PNG", "image/png")])
def test_a_sideways_photo_keeps_its_orientation(fmt: str, mime: str):
    data = _photo(fmt, orientation=6)
    result = image_metadata.strip(data, mime)
    with Image.open(io.BytesIO(result.data)) as image:
        assert dict(image.getexif()) == {image_metadata.ORIENTATION: 6}
        upright = ImageOps.exif_transpose(image).size
    assert upright == (80, 160)


def test_a_photo_with_nothing_to_strip_is_stored_as_it_came():
    out = io.BytesIO()
    _picture("plain").save(out, format="PNG")
    data = out.getvalue()
    assert image_metadata.strip(data, "image/png").data == data


def test_a_pdf_is_stored_as_it_came():
    data = b"%PDF-1.4\n% invented\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
    assert image_metadata.strip(data, "application/pdf").data == data


def test_a_photo_that_cannot_be_taken_apart_is_re_encoded_without_metadata(
    monkeypatch: pytest.MonkeyPatch,
):
    def malformed(data: bytes, orientation: int) -> bytes:
        raise image_metadata._Malformed("invented")

    monkeypatch.setitem(image_metadata._STRIPPERS, "image/png", malformed)
    data = _photo("PNG", orientation=6)
    result = image_metadata.strip(data, "image/png")
    assert result.reencoded and result.mime == "image/jpeg"
    assert not _has_gps(result.data) and MAKER.encode() not in result.data
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.size == (80, 160)  # turned upright in the pixels


def test_an_undecodable_photo_with_metadata_is_refused():
    exif = b"Exif\x00\x00" + MAKER.encode()
    data = b"\xff\xd8\xff\xe1" + len(exif).to_bytes(2, "big") + exif + b"\x00" * 32
    with pytest.raises(image_metadata.Unreadable):
        image_metadata.strip(data, "image/jpeg")


# --- upload -------------------------------------------------------------------


async def test_an_uploaded_photo_is_stored_and_served_without_its_gps(
    admin_client: httpx.AsyncClient, receipts_dir: Path
):
    data = _photo("JPEG", "upload")
    r = await upload(admin_client, data, filename="receipt.jpg", content_type="image/jpeg")
    assert r.status_code == 201, r.text
    document = r.json()["document"]
    stored = receipts_dir / relative_path(document["sha256"], "image/jpeg")
    assert not _has_gps(stored.read_bytes())
    assert MAKER.encode() not in stored.read_bytes()
    assert document["sha256"] == hashlib.sha256(stored.read_bytes()).hexdigest()
    assert document["sha256"] != hashlib.sha256(data).hexdigest()
    served = await admin_client.get(f"/api/v1/receipts/{document['id']}/image")
    assert served.status_code == 200 and not _has_gps(served.content)
    async with get_sessionmaker()() as db:
        row = await db.get(ReceiptDocument, document["id"])
        assert row is not None and row.upload_sha256 == hashlib.sha256(data).hexdigest()


async def test_the_same_photo_uploaded_again_is_still_caught(
    admin_client: httpx.AsyncClient, receipts_dir: Path
):
    data = _photo("JPEG", "twice")
    first = await upload(admin_client, data, filename="a.jpg", content_type="image/jpeg")
    second = await upload(admin_client, data, filename="b.jpg", content_type="image/jpeg")
    assert first.status_code == 201 and second.status_code == 200, second.text
    assert second.json()["document"]["id"] == first.json()["document"]["id"]


async def _stored_before(admin, receipts_dir: Path, data: bytes, mime: str, *, status="done"):
    """A receipt as stored before 0032: the uploaded bytes, under their own digest."""
    digest = hashlib.sha256(data).hexdigest()
    rel = relative_path(digest, mime)
    path = receipts_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    async with get_sessionmaker()() as db:
        document = ReceiptDocument(
            id=new_id(),
            sha256=digest,
            image_path=rel,
            mime=mime,
            bytes=len(data),
            uploaded_by=admin.id,
        )
        db.add(document)
        await db.flush()
        db.add(
            IngestJob(
                id=new_id(),
                receipt_document_id=document.id,
                stage="captured",
                status=status,
                attempts=0,
            )
        )
        await db.commit()
        return document.id, path


async def test_a_receipt_stored_before_this_is_caught_when_uploaded_again(
    admin_client: httpx.AsyncClient, admin, receipts_dir: Path
):
    data = _photo("JPEG", "older")
    doc_id, _ = await _stored_before(admin, receipts_dir, data, "image/jpeg")
    r = await upload(admin_client, data, filename="again.jpg", content_type="image/jpeg")
    assert r.status_code == 200, r.text
    assert r.json()["document"]["id"] == str(doc_id)


async def test_reviving_a_receipt_stored_before_this_stores_it_stripped(
    admin_client: httpx.AsyncClient, admin, receipts_dir: Path
):
    data = _photo("JPEG", "revive")
    doc_id, old_path = await _stored_before(
        admin, receipts_dir, data, "image/jpeg", status="discarded"
    )
    old_path.unlink()  # a discarded read's photo is deleted with it
    r = await upload(admin_client, data, filename="again.jpg", content_type="image/jpeg")
    assert r.status_code == 200, r.text
    assert r.json()["document"]["id"] == str(doc_id)
    async with get_sessionmaker()() as db:
        row = await db.get(ReceiptDocument, doc_id)
        assert row is not None
        assert row.upload_sha256 == hashlib.sha256(data).hexdigest()
        stored = receipts_dir / row.image_path
        assert row.sha256 == hashlib.sha256(stored.read_bytes()).hexdigest()
        assert not _has_gps(stored.read_bytes())


# --- the backfill -------------------------------------------------------------


async def test_the_backfill_strips_what_was_stored_before(admin, receipts_dir: Path):
    with_gps = _photo("JPEG", "backfill")
    plain = io.BytesIO()
    _picture("plain").save(plain, format="PNG")
    stripped_id, old_path = await _stored_before(admin, receipts_dir, with_gps, "image/jpeg")
    plain_id, _ = await _stored_before(admin, receipts_dir, plain.getvalue(), "image/png")
    gone_id, gone = await _stored_before(
        admin, receipts_dir, _photo("JPEG", "gone"), "image/jpeg", status="discarded"
    )
    gone.unlink()

    async with get_sessionmaker()() as db:
        planned = await receipt_strip.run(db, dry_run=True)
    assert planned.counts == {"stripped": 1, "unchanged": 1, "removed": 1}
    assert planned.changed == [str(stripped_id)]
    assert old_path.read_bytes() == with_gps  # a dry run changes nothing

    async with get_sessionmaker()() as db:
        done = await receipt_strip.run(db, dry_run=False)
    assert done.counts == {"stripped": 1, "unchanged": 1, "removed": 1}
    async with get_sessionmaker()() as db:
        row = await db.get(ReceiptDocument, stripped_id)
        assert row is not None
        stored = receipts_dir / row.image_path
        assert not _has_gps(stored.read_bytes())
        assert row.sha256 == hashlib.sha256(stored.read_bytes()).hexdigest()
        assert row.upload_sha256 == hashlib.sha256(with_gps).hexdigest()
        job = (
            await db.execute(select(IngestJob).where(IngestJob.receipt_document_id == stripped_id))
        ).scalar_one()
        assert job.status == "done"  # the receipt keeps its job (and through it, its purchase)
    assert not old_path.exists()
    async with get_sessionmaker()() as db:
        again = await receipt_strip.run(db, dry_run=False)
    assert again.counts["stripped"] == 0  # running it twice changes nothing more
    assert plain_id and gone_id


async def test_the_backfill_refuses_while_a_file_is_missing(admin, receipts_dir: Path):
    data = _photo("JPEG", "missing")
    doc_id, path = await _stored_before(admin, receipts_dir, data, "image/jpeg")
    keep_id, keep = await _stored_before(admin, receipts_dir, _photo("JPEG", "keep"), "image/jpeg")
    path.unlink()
    async with get_sessionmaker()() as db:
        report = await receipt_strip.run(db, dry_run=False)
    assert report.missing == [str(doc_id)] and not report.changed
    assert _has_gps(keep.read_bytes())  # nothing was changed
    assert keep_id


async def test_the_backfill_leaves_a_receipt_being_read(admin, receipts_dir: Path):
    data = _photo("JPEG", "busy")
    _, path = await _stored_before(admin, receipts_dir, data, "image/jpeg", status="running")
    async with get_sessionmaker()() as db:
        report = await receipt_strip.run(db, dry_run=False)
    assert report.counts == {"in_use": 1}
    assert path.read_bytes() == data


async def test_the_command_says_what_a_dry_run_would_do(admin, receipts_dir: Path):
    await _stored_before(admin, receipts_dir, _photo("JPEG", "cli"), "image/jpeg")
    env = os.environ.copy()
    env["RECEIPTS_PATH"] = str(receipts_dir)
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "receipts", "strip-metadata", "--dry-run"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "1  would be stripped" in result.stdout
    assert "Nothing was changed." in result.stdout
