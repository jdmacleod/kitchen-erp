"""Colour renders for a vision model and for review, upright in both (spec 04, 2J).

Every image here is generated: a white page with a dark block in its top-left
corner, so "upright" can be checked by where the block lands.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pillow_heif
import pytest
from PIL import Image

from app.ingest import raster
from app.ingest.errors import StageFailure
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.ingest_helpers import upload

no_network = gh.no_network
receipts_dir = ih.receipts_dir

UPRIGHT = (300, 120)
MARK = 40  # the dark block's side, at the upright page's top-left
EXIF_ORIENTATION = 0x0112


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


def _upright_page(colour: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    page = Image.new("RGB", UPRIGHT, "white")
    page.paste(colour, (0, 0, MARK, MARK))
    return page


def _sideways(path: Path, fmt: str) -> Path:
    """The upright page as a phone held sideways writes it: turned pixels plus a
    tag saying to turn them back (orientation 6)."""
    pillow_heif.register_heif_opener()
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = 6
    _upright_page().rotate(90, expand=True).save(path, format=fmt, exif=exif.tobytes(), quality=95)
    return path


def _is_upright(image: Image.Image) -> bool:
    gray = image.convert("L")
    w, h = gray.size
    return (
        (w, h) == UPRIGHT
        and gray.getpixel((MARK // 2, MARK // 2)) < 64
        and gray.getpixel((w - MARK // 2, h - MARK // 2)) > 192
    )


@pytest.mark.parametrize(("fmt", "converter"), [("JPEG", None), ("HEIF", "heif")])
def test_a_sideways_photo_renders_upright(tmp_path: Path, fmt: str, converter: str | None):
    source = _sideways(tmp_path / f"receipt.{fmt.lower()}", fmt)
    assert _is_upright(raster.render_page(source, converter).image)


def test_a_box_on_the_vision_image_lands_on_the_same_place_in_review(tmp_path: Path):
    """The model's image is smaller, but the same way up, so its boxes scale."""
    source = _sideways(tmp_path / "receipt.heic", "HEIF")
    vision = raster.vision_png(source, "heif", long_side=150)
    review = raster.render_page(source, "heif").image

    with Image.open(io.BytesIO(vision.png)) as shown:
        assert shown.size == (vision.width, vision.height) == (150, 60)
        # Where the model would see the dark block: its bounding box.
        box = shown.convert("L").point(lambda v: 255 if v < 64 else 0).getbbox()
    assert box is not None
    scale = review.width / vision.width
    centre = ((box[0] + box[2]) / 2 * scale, (box[1] + box[3]) / 2 * scale)
    assert review.convert("L").getpixel(centre) < 64


def test_colour_is_kept_for_a_model_and_for_review(tmp_path: Path):
    pillow_heif.register_heif_opener()
    for fmt, converter in (("PDF", "pdf"), ("HEIF", "heif"), ("PNG", None)):
        source = tmp_path / f"colour.{fmt.lower()}"
        _upright_page(colour=(200, 30, 30)).save(source, format=fmt)
        red, green, _ = raster.render_page(source, converter).image.getpixel((5, 5))
        assert red > 150 and green < 80, fmt


def test_tesseract_still_gets_grayscale(tmp_path: Path):
    source = tmp_path / "colour.pdf"
    _upright_page(colour=(200, 30, 30)).save(source, format="PDF")
    with Image.open(raster.to_png("pdf", source, tmp_path / "page.png")) as page:
        assert page.mode == "L"


def test_only_page_one_is_rendered_and_a_longer_pdf_says_so(tmp_path: Path):
    one, three = tmp_path / "one.pdf", tmp_path / "three.pdf"
    _upright_page().save(one, format="PDF")
    _upright_page().save(three, format="PDF", save_all=True, append_images=[_upright_page()] * 2)

    assert raster.render_page(one, "pdf").pages_truncated is False
    rendered = raster.render_page(three, "pdf")
    assert rendered.pages_truncated is True
    assert raster.vision_png(three, "pdf").pages_truncated is True


def test_the_vision_image_caps_the_long_side_and_never_enlarges(tmp_path: Path):
    tall = tmp_path / "tall.png"
    Image.new("RGB", (800, 3000), "white").save(tall)
    capped = raster.vision_png(tall, None)
    assert raster.VISION_LONG_SIDE == 2000
    assert (capped.width, capped.height) == (533, 2000)

    small = tmp_path / "small.png"
    Image.new("RGB", (400, 900), "white").save(small)
    kept = raster.vision_png(small, None)
    assert (kept.width, kept.height) == (400, 900)


def test_a_render_holds_the_pixel_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "page.png"
    _upright_page().save(source)
    monkeypatch.setattr(raster, "MAX_MEGAPIXELS", 0.001)
    with pytest.raises(StageFailure) as refused:
        raster.render_page(source, None)
    assert refused.value.code == "image_too_large"


async def test_a_thumbnail_of_a_sideways_photo_is_upright(
    admin_client: httpx.AsyncClient, receipts_dir: Path, tmp_path: Path
):
    """A scaled JPEG used to come out sideways: the browser's own EXIF handling
    turns the original, but never saw the PNG the server scaled from it."""
    source = _sideways(tmp_path / "receipt.jpg", "JPEG")
    document = (await upload(admin_client, source.read_bytes(), content_type="image/jpeg")).json()[
        "document"
    ]

    r = await admin_client.get(f"/api/v1/receipts/{document['id']}/image", params={"width": 150})

    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(r.content)) as thumb:
        assert thumb.size == (150, 60)
        assert thumb.convert("L").getpixel((10, 10)) < 64
