"""Product photo files: normalized originals, masks and derivatives (03, 1I).

Everything lives under ``MEDIA_PATH``:

```
incoming/<upload_sha>.<ext>             as uploaded, until the worker has read it
incoming/mask-<image_id>-<source>.png   a mask waiting for the worker
originals/<aa>/<bb>/<sha>.<jpg|png>     upright, sRGB, <= 3000 px, no metadata   (backed up)
masks/<aa>/<bb>/<mask_sha>.png          single channel, the original's size      (backed up)
derived/<aa>/<bb>/<sha>/<variant>-<ver>[-<mask_sha>].webp                       (rebuildable)
```

An original keeps its pixels: it is turned upright, converted to sRGB and
capped in size, and nothing else. A derivative is addressed by the original's
sha, the pipeline version and, for a cutout, the mask's sha, so the same inputs
always give the same bytes and a changed input is a new address.

The uploaded bytes are untrusted (non-negotiable 7): they are decoded by a
library, never executed, and the pixel count is checked from the header before
any pixels are decoded (``raster.guard_megapixels``).
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import uuid
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.ingest.errors import StageFailure
from app.ingest.raster import PHOTO_MAX_MEGAPIXELS, guard_megapixels, orient

if TYPE_CHECKING:
    from PIL import Image

# Bump when this module's output changes for the same inputs. The library
# versions are part of the pipeline version too, because a new Pillow or libwebp
# can encode the same pixels to different bytes.
PIPELINE_REVISION = 1

ORIGINAL_LONG_SIDE = 3000
ORIGINAL_QUALITY = 90
WEBP_QUALITY = 82
SIZES = {"160": 160, "480": 480, "1200": 1200}
CUTOUTS = {"cutout-480": 480, "cutout-1200": 1200}
CUTOUT_PADDING = 0.08
# A mask covering less or more than this of the photo is discarded: it found
# nothing, or everything.
MASK_MIN_COVER = 0.05
MASK_MAX_COVER = 0.95

PHOTO_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"}

_NAME = re.compile(
    r"^(?P<variant>160|480|1200|cutout-480|cutout-1200)-(?P<version>[0-9a-f]{8})"
    r"(?:-(?P<mask>[0-9a-f]{64}))?$"
)
_SHA = re.compile(r"^[0-9a-f]{64}$")


@cache
def pipeline_version() -> str:
    """Eight hex digits naming this pipeline: its revision and its encoders' versions."""
    import PIL
    from PIL import features

    parts = (PIPELINE_REVISION, PIL.__version__, features.version("webp"))
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8]


def root() -> Path:
    return Path(get_settings().media_path)


def _fan(sha: str) -> Path:
    return Path(sha[:2]) / sha[2:4]


def original_path(sha: str, ext: str) -> Path:
    return root() / "originals" / _fan(sha) / f"{sha}.{ext}"


def find_original(sha: str) -> Path | None:
    for ext in ("jpg", "png"):
        path = original_path(sha, ext)
        if path.is_file():
            return path
    return None


def mask_path(mask_sha: str) -> Path:
    return root() / "masks" / _fan(mask_sha) / f"{mask_sha}.png"


def derived_name(variant: str, mask_sha: str | None = None) -> str:
    name = f"{variant}-{pipeline_version()}"
    return f"{name}-{mask_sha}" if variant in CUTOUTS else name


def derived_path(sha: str, name: str) -> Path:
    return root() / "derived" / _fan(sha) / sha / f"{name}.webp"


def incoming_path(upload_sha: str, ext: str) -> Path:
    return root() / "incoming" / f"{upload_sha}.{ext}"


def incoming_mask_path(image_id: uuid.UUID, source: str) -> Path:
    return root() / "incoming" / f"mask-{image_id}-{source}.png"


def url(sha: str, variant: str, mask_sha: str | None = None) -> str:
    return f"/api/v1/media/{sha}/{derived_name(variant, mask_sha)}"


@dataclass(frozen=True)
class MediaName:
    variant: str
    version: str
    mask_sha: str | None


def parse_name(sha: str, name: str) -> MediaName | None:
    """The parts of a media address, or None when it is not one this pipeline makes."""
    match = _NAME.match(name)
    if not _SHA.match(sha) or match is None:
        return None
    variant, mask = match["variant"], match["mask"]
    if (variant in CUTOUTS) != (mask is not None):
        return None
    return MediaName(variant, match["version"], mask)


