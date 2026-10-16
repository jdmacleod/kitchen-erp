"""Where each printed row sits on the receipt photo (04, 2S; issue 380).

After a receipt is read, a local Tesseract pass over the stored image returns
a box per text line it finds (its TSV output). :func:`match_rows` pairs those
lines, in order, with the rows of the text the receipt was read from, by a
tolerant text match; a row matched with low confidence gets no box.
:func:`line_rows` then finds each purchase line's row, so review can outline
the current line's row on the photo.

The boxes are measured on the page as :func:`app.ingest.raster.render_page`
draws it: page 1, upright by its EXIF orientation, PDFs at the OCR render's
resolution. That is the image review shows, so a box lands on the row a person
sees. They are kept in that image's pixels, with its size beside them.

This is a convenience and never part of reading: it makes no model call, sends
nothing anywhere, and a missing or failing Tesseract records nothing.

Receipt text is untrusted: it is compared, never interpreted. Tesseract's
output is parsed field by field, and only numbers and words are taken from it.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.ingest import formats, raster
from app.ingest.errors import StageFailure
from app.ingest.ocr import RASTER_TIMEOUT_SECONDS, TESSERACT_CONFIG, TESSERACT_TIMEOUT_SECONDS
from app.ingest.printed_rows import receipt_rows, row_holding, row_key

STAGE = "row_boxes"
ROW_BOXES_VERSION = "1"

# How alike a printed row and a line Tesseract found must be, as a percentage
# (difflib's ratio over the normalized text), for the row to get the line's box.
# Two readings of the same row by different OCR agree on most characters; two
# different rows of a receipt rarely share more than their layout.
MIN_SCORE = 70
# A row with fewer letters and digits than this matches too much by chance.
_MIN_CHARS = 3
# Tesseract's TSV level for a word (1 page, 2 block, 3 paragraph, 4 line).
_WORD_LEVEL = "5"
_NOT_WORD = re.compile(r"[^a-z0-9]+")


class BoxesUnavailable(Exception):
    """Boxes could not be found for this receipt; nothing is recorded."""


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class FoundLine:
    """One line Tesseract found: its words, and the box around them."""

    text: str
    box: Box


@dataclass(frozen=True)
class RowMatch:
    row: int  # index into receipt_rows(the OCR text)
    found: int  # index into the found lines
    score: int  # 0-100


def parse_tsv(tsv: str) -> list[FoundLine]:
    """Tesseract's TSV as lines of words, each with the box around its words.

    Words are grouped by page, block, paragraph and line number. Empty words
    (Tesseract reports layout rows with no text) and rows that do not parse are
    skipped.
    """
    rows = tsv.splitlines()
    if not rows:
        return []
    names = rows[0].split("\t")
    try:
        at = {name: names.index(name) for name in _COLUMNS}
    except ValueError:
        return []
    lines: dict[tuple[str, ...], list[tuple[str, Box]]] = {}
    for raw in rows[1:]:
        cells = raw.split("\t")
        if len(cells) < len(names) or cells[at["level"]] != _WORD_LEVEL:
            continue
        text = cells[at["text"]].strip()
        if not text:
            continue
        try:
            box = Box(*(int(cells[at[n]]) for n in ("left", "top", "width", "height")))
        except ValueError:
            continue
        key = tuple(cells[at[n]] for n in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append((text, box))
    return [_line(words) for words in lines.values()]


# The TSV columns read, found by name in its header row.
_COLUMNS = (
    "level",
    "page_num",
    "block_num",
    "par_num",
    "line_num",
    "left",
    "top",
    "width",
    "height",
    "text",
)


def _line(words: list[tuple[str, Box]]) -> FoundLine:
    left = min(b.left for _, b in words)
    top = min(b.top for _, b in words)
    right = max(b.left + b.width for _, b in words)
    bottom = max(b.top + b.height for _, b in words)
    return FoundLine(" ".join(t for t, _ in words), Box(left, top, right - left, bottom - top))


def _normal(text: str) -> str:
    return _NOT_WORD.sub(" ", text.lower()).strip()


def similarity(a: str, b: str) -> int:
    """How alike two rows read, 0-100, ignoring case, spacing and punctuation."""
    matcher = difflib.SequenceMatcher(None, _normal(a), _normal(b), autojunk=False)
    floor = MIN_SCORE / 100
    if matcher.real_quick_ratio() < floor or matcher.quick_ratio() < floor:
        return 0
    return round(matcher.ratio() * 100)


def match_rows(rows: Sequence[str], found: Sequence[FoundLine]) -> list[RowMatch]:
    """Pair printed rows with found lines, both in reading order.

    The pairing keeps both orders (a row never matches a line above an earlier
    row's) and maximizes the summed score of the pairs it makes; a pair scoring
    under MIN_SCORE is never made. Two rows printed alike ("Club Savings")
    therefore take the first and second such line, not both the first.
    """
    n, m = len(rows), len(found)
    scores = [
        [similarity(r, f.text) if len(row_key(r)) >= _MIN_CHARS else 0 for f in found] for r in rows
    ]
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = scores[i - 1][j - 1]
            pair = best[i - 1][j - 1] + s if s >= MIN_SCORE else 0
            best[i][j] = max(best[i - 1][j], best[i][j - 1], pair)
    matches: list[RowMatch] = []
    i, j = n, m
    while i > 0 and j > 0:
        s = scores[i - 1][j - 1]
        if s >= MIN_SCORE and best[i][j] == best[i - 1][j - 1] + s:
            matches.append(RowMatch(i - 1, j - 1, s))
            i, j = i - 1, j - 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            j -= 1
    matches.reverse()
    return matches


def boxes_output(receipt_text: str, found: list[FoundLine], width: int, height: int) -> dict:
    """The stage output: the image size, and a box for each matched row."""
    rows = receipt_rows(receipt_text)
    matches = match_rows(rows, found)
    return {
        "version": ROW_BOXES_VERSION,
        "width": width,
        "height": height,
        "row_count": len(rows),
        "found_count": len(found),
        "rows": [
            {
                "row": match.row,
                "left": found[match.found].box.left,
                "top": found[match.found].box.top,
                "width": found[match.found].box.width,
                "height": found[match.found].box.height,
                "score": match.score,
            }
            for match in matches
        ],
    }


def line_rows(
    lines: Sequence[tuple[str | None, str | None]], rows: Sequence[str]
) -> list[int | None]:
    """Each line's row index, from (printed_text, raw_text) pairs in line order.

    A line the reading matched to a row (``printed_text``) takes the next row
    printed exactly so (or, out of order, the first such row not yet taken); a
    line read before rows were kept takes the next row holding its text, as the
    reading matches them. Anything else has no row.
    """
    keys = [row_key(row) for row in rows]
    used: set[int] = set()
    at = 0
    out: list[int | None] = []
    for printed, raw in lines:
        j: int | None = None
        if printed:
            free = [k for k, row in enumerate(rows) if row == printed and k not in used]
            j = next((k for k in free if k >= at), free[0] if free else None)
        elif raw:
            j = row_holding(row_key(raw), keys, at)
        if j is not None and j not in used:
            used.add(j)
            at = max(at, j + 1)
            out.append(j)
        else:
            out.append(None)
    return out


# --- running Tesseract ---------------------------------------------------------------


def _upright_gray(source: Path, converter: str | None, target: Path) -> tuple[int, int]:
    """Page 1 as review shows it, in grayscale for Tesseract; its pixel size."""
    image = raster.render_page(source, converter).image
    image.convert("L").save(target, format="PNG")
    return image.width, image.height


async def find_lines(source: Path, mime: str, command: str) -> tuple[list[FoundLine], int, int]:
    """Run Tesseract over the stored receipt; the lines it finds and the page size.

    Raises BoxesUnavailable for anything that stops it: no binary, a format or
    file it cannot render, a timeout, a failed run.
    """
    if shutil.which(command) is None:
        raise BoxesUnavailable("tesseract_missing")
    fmt = formats.BY_MIME.get(mime)
    if fmt is None:
        raise BoxesUnavailable("unsupported_format")
    with tempfile.TemporaryDirectory(prefix="kerp-rows-") as tmp:
        page = Path(tmp) / "page.png"
        try:
            width, height = await asyncio.wait_for(
                asyncio.to_thread(_upright_gray, source, fmt.converter, page),
                timeout=RASTER_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            raise BoxesUnavailable("raster_timeout") from None
        except StageFailure as exc:
            raise BoxesUnavailable(exc.code) from None
        tsv = await _tesseract_tsv(command, page)
    return parse_tsv(tsv), width, height


async def _tesseract_tsv(command: str, page: Path) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            str(page),
            "stdout",
            "-l",
            "eng",
            *TESSERACT_CONFIG,
            "tsv",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        raise BoxesUnavailable("tesseract_spawn_failed") from None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=TESSERACT_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise BoxesUnavailable("tesseract_timeout") from None
    if proc.returncode != 0:
        raise BoxesUnavailable("tesseract_failed")
    return stdout.decode("utf-8", errors="replace")
