"""Row boxes on the receipt photo (04, 2S; issue 380).

The matching is pure and tested with hand-written TSV; the stage result, the
worker's pickup and the endpoint run against the database with Tesseract
replaced; the last tests run the real binary and skip where it isn't installed.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.ingest import row_boxes
from app.ingest.printed_rows import receipt_rows
from app.ingest.row_boxes import Box, FoundLine, line_rows, match_rows, parse_tsv, similarity
from app.models import IngestStageResult, PurchaseLine
from app.services import row_boxes as row_boxes_service
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.conftest import TESSERACT_SKIP
from tests.ingest_helpers import load_fixture, render_receipt_as, run_job, upload_fixture
from tests.pricebook_helpers import make_location, make_product

no_network = gh.no_network
receipts_dir = ih.receipts_dir
recorded = ih.recorded

BACKEND_ROOT = Path(__file__).resolve().parents[1]
HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)

NONE_FOUND = {"width": None, "height": None, "lines": []}


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


def _tsv(*lines: tuple[int, list[tuple[str, int, int]]]) -> str:
    """TSV for lines of words: (top, [(word, left, width), ...]), each 20px tall."""
    rows = [HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t600\t900\t-1\t"]
    for n, (top, words) in enumerate(lines, start=1):
        rows.append(f"4\t1\t1\t1\t{n}\t0\t0\t{top}\t600\t20\t-1\t")
        for w, (word, left, width) in enumerate(words, start=1):
            rows.append(f"5\t1\t1\t1\t{n}\t{w}\t{left}\t{top}\t{width}\t20\t91.5\t{word}")
    return "\n".join(rows) + "\n"


def _found(*texts: str) -> list[FoundLine]:
    return [FoundLine(t, Box(10, 30 * i, 400, 20)) for i, t in enumerate(texts)]


# --- parsing Tesseract's TSV -----------------------------------------------------------


def test_tsv_words_become_lines_with_the_box_around_their_words():
    tsv = _tsv(
        (40, [("KESTREL", 20, 90), ("OATS", 120, 50), ("4.29", 400, 50)]),
        (70, [("Bag", 20, 40), ("fee", 70, 40), ("0.10", 400, 50)]),
    )
    found = parse_tsv(tsv)
    assert [f.text for f in found] == ["KESTREL OATS 4.29", "Bag fee 0.10"]
    assert found[0].box == Box(left=20, top=40, width=430, height=20)


def test_tsv_skips_empty_words_short_rows_and_a_missing_header():
    blank = "5\t1\t1\t1\t1\t1\t20\t40\t30\t20\t-1\t "
    short = "5\t1\t1"
    not_a_number = "5\t1\t1\t1\t2\t1\tx\t40\t30\t20\t9\tA"
    tsv = "\n".join([HEADER, blank, short, not_a_number]) + "\n"
    assert parse_tsv(tsv) == []
    assert parse_tsv("") == []
    assert parse_tsv("not\ta\ttsv\n5\t1") == []


# --- matching printed rows to found lines ------------------------------------------------


def test_similarity_ignores_case_spacing_and_punctuation():
    assert similarity("Sourdough loaf        6.50", "SOURDOUGH LOAF 6.50") == 100
    assert similarity("Local honey 12oz 9.00", "Local honey 120z 9.00") >= 90
    assert similarity("Bag fee 0.10", "Total 24.41") < row_boxes.MIN_SCORE


def test_rows_match_their_lines_in_order_with_misreads_tolerated():
    rows = ["Rosalind's Corner Grocery", "Sourdough loaf 6.50", "Butter 1lb 5.75", "Total 24.41"]
    found = _found(
        "Rosalind's Corner Grocery", "Sourdough loaf 6.50", "Butter 11b 5.75", "Total 24.41"
    )
    assert [(m.row, m.found) for m in match_rows(rows, found)] == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_a_row_matched_with_low_confidence_gets_no_box():
    rows = ["Sourdough loaf 6.50", "Club savings -1.00", "Total 5.50"]
    # Tesseract smeared the middle row past recognition.
    found = _found("Sourdough loaf 6.50", "~~ ..i:: ;;", "Total 5.50")
    matches = match_rows(rows, found)
    assert [m.row for m in matches] == [0, 2]
    assert all(m.score >= row_boxes.MIN_SCORE for m in matches)


def test_rows_printed_alike_take_successive_lines_never_the_same_one():
    rows = ["KESTREL OATS 4.29", "Club Savings 0.50", "PEPPERS 0.63", "Club Savings 0.50"]
    found = _found("KESTREL OATS 4.29", "Club Savings 0.50", "PEPPERS 0.63", "Club Savings 0.50")
    assert [(m.row, m.found) for m in match_rows(rows, found)] == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_lines_tesseract_found_that_the_text_does_not_have_are_skipped():
    rows = ["Sourdough loaf 6.50", "Total 6.50"]
    found = _found("Sourdough loaf 6.50", "Thank you for shopping", "Total 6.50")
    assert [(m.row, m.found) for m in match_rows(rows, found)] == [(0, 0), (1, 2)]


def test_matching_never_crosses_order_for_a_better_score():
    # Row 1's text appears only above row 0's line: taking it would put row 1
    # above row 0 on the photo, so it gets no box.
    rows = ["Total 6.50", "Sourdough loaf 6.50"]
    found = _found("Sourdough loaf 6.50", "Total 6.50")
    assert len(match_rows(rows, found)) == 1


def test_rows_too_short_to_tell_apart_get_no_box():
    assert match_rows(["S", "1"], _found("S", "1")) == []


def test_the_output_keeps_numbers_only_never_the_text():
    text = "Sourdough loaf 6.50\nTotal 6.50\n"
    out = row_boxes.boxes_output(text, _found("Sourdough loaf 6.50", "Total 6.50"), 600, 900)
    assert out["width"] == 600 and out["height"] == 900
    assert out["row_count"] == 2 and out["found_count"] == 2
    assert out["rows"][1] == {
        "row": 1,
        "left": 10,
        "top": 30,
        "width": 400,
        "height": 20,
        "score": 100,
    }
    assert all(isinstance(v, int) for box in out["rows"] for v in box.values())


# --- each line's row ----------------------------------------------------------------------


def test_a_line_takes_the_row_it_was_matched_to_and_alike_rows_in_turn():
    rows = ["KESTREL OATS 4.29", "Club Savings 0.50", "PEPPERS 0.63", "Club Savings 0.50"]
    lines = [
        ("KESTREL OATS 4.29", "KESTREL OATS"),
        ("Club Savings 0.50", "Club Savings"),
        ("PEPPERS 0.63", "PEPPERS"),
        ("Club Savings 0.50", "Club Savings"),
        (None, "A line someone added"),
    ]
    assert line_rows(lines, rows) == [0, 1, 2, 3, None]


def test_a_line_read_before_rows_were_kept_finds_its_row_by_its_text():
    rows = ["Sourdough loaf 6.50", "Butter 1lb 5.75"]
    assert line_rows([(None, "Butter 1lb"), (None, "Sourdough")], rows) == [1, None]


def test_a_printed_row_no_longer_in_the_text_gives_no_row():
    assert line_rows([("Gone 1.00", "Gone")], ["Sourdough loaf 6.50"]) == [None]


# --- recorded after reading, served to review ----------------------------------------------


def _fake_tesseract(monkeypatch: pytest.MonkeyPatch, *, garble: str | None = None) -> list[str]:
    """Tesseract replaced: a line per OCR row, 30px apart; ``garble`` reads as noise."""
    calls: list[str] = []

    async def fake_find_lines(source: Path, mime: str, command: str):
        calls.append(mime)
        text = load_fixture("independent_minimal").ocr_text
        found = [
            FoundLine("#~ ;;" if row == garble else row, Box(12, 40 + 30 * i, 500, 22))
            for i, row in enumerate(receipt_rows(text))
        ]
        return found, 640, 1200

    monkeypatch.setattr(row_boxes, "find_lines", fake_find_lines)
    monkeypatch.setattr(row_boxes_service, "tesseract_installed", lambda: True)
    return calls


async def _read_receipt(admin_client: httpx.AsyncClient, recorded) -> tuple[str, str]:
    fixture = load_fixture("independent_minimal")
    recorded(fixture)
    _, job = await upload_fixture(admin_client, fixture)
    final = await run_job(job["id"])
    assert final.stage == "review"
    return job["id"], str(final.purchase_id)


async def _boxes(admin_client: httpx.AsyncClient, purchase_id: str) -> dict:
    r = await admin_client.get(f"/api/v1/purchases/{purchase_id}/row-boxes")
    assert r.status_code == 200, r.text
    return r.json()


async def test_review_gets_a_box_for_each_line_whose_row_was_found(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded, monkeypatch
):
    _fake_tesseract(monkeypatch, garble="Butter 1lb 5.75")
    job_id, purchase_id = await _read_receipt(admin_client, recorded)
    assert await _boxes(admin_client, purchase_id) == NONE_FOUND

    async with get_sessionmaker()() as db:
        assert await row_boxes_service.run_once(db, set()) is True
        assert await row_boxes_service.run_once(db, set()) is False  # nothing left to do
        lines = (
            await db.execute(
                select(PurchaseLine).where(PurchaseLine.purchase_id == uuid.UUID(purchase_id))
            )
        ).scalars()
        by_text = {line.raw_text.split()[0]: str(line.id) for line in lines}

    body = await _boxes(admin_client, purchase_id)
    assert (body["width"], body["height"]) == (640, 1200)
    boxed = {b["line_id"]: b for b in body["lines"]}
    # Rows 3, 4 and 6 of the text: the loaf, the honey and the apples.
    assert boxed[by_text["Sourdough"]] == {
        "line_id": by_text["Sourdough"],
        "left": 12,
        "top": 130,
        "width": 500,
        "height": 22,
    }
    assert boxed[by_text["Apples"]]["top"] == 40 + 30 * 6
    # The butter's row read as noise, so it has no outline.
    assert by_text["Butter"] not in boxed
    assert len(boxed) == 4

    outputs = await ih.stage_outputs(admin_client, job_id)
    assert "row_boxes" not in outputs  # not a reading stage; the job's stages are unchanged


async def test_boxes_are_a_new_stage_result_tied_to_the_text_they_matched(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded, monkeypatch
):
    _fake_tesseract(monkeypatch)
    job_id, purchase_id = await _read_receipt(admin_client, recorded)
    async with get_sessionmaker()() as db:
        result = await row_boxes_service.find_for_job(db, uuid.UUID(job_id))
        assert result is not None
        ocr = (
            await db.execute(
                select(IngestStageResult).where(
                    IngestStageResult.job_id == uuid.UUID(job_id), IngestStageResult.stage == "ocr"
                )
            )
        ).scalar_one()
        assert result.stage == "row_boxes" and result.adapter == "tesseract"
        assert result.output["ocr_result_id"] == str(ocr.id)
        assert "text" not in result.output
        # The text is read again: boxes found against the old text no longer apply.
        db.add(
            IngestStageResult(
                job_id=ocr.job_id,
                stage="ocr",
                adapter="client",
                adapter_version="1",
                output=dict(ocr.output),
                duration_ms=0,
            )
        )
        await db.commit()
        assert await row_boxes_service.jobs_needing_boxes(db) == [uuid.UUID(job_id)]
    assert (await _boxes(admin_client, purchase_id))["lines"] == []


async def test_without_tesseract_nothing_runs_and_nothing_is_recorded(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded, monkeypatch
):
    job_id, _ = await _read_receipt(admin_client, recorded)
    monkeypatch.setattr(row_boxes_service, "tesseract_installed", lambda: False)
    async with get_sessionmaker()() as db:
        assert await row_boxes_service.run_once(db, set()) is False
    # The binary itself missing is the same: find_for_job records nothing.
    monkeypatch.setattr(row_boxes_service, "get_settings", _settings_with("no-such-tesseract"))
    async with get_sessionmaker()() as db:
        assert await row_boxes_service.find_for_job(db, uuid.UUID(job_id)) is None
        assert await row_boxes_service.jobs_needing_boxes(db) == [uuid.UUID(job_id)]


def _settings_with(command: str):
    from app.core.config import get_settings

    settings = get_settings().model_copy(update={"tesseract_command": command})
    return lambda: settings


async def test_a_failure_leaves_the_receipt_without_boxes_and_the_worker_moves_on(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded, monkeypatch
):
    job_id, _ = await _read_receipt(admin_client, recorded)
    monkeypatch.setattr(row_boxes_service, "tesseract_installed", lambda: True)

    async def broken(source: Path, mime: str, command: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(row_boxes, "find_lines", broken)
    failed: set[uuid.UUID] = set()
    async with get_sessionmaker()() as db:
        assert await row_boxes_service.run_once(db, failed) is True
        assert failed == {uuid.UUID(job_id)}
        # Not tried again by this worker.
        assert await row_boxes_service.run_once(db, failed) is False
        found = await db.execute(
            select(IngestStageResult).where(IngestStageResult.stage == "row_boxes")
        )
        assert found.first() is None


async def test_a_receipt_still_being_read_waits_for_its_reading(
    admin_client: httpx.AsyncClient, receipts_dir: Path
):
    fixture = load_fixture("independent_minimal")
    _, job = await upload_fixture(admin_client, fixture)
    async with get_sessionmaker()() as db:
        assert await row_boxes_service.jobs_needing_boxes(db) == []
    assert job["stage"] == "captured"


async def test_row_boxes_of_a_purchase_with_no_receipt_or_no_purchase(
    admin_client: httpx.AsyncClient,
):
    r = await admin_client.get(f"/api/v1/purchases/{uuid.uuid4()}/row-boxes")
    assert r.status_code == 404
    loc = await make_location(admin_client, "Lantern Market", "Lantern Market")
    leeks = await make_product(admin_client, "Leeks", "Market leeks")
    line = {"product_id": leeks["id"], "qty": "1", "unit": "each", "line_total": "2.00"}
    r = await admin_client.post(
        "/api/v1/purchases",
        json={
            "vendor_location_id": loc["id"],
            "purchased_at": "2026-03-22T23:05:00Z",
            "total": "2.00",
            "lines": [line],
        },
    )
    assert r.status_code == 201, r.text
    assert await _boxes(admin_client, r.json()["id"]) == {
        "width": None,
        "height": None,
        "lines": [],
    }


def test_the_command_refuses_without_tesseract():
    env = os.environ.copy()
    env["TESSERACT_COMMAND"] = "no-such-tesseract"
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "receipts", "row-boxes"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "tesseract is not installed" in result.stderr


# --- the real binary ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("tesseract") is None, reason=TESSERACT_SKIP)
@pytest.mark.parametrize("mime", ["image/png", "application/pdf"])
async def test_tesseract_finds_a_box_for_every_printed_row(mime: str, tmp_path: Path):
    fixture = load_fixture("price_and_you_pay")
    path = render_receipt_as(mime, fixture.ocr_text, tmp_path / f"r.{mime.rsplit('/', 1)[1]}")
    found, width, height = await row_boxes.find_lines(path, mime, "tesseract")
    out = row_boxes.boxes_output(fixture.ocr_text, found, width, height)
    assert out["rows"] and len(out["rows"]) >= out["row_count"] - 2
    tops = [b["top"] for b in out["rows"]]
    assert tops == sorted(tops)
    assert all(b["left"] >= 0 and b["left"] + b["width"] <= width for b in out["rows"])


@pytest.mark.skipif(shutil.which("tesseract") is None, reason=TESSERACT_SKIP)
async def test_boxes_are_measured_on_the_photo_turned_upright(tmp_path: Path):
    """A phone writes a sideways photo and a tag to turn it; review shows it turned,
    so the boxes are measured on the turned image."""
    fixture = load_fixture("independent_minimal")
    upright = render_receipt_as("image/png", fixture.ocr_text, tmp_path / "upright.png")
    with Image.open(upright) as image:
        size = image.size
        sideways = image.convert("RGB").rotate(90, expand=True)  # stored turned a quarter
    exif = Image.Exif()
    exif[0x0112] = 6  # display: rotate 90 clockwise
    buffer = io.BytesIO()
    sideways.save(buffer, format="JPEG", quality=95, exif=exif)
    stored = tmp_path / "photo.jpg"
    stored.write_bytes(buffer.getvalue())

    found, width, height = await row_boxes.find_lines(stored, "image/jpeg", "tesseract")
    assert (width, height) == size
    out = row_boxes.boxes_output(fixture.ocr_text, found, width, height)
    assert len(out["rows"]) >= out["row_count"] - 1