def write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.part")
    tmp.write_bytes(data)
    tmp.replace(target)


# --- decoding -----------------------------------------------------------------


def _open(data: bytes) -> Image.Image:
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    return Image.open(io.BytesIO(data))


_UNREADABLE = (OSError, ValueError, SyntaxError)


def probe(data: bytes) -> tuple[int, int]:
    """The photo's displayed size, from its header alone; nothing is decoded.

    Raises StageFailure ``image_unreadable`` or ``image_too_large``.
    """
    from PIL import Image

    try:
        with _open(data) as image:
            width, height = image.size
            guard_megapixels(width, height, code="image_too_large", limit=PHOTO_MAX_MEGAPIXELS)
            turned = image.getexif().get(0x0112, 1) in (5, 6, 7, 8)
    except Image.DecompressionBombError:
        raise StageFailure(code="image_too_large") from None
    except _UNREADABLE as exc:
        raise StageFailure(code="image_unreadable", detail=type(exc).__name__) from None
    return (height, width) if turned else (width, height)


def _clean(image: Image.Image) -> Image.Image:
    """The same pixels in a new image that carries no metadata at all."""
    from PIL import Image

    return Image.frombytes(image.mode, image.size, image.tobytes())


def _to_srgb(image: Image.Image) -> Image.Image:
    """Convert from the embedded colour profile to sRGB; RGB(A) out either way."""
    from PIL import ImageCms

    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    mode = "RGBA" if has_alpha else "RGB"
    profile = image.info.get("icc_profile")
    if profile and image.mode in ("RGB", "RGBA", "CMYK"):
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            converted = ImageCms.profileToProfile(
                image, source, ImageCms.createProfile("sRGB"), outputMode=image.mode
            )
            if converted is not None:
                image = converted
        except (ImageCms.PyCMSError, OSError):
            pass  # an unreadable profile: keep the pixels as they are
    return image.convert(mode)


def _cap(image: Image.Image, long_side: int) -> Image.Image:
    from PIL import Image

    scale = long_side / max(image.size)
    if scale >= 1:
        return image
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _phash(image: Image.Image) -> int:
    """A 64-bit difference hash, as a signed integer for a BIGINT column."""
    from PIL import Image

    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = small.tobytes()  # one byte per pixel in mode L
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = pixels[row * 9 + col], pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits - (1 << 64) if bits >= 1 << 63 else bits


@dataclass(frozen=True)
class Normalized:
    data: bytes
    ext: str
    sha256: str
    width: int
    height: int
    phash: int


def normalize(data: bytes) -> Normalized:
    """The stored original: upright, sRGB, capped, re-encoded with no metadata."""
    from PIL import Image

    try:
        with _open(data) as opened:
            guard_megapixels(
                opened.width, opened.height, code="image_too_large", limit=PHOTO_MAX_MEGAPIXELS
            )
            image = _to_srgb(orient(opened))
    except Image.DecompressionBombError:
        raise StageFailure(code="image_too_large") from None
    except _UNREADABLE as exc:
        raise StageFailure(code="image_unreadable", detail=type(exc).__name__) from None
    image = _clean(_cap(image, ORIGINAL_LONG_SIDE))
    out = io.BytesIO()
    if image.mode == "RGBA":
        image.save(out, format="PNG", optimize=False)
        ext = "png"
    else:
        image.save(out, format="JPEG", quality=ORIGINAL_QUALITY)
        ext = "jpg"
    stored = out.getvalue()
    return Normalized(
        data=stored,
        ext=ext,
        sha256=hashlib.sha256(stored).hexdigest(),
        width=image.width,
        height=image.height,
        phash=_phash(image),
    )


def mask_fits(mask_size: tuple[int, int], photo_size: tuple[int, int]) -> bool:
    """A mask is the photo's displayed size, or that size before the 3000 px cap."""
    (mw, mh), (pw, ph) = mask_size, photo_size
    if (mw, mh) == (pw, ph):
        return True
    # A larger mask of the same shape: the photo was capped after it was made.
    return mw >= pw and mh >= ph and abs(mw * ph - mh * pw) <= max(mw, mh)


