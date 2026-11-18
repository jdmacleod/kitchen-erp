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

The grid and the receipts live in ``reading_bench_inputs``, one reading in
``reading_bench_reader`` and scoring in ``reading_bench_scoring``; the names the
CLI and the tests use are re-exported here (``import name as name``).
"""

from __future__ import annotations

import csv
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.ingest import llm
from app.ingest.errors import ModelUnavailable
from app.ingest.ocr import OCR_NUM_PREDICT as OCR_NUM_PREDICT
from app.ingest.ocr import drop_repeated_tail as drop_repeated_tail
from app.services.reading_bench_inputs import ARMS as ARMS
from app.services.reading_bench_inputs import DEFAULT_VISION_MODELS as DEFAULT_VISION_MODELS
from app.services.reading_bench_inputs import OCR_PROMPT_VERSION as OCR_PROMPT_VERSION
from app.services.reading_bench_inputs import TEXT_PROMPT_VERSION as TEXT_PROMPT_VERSION
from app.services.reading_bench_inputs import Config, Selection, label
from app.services.reading_bench_inputs import Receipt as Receipt
from app.services.reading_bench_inputs import default_configs as default_configs
from app.services.reading_bench_inputs import select_receipts as select_receipts
from app.services.reading_bench_inputs import worker_running as worker_running
from app.services.reading_bench_reader import ConfigSkipped, ocr_text, read_receipt
from app.services.reading_bench_reader import Reading as Reading
from app.services.reading_bench_scoring import CSV_COLUMNS, Decision, aggregate, decide, score
from app.services.reading_bench_scoring import margin as margin
from app.services.reading_bench_scoring import wilson80 as wilson80

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


async def run(  # noqa: C901, PLR0915
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
