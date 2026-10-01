"""Product photos (03, 1I): criteria 95–100 and 102.

Every image here is generated; no photo of anything real is used.
"""

from __future__ import annotations

import asyncio
import io
import struct
import zlib
from pathlib import Path

import asyncpg
import httpx
import pytest
from PIL import Image

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.services import media, product_photos
from tests.pricebook_helpers import make_product

RED, BLUE = (200, 30, 30), (30, 60, 200)


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


@pytest.fixture
async def product(admin_client: httpx.AsyncClient) -> dict:
    return await make_product(admin_client, "Rolled oats", "Oat tin")


def jpeg(size=(320, 240), colour=BLUE, **save) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="JPEG", **save)
    return out.getvalue()


def with_gps() -> bytes:
    """A JPEG carrying GPS tags, an XMP packet and a camera model."""
    exif = Image.Exif()
    exif[0x0110] = "Synthetic Camera"  # Model
    exif[0x8825] = {1: "N", 2: (33.0, 36.0, 0.0), 3: "W", 4: (120.0, 31.0, 0.0)}
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">synthetic</x:xmpmeta>'
    return jpeg(exif=exif.tobytes(), xmp=xmp)


def portrait_on_its_side() -> bytes:
    """Seen upright it is 200x300 with a red top third; stored turned, with orientation 6."""
    upright = Image.new("RGB", (200, 300), BLUE)
    upright.paste(Image.new("RGB", (200, 100), RED), (0, 0))
    sensor = upright.transpose(Image.Transpose.ROTATE_90)  # what the camera wrote
    exif = Image.Exif()
    exif[0x0112] = 6
    out = io.BytesIO()
    sensor.save(out, format="JPEG", quality=95, exif=exif.tobytes())
    return out.getvalue()


def mask_png(size: tuple[int, int], box: tuple[int, int, int, int]) -> bytes:
    mask = Image.new("L", size, 0)
    mask.paste(255, box)
    out = io.BytesIO()
    mask.save(out, format="PNG")
    return out.getvalue()


def png_header_only(width: int, height: int) -> bytes:
    """A PNG that declares its size and has no pixel data at all."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + body))
        return struct.pack(">I", len(body)) + kind + body + crc

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


async def upload(client, product_id: str, *photos: bytes, roles: list[str] | None = None):
    files = [("photos", (f"p{i}.jpg", data, "image/jpeg")) for i, data in enumerate(photos)]
    data: dict = {"product_id": product_id}
    if roles:
        data["roles"] = roles
    return await client.post("/api/v1/product-photos", data=data, files=files)


async def work() -> int:
    """Run the worker's product jobs until none is waiting."""
    ran = 0
    async with get_sessionmaker()() as db:
        while await product_photos.run_once(db, locked_by="test"):
            ran += 1
    return ran


async def photos_of(client, product_id: str) -> dict:
    r = await client.get(f"/api/v1/products/{product_id}/photos")
    assert r.status_code == 200, r.text
    return r.json()


def has_metadata(data: bytes) -> bool:
    markers = (b"Exif", b"EXIF", b"XMP", b"xmpmeta", b"GPS", b"Synthetic Camera")
    return any(m in data for m in markers)


async def test_upload_is_stored_queued_then_processed(admin_client, product):
    r = await upload(admin_client, product["id"], jpeg())
    assert r.status_code == 201, r.text
    [shown] = r.json()["items"]
    assert shown["status"] == "processing" and shown["urls"] is None
    assert r.json()["primary_image_id"] is None

    assert await work() == 1
    listed = await photos_of(admin_client, product["id"])
    [photo] = listed["items"]
    assert photo["status"] == "active" and photo["is_main"]
    assert (photo["width"], photo["height"]) == (320, 240)
    assert listed["primary_image_id"] == photo["id"]
    got = (await admin_client.get(f"/api/v1/products/{product['id']}")).json()
    assert got["photo"]["id"] == photo["id"] and got["photo"]["urls"]["small"].endswith(
        f"/160-{media.pipeline_version()}"
    )
    listing = (await admin_client.get("/api/v1/products")).json()["items"]
    assert listing[0]["photo"]["id"] == photo["id"]


