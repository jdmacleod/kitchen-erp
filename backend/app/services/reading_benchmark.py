"""``kerp reading-benchmark``: measure receipt readers on the household's receipts.

Spec 04, 2J; the design is ``docs/designs/vision-receipt-reading.md``, "Phase 0".
Step 1 compares three arms on receipts a person has already committed:

* (a) today's pipeline: OCR text read by the text model;
* (b) vision as OCR: an OCR model transcribes the image, then (a)'s text path;
* (c) a vision reader: a vision model reads the header and the lines (with
  boxes) from the image, one call each.

Every reading goes through the same post-passes as the lines stage and is scored
against what the person committed, never against the reader's own total.

**It writes nothing to the app database (R2-7).** Everything it needs is read
up front in one read-only transaction that is rolled back, before any model is
called; a test asserts that the row counts it could touch are unchanged. It
never runs a stage or drafts a purchase. Its output goes under the benchmarks
directory: a per-reading checkpoint (``readings.jsonl``, for ``--resume``), a
``summary.txt`` and one ``reading.csv`` row per configuration. Those hold ids,
counts and scores, never receipt text.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import WORKER_APPLICATION_NAME
from app.ingest import header as header_stage
from app.ingest import lines as lines_stage
from app.ingest import llm, raster, readers, structure
from app.ingest.errors import (
    InvalidModelOutput,
    ModelMissing,
    ModelTimeout,
    ModelUnavailable,
    StageFailure,
)
from app.ingest.formats import BY_MIME
from app.ingest.llm import VISION_RETRY, CallLedger, LlmClient, RetryPolicy
from app.ingest.ocr import run_ocr
from app.ingest.schemas import ReceiptHeader, VisionReceiptLines, valid_box
from app.ingest.witness import any_support, witness_amounts
from app.models import ReceiptDocument
from app.services.normalize import normalize_receipt_text

# --- the grid --------------------------------------------------------------------

# Arm (c)'s screening grid (MS4, 2026-10-01): two quality-tier models and four
# that fit on the household model server's GPU, in three families.
DEFAULT_VISION_MODELS = (
    "qwen3.5:9b",
    "qwen3-vl:8b-instruct",
    "gemma4:12b",
    "minicpm-v4.5:8b",
    "qwen3.6:27b",
    "gemma4:26b-a4b",
)
DEFAULT_OCR_MODEL = "glm-ocr"
LONG_SIDE = raster.VISION_LONG_SIDE

# What one vision call may wait. 8,192 output tokens at about 36 tok/s is about
# 230 s, so a runaway reply ends as out-of-room inside this rather than as a
# timeout (EV2).
VISION_CALL_TIMEOUT_SECONDS = 300.0

# The prompt that is scored is the prompt that ships (N3-9); a change to any of
# these tasks is a new version, and its rows are not compared with the old ones.
TEXT_PROMPT_VERSION = f"text-{lines_stage.GENERIC_PARSER_VERSION}"
OCR_PROMPT_VERSION = "ocr-1"
VISION_PROMPT_VERSION = "vision-box-1"

# glm-ocr's own prompt. It transcribes the receipt and then repeats lines until
# the output cap (measured on the household's model server: 22 distinct lines
# in 112); repeat and presence penalties did not stop it and cost accuracy. So
# the cap is what a 100-line receipt needs, and the loop is cut off afterwards.
# A loop on one token is stopped sooner by Ollama's repeat limit; transcribe()
# then keeps the rows read before it, and the call counts as a runaway.
OCR_TASK = "Text Recognition:"
OCR_NUM_PREDICT = 4096
REPEATED_RUN = 3
VISION_LINES_TASK = (
    f"{lines_stage.LINES_TASK} For each line also give its box: where it is printed on "
    "the page, as [x0, y0, x1, y1] whole numbers from 0 to 1000 (left, top, right and "
    "bottom, in thousandths of the page width and height)."
)


@dataclass(frozen=True)
class Config:
    """One reader configuration: an arm, its model, and the prompt it was scored with."""

    arm: str
    model: str
    prompt_version: str
    long_side: int | None = None
    variant: str = "separate"
    text_model: str | None = None  # the text model arms (a) and (b) end in

    @property
    def key(self) -> str:
        key = f"{self.arm}|{self.model}|{self.prompt_version}|{self.long_side}|{self.variant}"
        # Arm (b)'s reading also depends on the text model it ends in.
        return f"{key}|{self.text_model}" if self.arm == "b" and self.text_model else key


ARMS = ("a", "b", "c")


def default_configs(
    *,
    text_models: Iterable[str],
    ocr_model: str,
    vision_models: Iterable[str],
    arms: Iterable[str] = ARMS,
    think: str = "",
) -> list[Config]:
    """The grid: arm (a) and arm (b) once per text model, arm (c) once per vision model.

    ``think`` is LLM_THINK. It changes what the text model is asked, so it is part
    of arm (a) and arm (b)'s prompt version, and rows with another setting are not
    the same configuration.
    """
    wanted, texts = set(arms), list(text_models)
    suffix = f"+think={think}" if think else ""
    configs = []
    if "a" in wanted:
        configs += [Config("a", t, TEXT_PROMPT_VERSION + suffix, text_model=t) for t in texts]
    if "b" in wanted:
        configs += [
            Config("b", ocr_model, OCR_PROMPT_VERSION + suffix, LONG_SIDE, text_model=t)
            for t in texts
        ]
    if "c" in wanted:
        configs += [Config("c", m, VISION_PROMPT_VERSION, LONG_SIDE) for m in vision_models]
    return configs


def label(row: dict[str, Any]) -> str:
    """A configuration's name in the summary: arm (b) names the text model it ends in,
    and a text arm names its LLM_THINK setting."""
    text_model = row.get("text_model")
    name = f"{row['model']}→{text_model}" if row["arm"] == "b" and text_model else row["model"]
    think = (row.get("prompt_version") or "").partition("+think=")[2]
    return f"{name} think={think}" if think else name


# --- which receipts, and the truth -------------------------------------------------


@dataclass(frozen=True)
class Receipt:
    """One receipt to read, and what a person committed for it.

    ``item_amounts`` and ``vendor_id`` are empty for a receipt known only from
    ``expected.csv``; it then scores reconcile and item count, not line amounts
    or aliases.
    """

    document_id: uuid.UUID
    image_path: str
    mime: str
    client_ocr_text: str | None
    expected_total: Decimal
    expected_items: int
    item_amounts: tuple[Decimal, ...] = ()
    vendor_id: uuid.UUID | None = None
    purchase_created_at: datetime | None = None


@dataclass
class Selection:
    receipts: list[Receipt]
    excluded: dict[str, int] = field(default_factory=dict)
    # vendor id -> (normalized wording, when the alias was created)
    aliases: dict[uuid.UUID, list[tuple[str, datetime]]] = field(default_factory=dict)

    @property
    def set_hash(self) -> str:
        ids = sorted(str(r.document_id) for r in self.receipts)
        return hashlib.sha256("\n".join(ids).encode()).hexdigest()


_COMMITTED = text(
    """
    WITH hdr AS (
      SELECT DISTINCT ON (j.purchase_id) j.purchase_id, r.output
      FROM ingest_job j
      JOIN ingest_stage_result r ON r.job_id = j.id AND r.stage = 'header'
      WHERE j.purchase_id IS NOT NULL
      ORDER BY j.purchase_id, r.created_at DESC, r.id DESC
    )
    SELECT p.id AS purchase_id, p.total, p.flags, p.created_at,
           vl.vendor_id, d.id AS document_id, d.image_path, d.mime, d.client_ocr_text,
           (h.output -> 'flags' ? 'total_from_text'
              AND (h.output ->> 'total')::numeric = p.total) AS unedited_text_total
    FROM purchase p
    JOIN receipt_document d ON d.id = p.receipt_document_id
    LEFT JOIN vendor_location vl ON vl.id = p.vendor_location_id
    LEFT JOIN hdr h ON h.purchase_id = p.id
    WHERE p.status = 'committed'
    ORDER BY p.created_at, p.id
    """
)
_ITEM_AMOUNTS = text(
    """
    SELECT purchase_id, line_total FROM purchase_line
    WHERE purchase_id = ANY(:ids) AND line_kind = 'item' AND removed_at IS NULL
    ORDER BY purchase_id, seq
    """
)
_DOCUMENTS = text(
    "SELECT id, image_path, mime, client_ocr_text FROM receipt_document WHERE id = ANY(:ids)"
)
_ALIASES = text(
    "SELECT vendor_id, raw_text_norm, created_at FROM receipt_alias WHERE vendor_id = ANY(:ids)"
)


def read_expected_csv(path: Path) -> list[tuple[uuid.UUID, Decimal, int]]:
    """``document_id,total,item_line_count`` rows for receipts never committed."""
    rows = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                (
                    uuid.UUID(row["document_id"].strip()),
                    Decimal(row["total"].strip()),
                    int(row["item_line_count"].strip()),
                )
            )
    return rows


async def select_receipts(db: AsyncSession, expected_csv: Path | None = None) -> Selection:
    """The receipts to score, the exclusions by reason, and the vendors' aliases.

    SELECT only, in a read-only transaction the caller rolls back. Scored: every
    committed purchase with a receipt, except those whose total is not a
    person's (VX1, OV5): ``total_missing`` (computed, not printed), still
    ``reconcile_mismatch``, or a ``total_from_text`` total nobody edited.
    """
    await db.execute(text("SET TRANSACTION READ ONLY"))
    excluded = Counter({"total_missing": 0, "reconcile_mismatch": 0, "unedited_text_total": 0})
    kept = []
    for row in (await db.execute(_COMMITTED)).mappings():
        if "total_missing" in row["flags"]:
            excluded["total_missing"] += 1
        elif "reconcile_mismatch" in row["flags"]:
            excluded["reconcile_mismatch"] += 1
        elif row["unedited_text_total"]:
            excluded["unedited_text_total"] += 1
        else:
            kept.append(row)
    amounts: dict[uuid.UUID, list[Decimal]] = {row["purchase_id"]: [] for row in kept}
    if kept:
        for row in (await db.execute(_ITEM_AMOUNTS, {"ids": list(amounts)})).mappings():
            amounts[row["purchase_id"]].append(row["line_total"])
    receipts = [
        Receipt(
            document_id=row["document_id"],
            image_path=row["image_path"],
            mime=row["mime"],
            client_ocr_text=row["client_ocr_text"],
            expected_total=row["total"],
            expected_items=len(amounts[row["purchase_id"]]),
            item_amounts=tuple(amounts[row["purchase_id"]]),
            vendor_id=row["vendor_id"],
            purchase_created_at=row["created_at"],
        )
        for row in kept
    ]
    if expected_csv is not None:
        receipts.extend(await _expected_receipts(db, expected_csv, receipts, excluded))
    vendor_ids = sorted({r.vendor_id for r in receipts if r.vendor_id is not None})
    aliases: dict[uuid.UUID, list[tuple[str, datetime]]] = {v: [] for v in vendor_ids}
    if vendor_ids:
        for row in (await db.execute(_ALIASES, {"ids": vendor_ids})).mappings():
            aliases[row["vendor_id"]].append((row["raw_text_norm"], row["created_at"]))
    return Selection(receipts, dict(excluded), aliases)


async def _expected_receipts(
    db: AsyncSession, path: Path, committed: list[Receipt], excluded: Counter[str]
) -> list[Receipt]:
    rows = read_expected_csv(path)
    already = {r.document_id for r in committed}
    wanted = [row for row in rows if row[0] not in already]
    excluded["expected_csv_already_committed"] = len(rows) - len(wanted)
    found = {
        row["id"]: row
        for row in (await db.execute(_DOCUMENTS, {"ids": [row[0] for row in wanted]})).mappings()
    }
    excluded["expected_csv_unknown_document"] = sum(1 for row in wanted if row[0] not in found)
    return [
        Receipt(
            document_id=doc_id,
            image_path=found[doc_id]["image_path"],
            mime=found[doc_id]["mime"],
            client_ocr_text=found[doc_id]["client_ocr_text"],
            expected_total=total,
            expected_items=items,
        )
        for doc_id, total, items in wanted
        if doc_id in found
    ]


async def worker_running(db: AsyncSession) -> bool:
    """Whether this deployment's worker is connected. It would swap models under the
    benchmark. Only this database's: another deployment on the same server (a test
    database beside a household one) has its own worker."""
    row = await db.execute(
        text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE application_name = :name AND pid <> pg_backend_pid() "
            "AND datname = current_database()"
        ),
        {"name": WORKER_APPLICATION_NAME},
    )
    return bool(row.scalar_one())


# --- reading one receipt -----------------------------------------------------------


class ConfigSkipped(Exception):
    """The configuration cannot run at all: its model is missing or text-only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Reading:
    """What one configuration made of one receipt, before scoring."""

    lines: list[lines_stage.ParsedLine] | None = None
    header: ReceiptHeader | None = None
    error: str | None = None
    boxes_valid: int = 0
    boxes_total: int = 0
    pages_truncated: bool = False
    ledger: CallLedger = field(default_factory=CallLedger)