def probe_mask(data: bytes, photo_size: tuple[int, int]) -> None:
    """Refuse a mask that cannot be this photo's, from its header alone."""
    from PIL import Image

    try:
        with _open(data) as image:
            size = image.size
    except (Image.DecompressionBombError, *_UNREADABLE):
        raise StageFailure(code="mask_unreadable") from None
    if not mask_fits(size, photo_size):
        raise StageFailure(code="mask_mismatch", detail=f"{size[0]}x{size[1]}")


@dataclass(frozen=True)
class NormalizedMask:
    data: bytes
    sha256: str
    cover: float


def normalize_mask(data: bytes, photo_size: tuple[int, int]) -> NormalizedMask | None:
    """The stored mask at the original's size, or None when it covers too little or too much."""
    from PIL import Image

    try:
        with _open(data) as opened:
            if not mask_fits(opened.size, photo_size):
                raise StageFailure(code="mask_mismatch")
            guard_megapixels(
                opened.width, opened.height, code="mask_too_large", limit=PHOTO_MAX_MEGAPIXELS
            )
            mask = opened.convert("L")
    except Image.DecompressionBombError:
        raise StageFailure(code="mask_too_large") from None
    except _UNREADABLE:
        raise StageFailure(code="mask_unreadable") from None
    if mask.size != photo_size:
        mask = mask.resize(photo_size, Image.Resampling.LANCZOS)
    mask = _clean(mask)
    histogram = mask.histogram()
    cover = sum(histogram[128:]) / (mask.width * mask.height)
    if not MASK_MIN_COVER <= cover <= MASK_MAX_COVER:
        return None
    out = io.BytesIO()
    mask.save(out, format="PNG")
    stored = out.getvalue()
    return NormalizedMask(stored, hashlib.sha256(stored).hexdigest(), cover)


# --- derivatives --------------------------------------------------------------


def _webp(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=WEBP_QUALITY, method=6, exact=False)
    return out.getvalue()


def _cutout(original: Image.Image, mask: Image.Image, side: int) -> Image.Image:
    """The masked subject, centred on a transparent square with 8% padding."""
    from PIL import Image

    subject = original.convert("RGBA")
    subject.putalpha(mask)
    box = mask.point(lambda v: 255 if v >= 128 else 0).getbbox() or (0, 0, *mask.size)
    subject = subject.crop(box)
    inner = max(subject.size)
    full = math.ceil(inner / (1 - 2 * CUTOUT_PADDING))
    canvas = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    canvas.paste(subject, ((full - subject.width) // 2, (full - subject.height) // 2))
    if full > side:
        canvas = canvas.resize((side, side), Image.Resampling.LANCZOS)
    return canvas


def render(original_bytes: bytes, variant: str, mask_bytes: bytes | None = None) -> bytes:
    """One derivative, as WebP. Pure: the same inputs give the same bytes."""
    from PIL import Image

    with Image.open(io.BytesIO(original_bytes)) as opened:
        original = opened.copy()
    if variant in SIZES:
        return _webp(_cap(original, SIZES[variant]))
    if mask_bytes is None:
        raise ValueError(f"{variant} needs a mask")
    with Image.open(io.BytesIO(mask_bytes)) as opened_mask:
        mask = opened_mask.convert("L")
    return _webp(_cutout(original, mask, CUTOUTS[variant]))


def ensure(sha: str, variant: str, mask_sha: str | None = None) -> Path | None:
    """The derivative's file, made now if missing; None when its sources are gone."""
    target = derived_path(sha, derived_name(variant, mask_sha))
    if target.is_file():
        return target
    original = find_original(sha)
    if original is None:
        return None
    mask_bytes = None
    if variant in CUTOUTS:
        if mask_sha is None or not mask_path(mask_sha).is_file():
            return None
        mask_bytes = mask_path(mask_sha).read_bytes()
    write_atomic(target, render(original.read_bytes(), variant, mask_bytes))
    return target


def build_all(sha: str, mask_sha: str | None) -> list[Path]:
    """Every derivative a photo has: the sizes, and the cutouts when it has a mask."""
    variants = list(SIZES) + (list(CUTOUTS) if mask_sha else [])
    return [p for v in variants if (p := ensure(sha, v, mask_sha)) is not None]