async def test_the_same_photo_twice_is_the_same_image(admin_client, product):
    """Criterion 95, first part."""
    first = (await upload(admin_client, product["id"], with_gps())).json()["items"][0]
    again = (await upload(admin_client, product["id"], with_gps())).json()["items"]
    assert [p["id"] for p in again] == [first["id"]]
    await work()
    third = (await upload(admin_client, product["id"], with_gps())).json()["items"]
    assert [p["id"] for p in third] == [first["id"]]


async def test_stored_files_carry_no_metadata(admin_client, product, media_root: Path):
    """Criterion 95: no EXIF, XMP or GPS in originals, masks or derivatives."""
    assert has_metadata(with_gps())  # the generated photo does carry them
    await upload(admin_client, product["id"], with_gps())
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    r = await admin_client.post(
        f"/api/v1/product-photos/{photo['id']}/mask",
        files={"mask": ("m.png", mask_png((320, 240), (40, 40, 280, 200)), "image/png")},
    )
    assert r.status_code == 202, r.text
    await work()
    files = [p for p in media_root.rglob("*") if p.is_file()]
    kinds = {p.relative_to(media_root).parts[0] for p in files}
    assert kinds == {"originals", "masks", "derived"}  # incoming/ was emptied
    for path in files:
        assert not has_metadata(path.read_bytes()), path
        with Image.open(path) as image:
            assert not image.getexif()
            assert not {"exif", "xmp", "XML:com.adobe.xmp"} & set(image.info)


async def test_a_heic_photo_produces_every_derivative(admin_client, product, media_root: Path):
    """Criterion 95, last part."""
    import pillow_heif

    pillow_heif.register_heif_opener()
    out = io.BytesIO()
    Image.new("RGB", (300, 200), RED).save(out, format="HEIF")
    r = await admin_client.post(
        "/api/v1/product-photos",
        data={"product_id": product["id"]},
        files=[("photos", ("p.heic", out.getvalue(), "image/heic"))],
    )
    assert r.status_code == 201, r.text
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    assert photo["status"] == "active"
    for key in ("small", "medium", "large"):
        got = await admin_client.get(photo["urls"][key])
        assert got.status_code == 200 and got.headers["content-type"] == "image/webp"


async def test_rebuild_is_byte_identical_and_addresses_name_their_inputs(
    admin_client, product, media_root: Path
):
    """Criterion 96."""
    await upload(admin_client, product["id"], jpeg((900, 700)))
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    await admin_client.post(
        f"/api/v1/product-photos/{photo['id']}/mask",
        files={"mask": ("m.png", mask_png((900, 700), (100, 100, 700, 600)), "image/png")},
    )
    await work()
    derived = media_root / "derived"
    before = {p.relative_to(derived): p.read_bytes() for p in derived.rglob("*.webp")}
    assert len(before) == 5  # three sizes and two cutouts

    import shutil

    shutil.rmtree(derived)
    async with get_sessionmaker()() as db:
        assert await product_photos.rebuild_derivatives(db) == 5
    after = {p.relative_to(derived): p.read_bytes() for p in derived.rglob("*.webp")}
    assert after == before

    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    url = photo["urls"]["cutout_medium"]
    sha, name = url.split("/")[-2:]
    other_version = name.replace(media.pipeline_version(), "0" * 8)
    other_mask = name[:-64] + "f" * 64
    assert (await admin_client.get(url)).status_code == 200
    assert (await admin_client.get(f"/api/v1/media/{sha}/{other_version}")).status_code == 404
    assert (await admin_client.get(f"/api/v1/media/{sha}/{other_mask}")).status_code == 404