def _path(receipt: Receipt) -> Path:
    return Path(get_settings().receipts_path) / receipt.image_path


def _converter(receipt: Receipt) -> str | None:
    fmt = BY_MIME.get(receipt.mime)
    return None if fmt is None else fmt.converter


async def ocr_text(receipt: Receipt) -> str | None:
    """Today's OCR for the receipt: client text when present, else Tesseract."""
    document = ReceiptDocument(
        image_path=receipt.image_path,
        mime=receipt.mime,
        client_ocr_text=receipt.client_ocr_text,
    )
    try:
        _, found, _ = await run_ocr(document, _path(receipt))
    except StageFailure:
        return None
    return found


def drop_repeated_tail(transcript: str, run: int = REPEATED_RUN) -> str:
    """The transcript up to the first run of ``run`` lines it has already printed.

    A receipt can print one line twice (two of the same item), but not the same
    three lines in the same order twice; that is the OCR model looping.
    """
    lines = transcript.splitlines()
    seen: set[tuple[str, ...]] = set()
    for i in range(len(lines) - run + 1):
        key = tuple(line.strip() for line in lines[i : i + run])
        if not all(key):
            continue
        if key in seen:
            return "\n".join(lines[:i])
        seen.add(key)
    return transcript


def _post_passes(
    result: Any, receipt_text: str = "", header: ReceiptHeader | None = None
) -> list[lines_stage.ParsedLine]:
    """The lines stage's deterministic passes, exactly as the pipeline runs them."""
    return structure.post_passes(
        result,
        receipt_text,
        None if header is None else header.total,
        None if header is None else header.tax,
    ).lines


