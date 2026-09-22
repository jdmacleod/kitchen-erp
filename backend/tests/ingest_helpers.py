"""Shared helpers for the Phase 2C ingest tests.

Fixtures are YAML under tests/fixtures/receipts; any image is generated here at
test time and written only under a temporary directory. Every vendor, address,
phone, card and item in the corpus is invented; coordinates lie in the synthetic
grid from SECURITY.md.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from PIL import Image, ImageDraw, ImageFont

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.ingest import llm, ocr
from app.ingest.errors import OcrUnavailable
from app.ingest.replay import RecordedTransport
from app.ingest.stages import run_pending
from app.models import IngestJob

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "receipts"


@dataclass
class ReceiptFixture:
    name: str
    notes: str
    ocr_text: str
    expected_header: dict[str, Any]
    expected_lines: list[dict[str, Any]]
    expected_reconciliation: dict[str, Any]
    llm_responses: dict[str, Any]


def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.yaml"))


def load_fixture(name: str) -> ReceiptFixture:
    data = yaml.safe_load((FIXTURE_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    return ReceiptFixture(
        name=data["name"],
        notes=data.get("notes", ""),
        ocr_text=data["ocr_text"],
        expected_header=data.get("expected_header", {}),
        expected_lines=data.get("expected_lines", []),
        expected_reconciliation=data.get("expected_reconciliation", {}),
        llm_responses=data["llm_responses"],
    )


# --- images (never written into the repository) --------------------------------


def png_bytes(seed: str) -> bytes:
    """A small, valid PNG whose pixels depend on ``seed`` (distinct sha256 per seed)."""
    image = Image.new("L", (96, 24), color=255)
    ImageDraw.Draw(image).text((2, 2), seed[:14], fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for candidate in (
        "DejaVuSansMono.ttf",
        "Menlo.ttc",
        "Courier New.ttf",
        "LiberationMono-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_receipt_png(text: str, path: Path, *, size: int = 28) -> Path:
    """Render receipt text as a black-on-white PNG for the Tesseract path."""
    font = _font(size)
    lines = text.splitlines() or [""]
    line_height = int(size * 1.4)
    width = max(int(font.getlength(line)) for line in lines) + 80
    height = line_height * len(lines) + 80
    image = Image.new("L", (max(width, 400), height), color=255)
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((40, 40 + i * line_height), line, font=font, fill=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


# --- pytest fixtures ------------------------------------------------------------


@pytest.fixture
def receipts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Store uploaded images under a temp dir; pin the zone the fixtures assume."""
    target = tmp_path / "receipts"
    settings = get_settings()
    monkeypatch.setattr(settings, "receipts_path", str(target))
    monkeypatch.setattr(settings, "household_timezone", "America/Los_Angeles")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "llm_timeout_seconds", 2.0)
    monkeypatch.setattr(llm, "http_transport", None)
    yield target


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], RecordedTransport]:
    """Install a RecordedTransport for a fixture (or a raw responses map)."""

    def _install(source: ReceiptFixture | dict[str, Any]) -> RecordedTransport:
        responses = source.llm_responses if isinstance(source, ReceiptFixture) else source
        transport = RecordedTransport(responses)
        monkeypatch.setattr(llm, "http_transport", transport)
        return transport

    return _install


@pytest.fixture
def ocr_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A private copy of the OCR adapter registry tests may edit."""
    registry = dict(ocr.ADAPTERS)
    monkeypatch.setattr(ocr, "ADAPTERS", registry)
    return registry


@dataclass
class SpyAdapter:
    """An OCR adapter that records calls and returns canned text (or declines)."""

    name: str
    text: str | None = None
    version: str = "spy"
    calls: list[uuid.UUID] = field(default_factory=list)

    async def run(self, document: Any, image_path: Path) -> str:
        self.calls.append(document.id)
        if self.text is None:
            raise OcrUnavailable("spy_declines")
        return self.text

    def factory(self) -> Callable[[], SpyAdapter]:
        return lambda: self


# --- API helpers ------------------------------------------------------------------


async def upload(
    client: httpx.AsyncClient,
    data: bytes,
    *,
    ocr_text: str | None = None,
    captured_at: datetime | None = None,
    lat: str | None = None,
    lon: str | None = None,
    headers: dict[str, str] | None = None,
    filename: str = "receipt.png",
    content_type: str = "image/png",
) -> httpx.Response:
    form: dict[str, str] = {}
    if ocr_text is not None:
        form["ocr_text"] = ocr_text
    if captured_at is not None:
        form["captured_at"] = captured_at.isoformat()
    if lat is not None:
        form["lat"] = lat
    if lon is not None:
        form["lon"] = lon
    return await client.post(
        "/api/v1/receipts",
        files={"image": (filename, data, content_type)},
        data=form,
        headers=headers or {},
    )


async def upload_fixture(
    client: httpx.AsyncClient, fixture: ReceiptFixture, *, client_ocr: bool = True, **kw: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    r = await upload(
        client, png_bytes(fixture.name), ocr_text=fixture.ocr_text if client_ocr else None, **kw
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["document"], body["job"]


async def get_job(
    client: httpx.AsyncClient, job_id: str, *, include_output: bool = False
) -> dict[str, Any]:
    r = await client.get(
        f"/api/v1/ingest-jobs/{job_id}", params={"include_output": str(include_output).lower()}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def stage_outputs(client: httpx.AsyncClient, job_id: str) -> dict[str, dict[str, Any]]:
    detail = await get_job(client, job_id, include_output=True)
    return {r["stage"]: r["output"] for r in detail["stage_results"]}


# --- driving the pipeline ---------------------------------------------------------


async def run_job(job_id: str | uuid.UUID, **kw: Any) -> IngestJob:
    """Run every stage the job can run now, in a fresh session; return the job."""
    async with get_sessionmaker()() as db:
        job = await db.get(IngestJob, uuid.UUID(str(job_id)), populate_existing=True)
        assert job is not None
        await run_pending(db, job, **kw)
        job = await db.get(IngestJob, job.id, populate_existing=True)
        assert job is not None
        return job


async def load_job(job_id: str | uuid.UUID) -> IngestJob:
    async with get_sessionmaker()() as db:
        job = await db.get(IngestJob, uuid.UUID(str(job_id)), populate_existing=True)
        assert job is not None
        return job
