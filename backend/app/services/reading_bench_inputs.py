"""What ``kerp reading-benchmark`` measures (spec 04, 2J): the grid of reader
configurations, and the committed receipts with the truth each is scored against.

The selection is read in one read-only transaction; ``services/reading_benchmark``
describes the run.
"""

from __future__ import annotations

import csv
import hashlib
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import WORKER_APPLICATION_NAME
from app.ingest import lines as lines_stage
from app.ingest import raster

# --- the grid --------------------------------------------------------------------

# Arm (c)'s screening grid (MS4, 2026-10-01): two quality-tier models and four
# that fit on the household model server's GPU, in three families.


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
    or aliases. ``expected_items`` is None when that file leaves the count blank
    (a corpus that lists only some of each receipt's items): no item count then.
    """

    document_id: uuid.UUID
    image_path: str
    mime: str
    client_ocr_text: str | None
    expected_total: Decimal
    expected_items: int | None
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
           d.created_at AS uploaded_at,
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
    "SELECT id, image_path, mime, client_ocr_text, created_at AS uploaded_at"
    " FROM receipt_document WHERE id = ANY(:ids)"
)
_ALIASES = text(
    "SELECT vendor_id, raw_text_norm, created_at FROM receipt_alias WHERE vendor_id = ANY(:ids)"
)


def read_expected_csv(path: Path) -> list[tuple[uuid.UUID, Decimal, int | None]]:
    """``document_id,total,item_line_count`` rows for receipts never committed.

    A blank item_line_count means the count is not known.
    """
    rows = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                (
                    uuid.UUID(row["document_id"].strip()),
                    Decimal(row["total"].strip()),
                    int(count) if (count := (row["item_line_count"] or "").strip()) else None,
                )
            )
    return rows


async def select_receipts(
    db: AsyncSession, expected_csv: Path | None = None, uploaded_since: datetime | None = None
) -> Selection:
    """The receipts to score, the exclusions by reason, and the vendors' aliases.

    SELECT only, in a read-only transaction the caller rolls back. Scored: every
    committed purchase with a receipt, except those whose total is not a
    person's (VX1, OV5): ``total_missing`` (computed, not printed), still
    ``reconcile_mismatch``, or a ``total_from_text`` total nobody edited. With
    ``uploaded_since``, only receipts uploaded at or after it: a hold-out batch
    the configuration was not chosen on (04, 2O); the rest count as
    ``uploaded_before``.
    """
    await db.execute(text("SET TRANSACTION READ ONLY"))
    excluded = Counter({"total_missing": 0, "reconcile_mismatch": 0, "unedited_text_total": 0})
    if uploaded_since is not None:
        excluded["uploaded_before"] = 0
    kept = []
    for row in (await db.execute(_COMMITTED)).mappings():
        if uploaded_since is not None and row["uploaded_at"] < uploaded_since:
            excluded["uploaded_before"] += 1
        elif "total_missing" in row["flags"]:
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
        receipts.extend(
            await _expected_receipts(db, expected_csv, receipts, excluded, uploaded_since)
        )
    vendor_ids = sorted({r.vendor_id for r in receipts if r.vendor_id is not None})
    aliases: dict[uuid.UUID, list[tuple[str, datetime]]] = {v: [] for v in vendor_ids}
    if vendor_ids:
        for row in (await db.execute(_ALIASES, {"ids": vendor_ids})).mappings():
            aliases[row["vendor_id"]].append((row["raw_text_norm"], row["created_at"]))
    return Selection(receipts, dict(excluded), aliases)


async def _expected_receipts(
    db: AsyncSession,
    path: Path,
    committed: list[Receipt],
    excluded: Counter[str],
    uploaded_since: datetime | None = None,
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
    if uploaded_since is not None:
        before = {d for d, row in found.items() if row["uploaded_at"] < uploaded_since}
        excluded["uploaded_before"] += len(before)
        found = {d: row for d, row in found.items() if d not in before}
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