async def _read_text(client: LlmClient, receipt_text: str, reading: Reading) -> None:
    """Arm (a)'s reading of a receipt text: today's readers and post-passes."""
    header, _ = await readers.read_header(
        client, receipt_text, deadline_at=readers.stage_deadline_at()
    )
    if header is not None:
        header, _ = readers.apply_printed_total(header, receipt_text)
    reading.header = header
    lines = await readers.read_lines(client, receipt_text, deadline_at=readers.stage_deadline_at())
    if lines.lines is None:
        reading.error = lines.reason or "lines_unparsed"
        return
    reading.lines = _post_passes(lines.lines, receipt_text, header)
    if lines.unread_parts:
        reading.error = "lines_partial"


async def _read_vision(client: LlmClient, image: bytes, reading: Reading) -> None:
    """Arm (c): header and lines from the image, one call each (the "separate" variant)."""
    common: dict[str, Any] = {
        "images": [image],
        "think": False,
        "timeout_seconds": VISION_CALL_TIMEOUT_SECONDS,
    }
    try:
        reading.header, _ = await client.extract(
            ReceiptHeader,
            header_stage.HEADER_TASK,
            "",
            deadline_seconds=lines_stage.lines_deadline_seconds(),
            retry=VISION_RETRY,
            **common,
        )
    except InvalidModelOutput:
        reading.header = None
    deadline = lines_stage.lines_deadline_seconds()
    started = time.monotonic()
    answer, _ = await client.extract(
        VisionReceiptLines,
        VISION_LINES_TASK,
        "",
        deadline_seconds=deadline,
        retry=VISION_RETRY,
        **common,
    )
    if not any(line.line_kind == "item" for line in answer.lines):
        # A valid reply with no items is no reading (GAP-2). Ask once more a
        # little differently, as for an empty text part (#60).
        answer, _ = await client.extract(
            VisionReceiptLines,
            VISION_LINES_TASK,
            "",
            deadline_seconds=max(deadline - (time.monotonic() - started), 0.001),
            temperature=llm.EMPTY_PART_RETRY_TEMPERATURE,
            retry=RetryPolicy(retries=0),
            **common,
        )
        if not any(line.line_kind == "item" for line in answer.lines):
            reading.error = "empty_reading"
            return
    reading.boxes_total = len(answer.lines)
    reading.boxes_valid = sum(1 for line in answer.lines if valid_box(line.box))
    reading.lines = _post_passes(answer.without_boxes(), header=reading.header)


