"""Lines stage: receipt body to structured purchase lines, plus reconciliation.

Parsing is tiered: a registered vendor parser (:mod:`app.ingest.parsers`),
then the generic language-model parser constrained to :class:`ReceiptLines`.
Model output is refined deterministically: units are normalized through the
unit table, weighed ("2.31 lb @ 3.99/lb") and counted ("2 @ 1.99") patterns
fill quantities the model left empty, and discounts and deposits attach to the
item printed directly above them when the model gave no parent.

Conventions for stored lines: ``line_total`` is always the positive magnitude
printed on the line. A discount is subtracted by virtue of its kind, so the
observation price of an item is ``item.line_total - sum(attached discounts)``.

Re-running the stage on a job that already has a draft purchase deletes that
draft's lines and recreates them, and rewrites the header fields; the purchase
row and its id are kept, so ``ingest_job.purchase_id`` stays valid. A purchase
that is no longer a draft is never touched (``purchase_not_draft``).

Stage output shape::

    {"parsed": true, "parser": "llm-generic", "parser_version": "1", "model_attempts": 1,
     "purchase_id": "...", "line_count": 9,
     "lines": [{"seq": 1, "purchase_line_id": "...", "raw_text": "...",
                "line_kind": "item", "qty": "2.31", "unit": "lb", "unit_price": "3.99",
                "line_total": "9.22", "parent_seq": null, "flags": []}],
     "reconciliation": {"checked": true, "items": "..", "discounts": "..", "tax": "..",
                        "tax_source": "lines" | "header" | "none", "deposits": "..",
                        "fees": "..", "computed_total": "..", "printed_total": "..",
                        "difference": "0.00", "mismatch": false},
     "purchase_flags": ["reconcile_mismatch", ...]}

When nothing parses: ``{"parsed": false, "reason": "invalid_model_output",
"model_attempts": 3, "purchase_id": "...", "line_count": 0, ...}``; the draft
purchase exists with no lines and the flag ``lines_unparsed``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.ids import new_id
from app.ingest.errors import StageFailure
from app.ingest.schemas import ReceiptLine, ReceiptLines
from app.models import IngestJob, Purchase, PurchaseLine, ReceiptDocument
from app.services.normalize import normalize_receipt_text
from app.units import UnitParseFailure, parse_unit

LINES_TASK = (
    "List every purchased line of this receipt in order. Include item lines, "
    "discounts or savings printed beneath an item, container deposits (CRV, redemption "
    "value, bottle deposit), fees (bag fee, surcharge), and the tax line(s) printed in "
    "the totals block. Exclude the store header, subtotal, total, tender, change, "
    "loyalty summaries, and footer text. Keep raw_text exactly as printed. "
    "Give qty only when the line shows it or is a plain one-of item; when a weight or "
    "count is printed but unreadable, give null rather than 1."
)

GENERIC_PARSER = "llm-generic"
# 2: printed weights and counts override the model's, and assumed quantities are flagged (#31).
GENERIC_PARSER_VERSION = "2"
RECONCILE_TOLERANCE = Decimal("0.02")
CENTS = Decimal("0.01")

WEIGHT_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(lbs?|kg|oz|g)\b\s*@\s*\$?\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)
COUNT_PATTERN = re.compile(r"(?<![\d.])(\d{1,3})\s*@\s*\$?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
# A weight that is printed but did not survive OCR cleanly: "1.24 1b", "0.85 Ib", a
# weight with no "@ price". Enough to know the line is weighed, not enough to read it.
LOOSE_WEIGHT_PATTERN = re.compile(r"(?<![\d.])\d+\.\d+\s*(lbs?|[1i]bs?|kg|oz)\b", re.IGNORECASE)

# Flags that say the quantity is not what a person or the printed line said.
# qty_inferred: read from the printed pattern because the model gave none.
# qty_corrected: the printed pattern disagreed with the model, and the print won.
# qty_assumed: nothing supports the quantity; it is the 1-each default, or the line
#   looks weighed but its weight could not be read.
QTY_FLAGS = frozenset({"qty_inferred", "qty_corrected", "qty_assumed"})


@dataclass
class ParsedLine:
    seq: int
    raw_text: str
    line_kind: str
    qty: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    line_total: Decimal
    parent_seq: int | None = None
    flags: list[str] = field(default_factory=list)
    purchase_line_id: uuid.UUID | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "purchase_line_id": None
            if self.purchase_line_id is None
            else str(self.purchase_line_id),
            "raw_text": self.raw_text,
            "line_kind": self.line_kind,
            "qty": None if self.qty is None else str(self.qty),
            "unit": self.unit,
            "unit_price": None if self.unit_price is None else str(self.unit_price),
            "line_total": str(self.line_total),
            "parent_seq": self.parent_seq,
            "flags": list(self.flags),
        }


def lines_budget_seconds(receipt_text: str) -> float:
    """How long the lines stage may wait for the model: the base budget plus a
    little per line of receipt text, since generation time grows with the lines
    it has to write out (#34)."""
    settings = get_settings()
    lines = sum(1 for row in receipt_text.splitlines() if row.strip())
    budget = settings.llm_timeout_seconds + settings.llm_lines_seconds_per_line * lines
    # Never past the job's lock: a stage still waiting when the lock lapses could
    # be claimed by another worker and read twice.
    return min(budget, 0.8 * settings.ingest_lock_timeout_seconds)


def _unit(text: str | None) -> str | None:
    if text is None or not text.strip():
        return None
    parsed = parse_unit(text)
    return None if isinstance(parsed, UnitParseFailure) else parsed


def refine_line(seq: int, line: ReceiptLine) -> ParsedLine:
    """Normalize units and fill quantities from the printed patterns when absent."""
    flags: list[str] = []
    qty, unit_price = line.qty, line.unit_price
    unit = _unit(line.unit)
    if line.unit is not None and unit is None:
        flags.append("unknown_unit")
    if line.line_kind == "item":
        # What the receipt prints outranks what the model says (non-negotiable 6:
        # never guess): a printed weight or count is read from the text, and a
        # quantity nothing supports is marked as assumed for review (#31).
        weighed = WEIGHT_PATTERN.search(line.raw_text)
        counted = COUNT_PATTERN.search(line.raw_text) if weighed is None else None
        printed: tuple[Decimal, str | None, Decimal] | None = None
        if weighed is not None:
            printed = (
                Decimal(weighed.group(1)),
                _unit(weighed.group(2)),
                Decimal(weighed.group(3)),
            )
        elif counted is not None:
            printed = (Decimal(counted.group(1)), "each", Decimal(counted.group(2)))
        if printed is not None:
            p_qty, p_unit, p_price = printed
            if qty is None:
                flags.append("qty_inferred")
            elif qty != p_qty or (unit is not None and p_unit is not None and unit != p_unit):
                flags.append("qty_corrected")
            qty, unit = p_qty, p_unit or unit or "each"
            unit_price = unit_price if unit_price is not None else p_price
        elif qty is None:
            qty, unit = (
                Decimal("1"),
                "each",
            )  # a plain item line is one pack, if nothing says otherwise
            flags.append("qty_assumed")
        elif LOOSE_WEIGHT_PATTERN.search(line.raw_text):
            # A weight is printed but could not be read: whatever the model said,
            # nothing on the line supports it.
            flags.append("qty_assumed")
        if unit is None:
            unit = "each"
    return ParsedLine(
        seq=seq,
        raw_text=line.raw_text,
        line_kind=line.line_kind,
        qty=qty,
        unit=unit,
        unit_price=unit_price,
        line_total=line.line_total,
        flags=flags,
    )


def attach_parents(model_lines: list[ReceiptLine], parsed: list[ParsedLine]) -> None:
    """Set ``parent_seq`` for discounts and deposits (1-based, item lines only)."""
    for i, (raw, line) in enumerate(zip(model_lines, parsed, strict=True)):
        if line.line_kind not in ("discount", "deposit"):
            continue
        p = raw.parent_index
        if p is not None and 0 <= p < len(parsed) and parsed[p].line_kind == "item" and p != i:
            line.parent_seq = parsed[p].seq
            continue
        if p is not None:
            line.flags.append("parent_rejected")
        # Printed directly beneath an item, or beneath another attachment of one.
        j = i - 1
        while j >= 0 and parsed[j].line_kind in ("discount", "deposit"):
            if parsed[j].parent_seq is not None:
                line.parent_seq = parsed[j].parent_seq
                line.flags.append("parent_inferred")
                break
            j -= 1
        else:
            if j >= 0 and parsed[j].line_kind == "item":
                line.parent_seq = parsed[j].seq
                line.flags.append("parent_inferred")


def parse_model_lines(result: ReceiptLines) -> list[ParsedLine]:
    parsed = [refine_line(i + 1, line) for i, line in enumerate(result.lines)]
    attach_parents(result.lines, parsed)
    return parsed


def reconcile(
    lines: list[ParsedLine], printed_total: Decimal | None, header_tax: Decimal | None
) -> dict[str, Any]:
    sums = {k: Decimal("0") for k in ("item", "discount", "tax", "deposit", "fee")}
    for line in lines:
        sums[line.line_kind] += line.line_total
    tax_source = "lines"
    if not any(line.line_kind == "tax" for line in lines):
        if header_tax is not None:
            sums["tax"], tax_source = header_tax, "header"
        else:
            tax_source = "none"
    computed = sums["item"] - sums["discount"] + sums["tax"] + sums["deposit"] + sums["fee"]
    out: dict[str, Any] = {
        "checked": printed_total is not None,
        "items": str(sums["item"]),
        "discounts": str(sums["discount"]),
        "tax": str(sums["tax"]),
        "tax_source": tax_source,
        "deposits": str(sums["deposit"]),
        "fees": str(sums["fee"]),
        "computed_total": str(computed.quantize(CENTS)),
        "printed_total": None if printed_total is None else str(printed_total),
        "difference": None,
        "mismatch": False,
    }
    if printed_total is not None:
        difference = (computed - printed_total).quantize(CENTS)
        out["difference"] = str(difference)
        out["mismatch"] = abs(difference) > RECONCILE_TOLERANCE
    return out


def computed_total(lines: list[ParsedLine]) -> Decimal:
    total = Decimal("0")
    for line in lines:
        total += -line.line_total if line.line_kind == "discount" else line.line_total
    return total.quantize(CENTS)


async def upsert_draft_purchase(
    db: AsyncSession,
    job: IngestJob,
    document: ReceiptDocument,
    *,
    purchased_at: datetime,
    subtotal: Decimal | None,
    tax: Decimal | None,
    total: Decimal,
    vendor_location_id: uuid.UUID | None,
    flags: list[str],
    lines: list[ParsedLine],
) -> Purchase:
    """Create the receipt's draft purchase, or rewrite the one the job already has."""
    purchase: Purchase | None = None
    if job.purchase_id is not None:
        purchase = await db.get(Purchase, job.purchase_id)
    if purchase is not None:
        if purchase.status != "draft":
            raise StageFailure(code="purchase_not_draft")
        await db.execute(delete(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id))
        purchase.purchased_at = purchased_at
        purchase.subtotal = subtotal
        purchase.tax = tax
        purchase.total = total
        purchase.vendor_location_id = vendor_location_id
        purchase.flags = list(flags)
    else:
        purchase = Purchase(
            id=new_id(),
            vendor_location_id=vendor_location_id,
            receipt_document_id=document.id,
            purchased_at=purchased_at,
            subtotal=subtotal,
            tax=tax,
            total=total,
            status="draft",
            source="receipt",
            entered_by=document.uploaded_by,
            flags=list(flags),
        )
        db.add(purchase)
        await db.flush()  # the purchase row must exist before the job points at it
        job.purchase_id = purchase.id
    await db.flush()

    by_seq: dict[int, uuid.UUID] = {}
    for line in lines:
        line.purchase_line_id = new_id()
        by_seq[line.seq] = line.purchase_line_id
    for line in lines:
        db.add(
            PurchaseLine(
                id=line.purchase_line_id,
                purchase_id=purchase.id,
                seq=line.seq,
                raw_text=line.raw_text,
                raw_text_norm=normalize_receipt_text(line.raw_text),
                line_kind=line.line_kind,
                parent_line_id=None if line.parent_seq is None else by_seq.get(line.parent_seq),
                qty=line.qty,
                unit=line.unit,
                unit_price=line.unit_price,
                line_total=line.line_total,
                resolution="unmatched",
                flags=list(line.flags),
            )
        )
    await db.flush()
    return purchase
