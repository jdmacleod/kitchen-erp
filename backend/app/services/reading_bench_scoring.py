"""Scoring ``kerp reading-benchmark`` readings (spec 04, 2J): one reading against
what the person committed, the ``reading.csv`` row per configuration, and the
decision rule that names a winner.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.ingest import lines as lines_stage
from app.ingest import llm
from app.ingest.witness import any_support, witness_amounts
from app.services.normalize import normalize_receipt_text
from app.services.reading_bench_inputs import Config, Receipt, label
from app.services.reading_bench_reader import Reading

# --- the grid --------------------------------------------------------------------

# Arm (c)'s screening grid (MS4, 2026-10-01): two quality-tier models and four
# that fit on the household model server's GPU, in three families.


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
        "item_count_diff": (
            None if receipt.expected_items is None else abs(len(items) - receipt.expected_items)
        ),
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


def _mean(values: Iterable[float | None]) -> float | None:
    """The mean of the values that are known, or None when none is."""
    known = [v for v in values if v is not None]
    return statistics.fmean(known) if known else None


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
        item_count_mad=_mean(row["item_count_diff"] for row in rows),
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