async def read_receipt(
    config: Config, receipt: Receipt, ocr: str | None, *, base_url: str | None = None
) -> Reading:
    """One configuration's reading of one receipt. Model failures become ``error``.

    Raises :class:`ConfigSkipped` for a model that is not there or cannot take
    images, and :class:`ModelUnavailable` when the server itself is down: both
    stop more than this one reading.
    """
    reading = Reading()

    def client(model: str, role: str) -> LlmClient:
        return LlmClient(base_url=base_url, model=model, ledger=reading.ledger, role=role)

    try:
        if config.arm == "a":
            if not ocr:
                reading.error = "no_ocr_text"
                return reading
            await _read_text(client(config.model, "text"), ocr, reading)
        else:
            image = raster.vision_png(
                _path(receipt), _converter(receipt), config.long_side or LONG_SIDE
            )
            reading.pages_truncated = image.pages_truncated
            if config.arm == "b":
                transcript = await client(config.model, "ocr").transcribe(
                    [image.png],
                    OCR_TASK,
                    timeout_seconds=VISION_CALL_TIMEOUT_SECONDS,
                    num_predict=OCR_NUM_PREDICT,
                )
                transcript = drop_repeated_tail(transcript)
                if not transcript.strip():
                    reading.error = "empty_transcript"
                    return reading
                await _read_text(
                    client(config.text_model or config.model, "text"), transcript, reading
                )
            else:
                await _read_vision(client(config.model, "primary"), image.png, reading)
    except ModelMissing as exc:
        raise ConfigSkipped(exc.reason) from None
    except StageFailure as exc:
        reading.error = exc.code
    except InvalidModelOutput as exc:
        reading.error = exc.code
    except ModelTimeout:
        reading.error = ModelTimeout.code
    return reading


# --- scoring -----------------------------------------------------------------------

RECONCILE_TOLERANCE = lines_stage.RECONCILE_TOLERANCE
RUNAWAY_OUTCOMES = {"timeout", "out_of_room"}


def _runaway(call: llm.CallRecord) -> bool:
    """A call that ran out of time or room. The OCR model always fills its cap
    (its loop is cut off afterwards), so only its timeouts count."""
    if call.role == "ocr":
        return call.outcome == "timeout"
    return call.outcome in RUNAWAY_OUTCOMES


def _multiset_matches(found: Iterable[Decimal], expected: Iterable[Decimal]) -> int:
    return sum((Counter(found) & Counter(expected)).values())