async def test_a_sideways_portrait_and_its_mask_give_an_upright_square_cutout(
    admin_client, product
):
    """Criterion 97, first part: the mask is in displayed orientation."""
    await upload(admin_client, product["id"], portrait_on_its_side())
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    assert (photo["width"], photo["height"]) == (200, 300)
    # The red top third, seen upright.
    r = await admin_client.post(
        f"/api/v1/product-photos/{photo['id']}/mask",
        files={"mask": ("m.png", mask_png((200, 300), (20, 10, 180, 90)), "image/png")},
    )
    assert r.status_code == 202, r.text
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    assert photo["has_cutout"] and photo["cutout_source"] == "tool"
    got = await admin_client.get(photo["urls"]["cutout_medium"])
    with Image.open(io.BytesIO(got.content)) as cutout:
        assert cutout.mode == "RGBA" and cutout.width == cutout.height
        alpha = cutout.getchannel("A")
        left, top, right, bottom = alpha.point(lambda v: 255 if v >= 128 else 0).getbbox()
        side = cutout.width
        # The subject's long side (160 px wide) fills all but 8% on each side.
        assert abs(left / side - 0.08) < 0.02 and abs((side - right) / side - 0.08) < 0.02
        assert top > left  # shorter than it is wide, so centred with more room above
        r_, g_, b_, _ = cutout.getpixel((side // 2, side // 2))
        assert r_ > 150 and b_ < 100  # red: the mask landed on the top third


async def test_a_mask_covering_too_little_gives_no_cutout(admin_client, product):
    """Criterion 97, second part."""
    await upload(admin_client, product["id"], jpeg((200, 200)))
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    tiny = mask_png((200, 200), (0, 0, 28, 28))  # about 2%
    await admin_client.post(
        f"/api/v1/product-photos/{photo['id']}/mask", files={"mask": ("m.png", tiny, "image/png")}
    )
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    assert photo["status"] == "active" and photo["is_main"]
    assert not photo["has_cutout"] and photo["urls"]["cutout_medium"] is None
    assert (await admin_client.get(photo["urls"]["medium"])).status_code == 200


async def test_a_mask_of_another_size_is_refused(admin_client, product):
    await upload(admin_client, product["id"], jpeg((200, 200)))
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    r = await admin_client.post(
        f"/api/v1/product-photos/{photo['id']}/mask",
        files={"mask": ("m.png", mask_png((300, 200), (0, 0, 100, 100)), "image/png")},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "mask_mismatch"


async def test_oversized_and_unreadable_photos_are_refused_before_decoding(
    admin_client, product, media_root: Path
):
    """Criterion 98."""
    huge = png_header_only(10_000, 6_000)  # 60 MP declared, no pixels behind it
    r = await upload(admin_client, product["id"], huge)
    assert r.status_code == 422 and r.json()["error"]["code"] == "image_too_large"
    r = await upload(admin_client, product["id"], b"GIF89a not a photo this reads")
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_image"
    r = await upload(admin_client, product["id"], b"\xff\xd8\xff truncated jpeg")
    assert r.status_code == 415
    # One bad file in a batch stores none of them.
    r = await upload(admin_client, product["id"], jpeg(), huge)
    assert r.status_code == 422
    assert not media_root.exists() or not any(media_root.rglob("*.*"))
    assert (await photos_of(admin_client, product["id"]))["items"] == []


async def test_more_than_four_photos_are_refused(admin_client, product):
    r = await upload(admin_client, product["id"], *(jpeg(colour=(i, i, i)) for i in range(5)))
    assert r.status_code == 422


async def test_a_photo_that_fails_says_so_and_can_be_retried(admin_client, product, media_root):
    # Readable header, broken body: refused only when the worker decodes it.
    out = io.BytesIO()
    Image.effect_noise((600, 600), 64).convert("RGB").save(out, format="JPEG")
    broken = out.getvalue()[: len(out.getvalue()) // 2]
    r = await upload(admin_client, product["id"], broken)
    assert r.status_code == 201, r.text
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    assert photo["status"] == "failed" and photo["urls"] is None
    r = await admin_client.post(f"/api/v1/product-photos/{photo['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "processing"
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    assert photo["status"] == "failed"


async def test_labels_are_never_the_main_photo(admin_client, product):
    r = await upload(
        admin_client,
        product["id"],
        jpeg(colour=RED),
        jpeg(colour=BLUE, quality=50),
        roles=["label_nutrition", "label_front"],
    )
    assert r.status_code == 201, r.text
    await work()
    listed = await photos_of(admin_client, product["id"])
    assert {p["role"] for p in listed["items"]} == {"label_nutrition", "label_front"}
    assert listed["primary_image_id"] is None
    label = listed["items"][0]
    r = await admin_client.post(f"/api/v1/product-photos/{label['id']}/use-as-main")
    assert r.status_code == 409


async def test_choose_unset_hide_and_show(admin_client, product):
    """Criterion 99, second part: unsetting a choice restores the rule's choice."""
    await upload(admin_client, product["id"], jpeg((400, 400)), jpeg((800, 800)))
    await work()
    small, big = (await photos_of(admin_client, product["id"]))["items"]
    assert big["is_main"]  # more pixels

    r = await admin_client.post(f"/api/v1/product-photos/{small['id']}/use-as-main")
    assert r.status_code == 200 and r.json()["is_main"] and r.json()["pinned"]
    r = await admin_client.post(f"/api/v1/product-photos/{small['id']}/unset-main")
    assert not r.json()["is_main"]
    assert (await photos_of(admin_client, product["id"]))["primary_image_id"] == big["id"]

    r = await admin_client.post(f"/api/v1/product-photos/{big['id']}/hide")
    assert r.json()["status"] == "hidden"
    assert (await photos_of(admin_client, product["id"]))["primary_image_id"] == small["id"]
    r = await admin_client.post(f"/api/v1/product-photos/{big['id']}/show")
    assert r.json()["is_main"]


async def test_concurrent_choices_end_with_one_main_photo(
    admin_client, product, owner_conn: asyncpg.Connection
):
    """Criterion 99, last part."""
    await upload(admin_client, product["id"], *(jpeg(colour=(i * 40, 0, 0)) for i in range(4)))
    await work()
    ids = [p["id"] for p in (await photos_of(admin_client, product["id"]))["items"]]

    async def choose(image_id: str) -> None:
        async with get_sessionmaker()() as db:
            await product_photos.use_as_main(db, image_id)  # type: ignore[arg-type]

    await asyncio.gather(*(choose(i) for i in ids))
    pinned = await owner_conn.fetch("SELECT id FROM product_image WHERE pinned")
    assert len(pinned) == 1
    main = await owner_conn.fetchval("SELECT primary_image_id FROM product")
    assert main == pinned[0]["id"]


async def test_media_needs_a_session_and_is_cached_for_good(admin_client, product, client):
    """Criterion 100."""
    await upload(admin_client, product["id"], jpeg())
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    url = photo["urls"]["medium"]
    assert url.startswith("/api/v1/media/")
    r = await admin_client.get(url)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    async with httpx.AsyncClient(transport=client._transport, base_url="http://test") as anon:
        assert (await anon.get(url)).status_code == 401


async def test_a_missing_derivative_is_rebuilt_and_a_missing_original_is_404(
    admin_client, product, media_root: Path
):
    await upload(admin_client, product["id"], jpeg())
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    url = photo["urls"]["large"]
    first = (await admin_client.get(url)).content
    for path in (media_root / "derived").rglob("*.webp"):
        path.unlink()
    assert (await admin_client.get(url)).content == first
    for path in (media_root / "originals").rglob("*.*"):
        path.unlink()
    for path in (media_root / "derived").rglob("*.webp"):
        path.unlink()
    assert (await admin_client.get(url)).status_code == 404


async def test_without_a_mask_there_is_no_cutout_and_a_helper_mask_adds_one(admin_client, product):
    """Criterion 102: the stub helper posts a mask, which is recorded as the tool's."""
    await upload(admin_client, product["id"], jpeg((300, 300)))
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    assert photo["status"] == "active" and not photo["has_cutout"]
    assert photo["cutout_source"] is None

    stub_helper_mask = mask_png((300, 300), (50, 50, 250, 250))
    r = await admin_client.post(
        f"/api/v1/product-photos/{photo['id']}/mask",
        data={"source": "tool"},
        files={"mask": ("m.png", stub_helper_mask, "image/png")},
    )
    assert r.status_code == 202
    await work()
    [photo] = (await photos_of(admin_client, product["id"]))["items"]
    assert photo["has_cutout"] and photo["cutout_source"] == "tool"
    assert (await admin_client.get(photo["urls"]["cutout_large"])).status_code == 200


async def test_a_mask_for_a_photo_still_processing_waits(admin_client, product):
    [photo] = (await upload(admin_client, product["id"], jpeg())).json()["items"]
    r = await admin_client.post(
        f"/api/v1/product-photos/{photo['id']}/mask",
        files={"mask": ("m.png", mask_png((320, 240), (0, 0, 100, 100)), "image/png")},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "photo_processing"


async def test_stage_results_are_append_only(
    admin_client, product, app_conn: asyncpg.Connection, owner_conn: asyncpg.Connection
):
    await upload(admin_client, product["id"], jpeg())
    await work()
    row = await owner_conn.fetchrow("SELECT id, output FROM product_stage_result")
    assert row is not None
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("UPDATE product_stage_result SET duration_ms = 0")
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
        await owner_conn.execute("DELETE FROM product_stage_result WHERE id = $1", row["id"])
