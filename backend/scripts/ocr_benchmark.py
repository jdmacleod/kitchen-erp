"""Measure an OCR setting before adopting it (#65).

Run inside the api container, where Tesseract, the fixtures and the model
server's address all are::

    docker compose exec api python scripts/ocr_benchmark.py synthetic
    docker compose exec api python scripts/ocr_benchmark.py real /data/bench

``synthetic`` renders every fixture receipt, degrades it like a phone document
scan (downscale, blur, a slight tilt, speckle, JPEG), reads it with each
configuration, and reports the share of the fixture's printed prices recovered
exactly. It needs no model and no real data, so its output can be shared.

``real DIR`` reads every PDF, JPEG or PNG in DIR with each configuration, then
asks the model for the header twice per text and counts the reads that
recovered a printed total. That is the measure that decided #65: the total is
what every line is checked against, and a setting that read more prices on
synthetic scans (Sauvola thresholding) lost the total on three of eight real
receipts. A total that is present is not necessarily right: at 300 dpi every
read returned one, but one had lost its leading digit. So give ``--expected FILE``,
one ``filename,total`` per line copied from the receipts themselves (kept outside
the repository with the receipts), and each read is scored as an exact match.
Without it only presence is scored. With ``--show-totals`` it prints each
receipt's totals so the disagreements can be checked against the images. That output is household
data: read it on this machine, and never paste it into an issue or a PR.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import io
import random
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import pypdfium2
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from decimal import Decimal  # noqa: E402

from app.ingest.errors import IngestError, InvalidModelOutput  # noqa: E402
from app.ingest.header import HEADER_TASK  # noqa: E402
from app.ingest.llm import LlmClient  # noqa: E402
from app.ingest.ocr import TESSERACT_CONFIG  # noqa: E402
from app.ingest.raster import PDF_RENDER_DPI  # noqa: E402
from app.ingest.schemas import ReceiptHeader  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "receipts"
PRICE = re.compile(r"\b\d{1,4}\.\d{2}\b")

Preprocess = Callable[[Image.Image], Image.Image]


def _same(image: Image.Image) -> Image.Image:
    return image


# name -> (render dpi for PDFs, preprocessing, Tesseract arguments). "current" is
# what the worker does; add a candidate here and run both modes.
CONFIGS: dict[str, tuple[float, Preprocess, tuple[str, ...]]] = {
    "current": (PDF_RENDER_DPI, _same, TESSERACT_CONFIG),
    "sauvola": (PDF_RENDER_DPI, _same, (*TESSERACT_CONFIG, "-c", "thresholding_method=2")),
    "300dpi": (300, _same, TESSERACT_CONFIG),
    "psm4": (PDF_RENDER_DPI, _same, ("--psm", "4")),
}

# (scale, blur radius, tilt in degrees, JPEG quality), mild to harsh.
DEGRADATIONS = [(0.60, 0.6, 0.4, 50), (0.50, 0.9, -0.8, 35), (0.42, 1.1, 1.2, 30)]


class MeasurementError(RuntimeError):
    """A run that could not measure anything; never scored as a poor result."""


def tesseract(image: Image.Image, args: tuple[str, ...]) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        image.save(f.name)
        done = subprocess.run(
            ["tesseract", f.name, "stdout", "-l", "eng", *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    if done.returncode != 0:
        # A missing language or a bad candidate argument, not bad OCR.
        raise MeasurementError(f"tesseract {' '.join(args)}: {done.stderr.strip()[:300]}")
    return done.stdout


def render(text: str, size: int = 22) -> Image.Image:
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", size)  # in the api image
    except OSError:
        font = ImageFont.load_default(size=size)
    rows = text.splitlines()
    height = int(size * 1.35)
    width = max(int(font.getlength(r)) for r in rows) + 60
    image = Image.new("L", (width, height * len(rows) + 60), 255)
    draw = ImageDraw.Draw(image)
    for i, row in enumerate(rows):
        draw.text((30, 30 + i * height), row, font=font, fill=0)
    return image


def degrade(
    image: Image.Image, seed: int, scale: float, blur: float, tilt: float, quality: int
) -> Image.Image:
    rnd = random.Random(seed)
    out = image.resize((int(image.width * scale), int(image.height * scale)), Image.BILINEAR)
    out = out.filter(ImageFilter.GaussianBlur(blur)).rotate(tilt, expand=True, fillcolor=235)
    out = out.point(lambda p: min(255, int(p * 0.85 + 30)))  # thermal-paper grey
    pixels = out.load()
    for _ in range(out.width * out.height // 400):
        pixels[rnd.randrange(out.width), rnd.randrange(out.height)] = rnd.choice((90, 200))
    buffer = io.BytesIO()
    out.save(buffer, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buffer.getvalue())).convert("L")


def price_recall(expected: list[str], text: str) -> int:
    got = collections.Counter(PRICE.findall(text))
    return sum(min(n, got[t]) for t, n in collections.Counter(expected).items())


def synthetic() -> None:
    scores = collections.defaultdict(lambda: [0, 0])
    for path in sorted(FIXTURES.glob("*.yaml")):
        text = yaml.safe_load(path.read_text())["ocr_text"]
        expected = PRICE.findall(text)
        if not expected:
            continue
        clean = render(text)
        for k, degradation in enumerate(DEGRADATIONS):
            scan = degrade(clean, k, *degradation)
            for name, (dpi, prepare, args) in CONFIGS.items():
                if dpi != PDF_RENDER_DPI:
                    continue  # a synthetic scan has no render resolution to change
                score = scores[name]
                score[0] += price_recall(expected, tesseract(prepare(scan), args))
                score[1] += len(expected)
    print("Synthetic scans: printed prices recovered exactly")
    for name, (hit, total) in scores.items():
        print(f"  {name:10} {hit:4}/{total:<4} {100 * hit / total:5.1f}%")
    skipped = [n for n, (dpi, _p, _a) in CONFIGS.items() if dpi != PDF_RENDER_DPI]
    if skipped:
        print(f"  not scored here (they differ only in PDF render dpi): {', '.join(skipped)}")


def load_page(path: Path, dpi: float) -> Image.Image:
    if path.suffix.lower() == ".pdf":
        document = pypdfium2.PdfDocument(path)
        try:
            return document[0].render(scale=dpi / 72).to_pil().convert("L")
        finally:
            document.close()
    return Image.open(path).convert("L")


def load_expected(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    expected = {}
    for row in path.read_text().splitlines():
        if row.strip() and not row.startswith("#"):
            name, total = (part.strip() for part in row.split(",", 1))
            expected[name] = total
    return expected


async def real(directory: Path, show_totals: bool, expected_path: Path | None) -> int:
    kinds = (".pdf", ".jpg", ".jpeg", ".png")
    files = sorted(p for p in directory.iterdir() if p.suffix.lower() in kinds)
    expected = load_expected(expected_path)
    client = LlmClient()
    present = collections.Counter()
    exact = collections.Counter()
    decimals = collections.Counter()
    model_errors = collections.Counter()
    disagree = 0
    for n, path in enumerate(files, start=1):
        per_config = {}
        for name, (dpi, prepare, args) in CONFIGS.items():
            text = tesseract(prepare(load_page(path, dpi)), args)
            decimals[name] += len(PRICE.findall(text))
            totals: list[str | None] = []
            for _ in range(2):
                try:
                    header, _ = await client.extract(ReceiptHeader, HEADER_TASK, text)
                except InvalidModelOutput:
                    totals.append(None)  # the model answered; no usable total
                    continue
                except IngestError as exc:
                    # Unreachable or timed out: not a read, so not a score.
                    model_errors[name] += 1
                    totals.append(exc.code)
                    continue
                totals.append(None if header.total is None else str(header.total))
            numbers = [t for t in totals if t and t[0].isdigit()]
            present[name] += len(numbers)
            want = expected.get(path.name)
            if want is not None:
                exact[name] += sum(1 for t in numbers if Decimal(t) == Decimal(want))
            per_config[name] = totals
        values = {t for totals in per_config.values() for t in totals if t and t[0].isdigit()}
        disagree += len(values) > 1
        if show_totals:
            cells = (f"{k}={'/'.join(map(str, v))}" for k, v in per_config.items())
            print(f"  receipt {n} ({path.name}): " + "  ".join(cells))
    reads = 2 * len(files)
    scored = sum(1 for f in files if f.name in expected)
    print(f"Real receipts ({len(files)}): header reads per configuration: {reads}")
    for name in CONFIGS:
        line = f"  {name:10} total present {present[name]:3}/{reads:<3}"
        if scored:
            line += f" exact {exact[name]:3}/{2 * scored:<3}"
        print(f"{line} decimals {decimals[name]}")
    if not scored:
        print("  presence only: give --expected to score exact totals")
    print(f"  configurations disagree on the total for {disagree} receipt(s)")
    if sum(model_errors.values()):
        print(f"  NOT MEASURED: {sum(model_errors.values())} read(s) could not reach the model")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("synthetic")
    real_parser = sub.add_parser("real")
    real_parser.add_argument("directory", type=Path)
    real_parser.add_argument("--show-totals", action="store_true")
    real_parser.add_argument("--expected", type=Path, help="filename,total per line")
    args = parser.parse_args()
    try:
        if args.mode == "synthetic":
            synthetic()
        else:
            sys.exit(asyncio.run(real(args.directory, args.show_totals, args.expected)))
    except MeasurementError as exc:
        sys.exit(f"not measured: {exc}")


if __name__ == "__main__":
    main()