def score(
    config: Config,
    receipt: Receipt,
    reading: Reading,
    ocr: str | None,
    aliases: dict[uuid.UUID, list[tuple[str, datetime]]],
) -> dict[str, Any]:
    """One reading's scores. Numbers and ids only: this goes to ``readings.jsonl``."""
    lines = reading.lines or []
    items = [line for line in lines if line.line_kind == "item"]
    header_tax = None if reading.header is None else reading.header.tax
    reconciled = False
    if items:
        result = lines_stage.reconcile(lines, receipt.expected_total, header_tax)
        reconciled = not result["mismatch"]
    header_total = None if reading.header is None else reading.header.total
    calls = reading.ledger.calls
    out: dict[str, Any] = {
        "config": config.key,
        "document_id": str(receipt.document_id),
        "error": reading.error,
        "reconciled": reconciled,
        "header_total_exact": header_total is not None and header_total == receipt.expected_total,
        "item_count": len(items),
        "item_count_diff": abs(len(items) - receipt.expected_items),
        "runaway": any(_runaway(call) for call in calls),
        "seconds": round(sum(call.seconds for call in calls), 3),
        "completion_tokens": sum(call.completion_tokens or 0 for call in calls),
        "load_seconds": round(sum(call.load_seconds or 0 for call in calls), 3),
        "calls": len(calls),
        "boxes_valid": reading.boxes_valid,
        "boxes_total": reading.boxes_total,
        "pages_truncated": reading.pages_truncated,
    }
    amounts = [line.line_total for line in items]
    if receipt.item_amounts:
        out["amounts_expected"] = len(receipt.item_amounts)
        out["amounts_exact"] = _multiset_matches(amounts, receipt.item_amounts)
    if receipt.vendor_id is not None and receipt.purchase_created_at is not None:
        # Only aliases that existed before this receipt was drafted (N2-7).
        known = {
            wording
            for wording, created in aliases.get(receipt.vendor_id, [])
            if created < receipt.purchase_created_at
        }
        out["alias_lines"] = len(items)
        out["alias_hits"] = sum(
            1 for line in items if normalize_receipt_text(line.raw_text or "") in known
        )
    if ocr and receipt.item_amounts:
        # Witness quality on the lines this reader got right.
        right = list((Counter(amounts) & Counter(receipt.item_amounts)).elements())
        out["witness_lines"] = len(right)
        out["witness_exact"] = sum(witness_amounts(right, ocr, digit_runs=False))
        out["witness_tolerant"] = sum(witness_amounts(right, ocr, digit_runs=True))
        perturbed = [
            wrong
            for amount in right
            for wrong in (amount + Decimal("0.01"), amount - Decimal("0.01"), amount * 10)
            if wrong > 0 and wrong not in receipt.item_amounts
        ]
        out["false_probes"] = len(perturbed)
        out["false_supported"] = sum(any_support(w, ocr, digit_runs=True) for w in perturbed)
    return out


# --- aggregates --------------------------------------------------------------------

CSV_COLUMNS = [
    "run_id",
    "started_at",
    "receipt_set_hash",
    "arm",
    "model",
    "second_model",
    "model_digest",
    "prompt_version",
    "long_side",
    "variant",
    "n",
    "reconcile_share",
    "consensus_share",
    "runaway_share",
    "header_total_exact",
    "item_count_mad",
    "alias_hit_rate",
    "ocr_support",
    "ocr_support_tolerant",
    "false_support",
    "vlm_agreement",
    "box_valid_share",
    "box_iou50_share",
    "seconds_p50",
    "seconds_max",
    "tokens_p50",
    "load_seconds_p50",
    "model_tag",
    "line_amount_exact_share",
    "wrong_preselection_count",
    "ci80_low",
    "ci80_high",
    "text_model",
]

# z for a two-sided 80% interval.
_Z80 = 1.2815515655446004


def wilson80(successes: int, n: int) -> tuple[float, float]:
    """The 80% Wilson interval for a binomial share (OV4)."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    z2 = _Z80**2
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = _Z80 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _share(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _sum(rows: list[dict[str, Any]], key: str) -> int:
    return sum(row.get(key, 0) for row in rows)


def aggregate(
    config: Config,
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    started_at: str,
    set_hash: str,
    digest: str,
) -> dict[str, Any]:
    """One ``reading.csv`` row for a configuration's readings."""
    n = len(rows)
    reconciled = sum(1 for row in rows if row["reconciled"])
    low, high = wilson80(reconciled, n)
    seconds = [row["seconds"] for row in rows]
    out: dict[str, Any] = dict.fromkeys(CSV_COLUMNS)
    out.update(
        run_id=run_id,
        started_at=started_at,
        receipt_set_hash=set_hash,
        arm=config.arm,
        model=config.model,
        model_digest=digest,
        model_tag=config.model,
        text_model=config.text_model,
        prompt_version=config.prompt_version,
        long_side=config.long_side,
        variant=config.variant,
        n=n,
        reconcile_share=_share(reconciled, n),
        runaway_share=_share(sum(1 for row in rows if row["runaway"]), n),
        header_total_exact=_share(sum(1 for row in rows if row["header_total_exact"]), n),
        item_count_mad=None if n == 0 else statistics.fmean(row["item_count_diff"] for row in rows),
        alias_hit_rate=_share(_sum(rows, "alias_hits"), _sum(rows, "alias_lines")),
        line_amount_exact_share=_share(_sum(rows, "amounts_exact"), _sum(rows, "amounts_expected")),
        ocr_support=_share(_sum(rows, "witness_exact"), _sum(rows, "witness_lines")),
        ocr_support_tolerant=_share(_sum(rows, "witness_tolerant"), _sum(rows, "witness_lines")),
        false_support=_share(_sum(rows, "false_supported"), _sum(rows, "false_probes")),
        box_valid_share=_share(_sum(rows, "boxes_valid"), _sum(rows, "boxes_total")),
        seconds_p50=None if n == 0 else statistics.median(seconds),
        seconds_max=None if n == 0 else max(seconds),
        tokens_p50=None if n == 0 else statistics.median(row["completion_tokens"] for row in rows),
        load_seconds_p50=None if n == 0 else statistics.median(row["load_seconds"] for row in rows),
        ci80_low=low,
        ci80_high=high,
    )
    return out


# --- the decision rule -------------------------------------------------------------

RECONCILE_BAR = 0.75
ALIAS_SLACK = 0.05
CONSENSUS_TRIGGER = 0.2


def margin(n: int) -> float:
    """A difference counts only beyond the larger of 3 receipts and 1/n (OV4).

    In shares, 3 receipts is 3/n, which is never smaller than 1/n; the second
    term is kept as written in the design.
    """
    return 0.0 if n == 0 else max(3 / n, 1 / n)


def _best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """An arm's configuration: highest reconcile share, then alias hits, then speed."""
    if not rows:
        return None
    return max(
        rows,
        key=lambda r: (
            r["reconcile_share"] or 0.0,
            r["alias_hit_rate"] if r["alias_hit_rate"] is not None else -1.0,
            -(r["seconds_p50"] or 0.0),
        ),
    )


@dataclass
class Decision:
    best: dict[str, dict[str, Any] | None]
    passed: dict[str, list[str]]
    reader: str
    consensus_step: bool
    notes: list[str]


def decide(rows: list[dict[str, Any]], n: int) -> Decision:
    """Steps 1–4 of the decision rule (R2-4, VD1, VD9, VD10, OV1), and OV3's trigger."""
    best = {arm: _best([r for r in rows if r["arm"] == arm]) for arm in ("a", "b", "c")}
    m = margin(n)
    a = best["a"]
    a_share = (a or {}).get("reconcile_share") or 0.0
    a_alias = (a or {}).get("alias_hit_rate")
    passed: dict[str, list[str]] = {}
    notes: list[str] = [f"margin: {m:.3f} (the larger of 3 receipts and 1/n, n={n})"]
    for arm in ("b", "c"):
        row = best[arm]
        if row is None:
            continue
        share = row["reconcile_share"] or 0.0
        failures = []
        if share < RECONCILE_BAR:
            failures.append(f"reconcile share {share:.2f} under {RECONCILE_BAR:.2f}")
        if a is None:
            failures.append("no arm (a) to compare with")
        elif share - a_share <= m:
            failures.append(f"beats (a) by {share - a_share:+.3f}, not more than the margin")
        alias = row["alias_hit_rate"]
        if a_alias is not None and alias is not None and alias < a_alias - ALIAS_SLACK:
            failures.append(f"alias hit rate {alias:.2f} more than 5 points under (a)'s")
        if failures:
            notes.append(f"({arm}) {label(row)} dropped: " + "; ".join(failures))
        else:
            passed[arm] = [label(row)]
    if not passed:
        reader = "neither"
    elif len(passed) == 1:
        reader = next(iter(passed))
    else:
        b_share = best["b"]["reconcile_share"] or 0.0  # type: ignore[index]
        c_share = best["c"]["reconcile_share"] or 0.0  # type: ignore[index]
        reader = "b" if b_share - c_share > m else "c"
    consensus_step = reader == "c" and 1 - (best["c"]["reconcile_share"] or 0.0) > CONSENSUS_TRIGGER  # type: ignore[index]
    return Decision(best, passed, reader, consensus_step, notes)


# --- running -----------------------------------------------------------------------


@dataclass
class RunOptions:
    out_dir: Path
    configs: list[Config]
    expected_csv: Path | None = None
    resume: str | None = None
    base_url: str | None = None
    compare_with_stored: bool = False


# Seconds per receipt assumed for an estimate before a configuration has a p50 of
# its own in reading.csv (EV9's measured speeds, a 35-line receipt).
DEFAULT_SECONDS = {"a": 120.0, "b": 130.0, "c": 150.0}


def estimate_seconds(configs: list[Config], n: int, history: list[dict[str, str]]) -> float:
    """Configurations × n × p50 seconds, from earlier runs where there are any."""
    total = 0.0
    for config in configs:
        p50s = [
            float(row["seconds_p50"])
            for row in history
            if row.get("arm") == config.arm
            and row.get("model") == config.model
            and row.get("seconds_p50")
        ]
        total += n * (p50s[-1] if p50s else DEFAULT_SECONDS[config.arm])
    return total


def read_history(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.is_file():
        return []
    with csv_path.open(newline="") as handle:
        return list(csv.DictReader(handle))


async def model_digests(base_url: str | None) -> dict[str, str]:
    """Each installed model's digest, so a re-pulled tag shows as a new row. Best effort."""
    url = f"{(base_url or get_settings().ollama_base_url).rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(transport=llm.http_transport, timeout=10) as client:
            body = (await client.get(url)).json()
        return {m["name"]: m.get("digest", "") for m in body.get("models", [])}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return {}


class RunLog:
    """Progress lines to the run's log.txt and to the console: counts, never text."""

    def __init__(self, path: Path, echo: Callable[[str], None] = print) -> None:
        self.path = path
        self.echo = echo

    def __call__(self, message: str) -> None:
        line = f"{datetime.now(UTC).strftime('%H:%M:%S')} {message}"
        with self.path.open("a") as handle:
            handle.write(line + "\n")
        self.echo(line)


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    rows: list[dict[str, Any]]
    decision: Decision
    skipped: dict[str, str]
    summary: str


async def run(
    selection: Selection, options: RunOptions, *, echo: Callable[[str], None] = print
) -> RunResult:
    """Read every receipt with every configuration, checkpointing each reading."""
    root = options.out_dir
    runs = root / "reading-runs"
    if options.resume:
        run_id = options.resume
        run_dir = runs / run_id
        meta = json.loads((run_dir / "run.json").read_text())
        configs = [Config(**c) for c in meta["configs"]]
        wanted = set(meta["document_ids"])
        receipts = [r for r in selection.receipts if str(r.document_id) in wanted]
        started_at = meta["started_at"]
    else:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = runs / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        configs = options.configs
        receipts = selection.receipts
        started_at = datetime.now(UTC).isoformat()
        meta = {
            "run_id": run_id,
            "started_at": started_at,
            "receipt_set_hash": selection.set_hash,
            "document_ids": [str(r.document_id) for r in receipts],
            "configs": [asdict(c) for c in configs],
            "excluded": selection.excluded,
        }
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    set_hash = meta["receipt_set_hash"]
    log = RunLog(run_dir / "log.txt", echo)
    checkpoint = run_dir / "readings.jsonl"
    done: dict[str, list[dict[str, Any]]] = {}
    skipped: dict[str, str] = dict(meta.get("skipped", {}))
    if checkpoint.is_file():
        for line in checkpoint.read_text().splitlines():
            row = json.loads(line)
            done.setdefault(row["config"], []).append(row)
    history = read_history(root / "reading.csv")
    remaining = [
        c for c in configs if c.key not in skipped and len(done.get(c.key, [])) < len(receipts)
    ]
    estimate = estimate_seconds(remaining, len(receipts), history)
    log(
        f"run {run_id}: n={len(receipts)} receipts, {len(configs)} configurations, "
        f"excluded {selection.excluded}; estimated {estimate / 3600:.1f} h"
    )
    if len(receipts) < len(set(meta["document_ids"])):
        log(f"{len(set(meta['document_ids'])) - len(receipts)} receipt(s) are no longer eligible")

    ocr_cache: dict[uuid.UUID, str | None] = {}
    for config in configs:
        if config.key in skipped:
            continue
        finished = {row["document_id"] for row in done.get(config.key, [])}
        todo = [r for r in receipts if str(r.document_id) not in finished]
        if todo:
            log(f"({config.arm}) {config.model}: {len(todo)} to read")
        for receipt in todo:
            if receipt.document_id not in ocr_cache:
                ocr_cache[receipt.document_id] = await ocr_text(receipt)
            ocr = ocr_cache[receipt.document_id]
            try:
                reading = await read_receipt(config, receipt, ocr, base_url=options.base_url)
            except ConfigSkipped as exc:
                skipped[config.key] = exc.reason
                meta["skipped"] = skipped
                (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
                log(f"({config.arm}) {config.model}: skipped ({exc.reason})")
                break
            except ModelUnavailable as exc:
                log(f"model server unavailable ({exc.detail}); resume with --resume {run_id}")
                raise
            row = score(config, receipt, reading, ocr, selection.aliases)
            with checkpoint.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            done.setdefault(config.key, []).append(row)
            log(
                f"({config.arm}) {config.model}: {len(done[config.key])}/{len(receipts)} "
                f"reconciled={row['reconciled']} {row['seconds']:.0f}s"
                + (f" error={row['error']}" if row["error"] else "")
            )

    digests = await model_digests(options.base_url)
    rows = [
        aggregate(
            config,
            [
                r
                for r in done.get(config.key, [])
                if r["document_id"] in {str(x.document_id) for x in receipts}
            ],
            run_id=run_id,
            started_at=started_at,
            set_hash=set_hash,
            digest=digests.get(config.model, ""),
        )
        for config in configs
        if config.key not in skipped
    ]
    compared = rows + (_stored_rows(history, set_hash, rows) if options.compare_with_stored else [])
    decision = decide(compared, len(receipts))
    summary = render_summary(
        run_id,
        set_hash,
        selection.excluded,
        rows,
        compared,
        decision,
        skipped,
        history,
        len(receipts),
    )
    (run_dir / "summary.txt").write_text(summary)
    if not meta.get("written"):
        _append_csv(root / "reading.csv", rows)
        meta["written"] = True
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    log(f"done: {run_dir / 'summary.txt'}")
    return RunResult(run_id, run_dir, rows, decision, skipped, summary)


def _stored_rows(
    history: list[dict[str, str]], set_hash: str, current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The latest stored row per configuration on the same receipts, for --models and --arms."""

    def config_of(row: dict[str, Any]) -> tuple[str, str, str, str]:
        # Rows written before the text_model column have none; for arm (a) it is the model.
        text_model = (row.get("text_model") or "") if row["arm"] == "b" else ""
        return (row["arm"], row["model"], row["prompt_version"], text_model)

    have = {config_of(r) for r in current}
    latest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in history:
        if row.get("receipt_set_hash") != set_hash:
            continue
        key = config_of(row)
        if key in have:
            continue

        def num(value: str | None) -> float | None:
            return float(value) if value not in (None, "") else None

        latest[key] = {
            **row,
            "reconcile_share": num(row.get("reconcile_share")),
            "alias_hit_rate": num(row.get("alias_hit_rate")),
            "seconds_p50": num(row.get("seconds_p50")),
        }
    return list(latest.values())


def _append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.is_file():
        with path.open(newline="") as handle:
            header = next(csv.reader(handle), None)
        if header != CSV_COLUMNS:
            # Written before a column was added: rewrite it under today's header, the
            # old rows with the new columns empty. Beside it first, so a run stopped
            # halfway never loses the stored history.
            old = read_history(path)
            rewritten = path.with_name(path.name + ".tmp")
            with rewritten.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows({k: row.get(k) or "" for k in CSV_COLUMNS} for row in old)
            rewritten.replace(path)
    new = not path.is_file()
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if new:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row.get(k) is None else row[k] for k in CSV_COLUMNS})


def _fmt(value: Any, pct: bool = True) -> str:
    if value is None:
        return "-"
    if pct:
        return f"{float(value) * 100:.0f}%"
    return f"{float(value):.0f}"


SUMMARY_HEADER = (
    "arm model                            reconcile [80% CI]    runaway total  items±  alias  "
    + "amounts witness/tol/false  boxes  s p50/max  tok p50"
)


def _summary_row(row: dict[str, Any]) -> str:
    mad = "-" if row["item_count_mad"] is None else f"{row['item_count_mad']:.1f}"
    witness = "/".join(
        _fmt(row[k]) for k in ("ocr_support", "ocr_support_tolerant", "false_support")
    )
    return (
        f"({row['arm']}) {label(row)[:32]:<32} "
        f"{_fmt(row['reconcile_share']):>5} [{_fmt(row['ci80_low'])}–{_fmt(row['ci80_high'])}]"
        f"  {_fmt(row['runaway_share']):>7} {_fmt(row['header_total_exact']):>6}  {mad:>5}"
        f"  {_fmt(row['alias_hit_rate']):>5}  {_fmt(row['line_amount_exact_share']):>7}"
        f"  {witness}  {_fmt(row['box_valid_share']):>5}"
        f"  {_fmt(row['seconds_p50'], False)}/{_fmt(row['seconds_max'], False)}"
        f"  {_fmt(row['tokens_p50'], False)}"
    )


def render_summary(
    run_id: str,
    set_hash: str,
    excluded: dict[str, int],
    rows: list[dict[str, Any]],
    compared: list[dict[str, Any]],
    decision: Decision,
    skipped: dict[str, str],
    history: list[dict[str, str]],
    n: int,
) -> str:
    """The aggregate table and the decision. Aggregates only, never a receipt."""
    out = [
        f"reading benchmark {run_id}",
        f"receipts: n={n} (set {set_hash[:12]}); excluded: "
        + ", ".join(f"{k}={v}" for k, v in excluded.items()),
        "",
        SUMMARY_HEADER,
    ]
    out += [_summary_row(row) for row in rows]
    for key, reason in skipped.items():
        arm, model = key.split("|")[:2]
        out.append(f"({arm}) {model}: skipped ({reason})")
    stored = [r for r in compared if r not in rows]
    if stored:
        out.append("")
        out.append("compared with stored rows on the same receipts:")
        for row in stored:
            out.append(f"({row['arm']}) {label(row)}: reconcile {_fmt(row['reconcile_share'])}")
    previous = [
        float(r["reconcile_share"])
        for r in history
        if r.get("receipt_set_hash") == set_hash and r.get("reconcile_share")
    ]
    if previous:
        out.append(f"previous best reconcile share on these receipts: {max(previous) * 100:.0f}%")
    out += ["", "decision:"]
    out += [f"  {note}" for note in decision.notes]
    out.append(
        {
            "neither": "  ship neither; the benchmark stays for the next model",
            "b": "  ship (b), vision as OCR",
            "c": "  ship (c), the vision reader",
        }[decision.reader]
    )
    if decision.consensus_step:
        out.append("  (c) leaves more than 1 in 5 unreconciled: step 2 (consensus) is triggered")
    if n < 10:
        out.append(f"  caution: n={n} is too few receipts for the margin to mean much")
    out += [
        "",
        "notes:",
        "  alias hits count only aliases created before the receipt was drafted; an alias",
        "  rewritten in place keeps its first created_at, so it may count as older (N2-7).",
        "  line amounts are matched as a multiset of item amounts against the committed lines.",
        "  box_iou50_share, consensus and second-model columns are for synthetic runs and step 2.",
    ]
    return "\n".join(out) + "\n"
