"""``kerp reading-benchmark`` (spec 04, 2J; criteria 67 and 69).

Receipts here are the synthetic fixtures, run through the pipeline and then
committed by setting the purchase's status, as a person's commit would leave it.
Every model answer is recorded.
"""

from __future__ import annotations

import csv
import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.db import WORKER_APPLICATION_NAME, get_sessionmaker
from app.ingest import llm
from app.ingest.replay import RecordedTransport
from app.models import (
    IngestStageResult,
    PriceObservation,
    Purchase,
    PurchaseLine,
    ReceiptAlias,
)
from app.services import reading_benchmark as bench
from app.services.normalize import normalize_receipt_text
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.ingest_helpers import load_fixture, png_bytes, run_job, upload
from tests.pricebook_helpers import make_location

no_network = gh.no_network
receipts_dir = ih.receipts_dir
recorded = ih.recorded

TEXT_MODEL = "text-model:1b"
OCR_MODEL = "ocr-model:1b"
VISION = "vision-a:1b"


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


# --- the pure parts ----------------------------------------------------------------


def test_wilson_interval_brackets_the_share():
    low, high = bench.wilson80(9, 12)
    assert low < 0.75 < high
    assert bench.wilson80(0, 0) == (0.0, 0.0)
    assert bench.wilson80(12, 12)[1] == 1.0


def test_margin_is_three_receipts():
    assert bench.margin(12) == pytest.approx(0.25)
    assert bench.margin(30) == pytest.approx(0.1)


def _row(arm: str, model: str, share: float, alias: float | None = 0.5, seconds: float = 10):
    return {
        "arm": arm,
        "model": model,
        "reconcile_share": share,
        "alias_hit_rate": alias,
        "seconds_p50": seconds,
    }


@pytest.mark.parametrize(
    ("rows", "n", "reader", "consensus"),
    [
        # (c) clears the bar and beats (a) by more than 3/30: ship it; 10% unreconciled.
        ([_row("a", "t", 0.5), _row("c", "v", 0.9)], 30, "c", False),
        # Under the 75% bar: neither, however far ahead of (a).
        ([_row("a", "t", 0.2), _row("c", "v", 0.7)], 30, "neither", False),
        # Ahead of (a) but within the margin (3/12 = 25%): no decision, so neither.
        ([_row("a", "t", 0.6), _row("c", "v", 0.8)], 12, "neither", False),
        # Aliases more than 5 points under (a)'s: dropped.
        ([_row("a", "t", 0.4, 0.6), _row("c", "v", 0.9, 0.5)], 30, "neither", False),
        # Both pass; (b) is not ahead of (c) by more than the margin: prefer (c). It
        # leaves more than 1 in 5 unreconciled, so consensus (step 2) is triggered.
        ([_row("a", "t", 0.3), _row("b", "o", 0.85), _row("c", "v", 0.78)], 30, "c", True),
        # Both pass; (b) ahead of (c) by more than the margin: (b).
        ([_row("a", "t", 0.3), _row("b", "o", 0.97), _row("c", "v", 0.8)], 30, "b", False),
        # Only (b) passes.
        ([_row("a", "t", 0.3), _row("b", "o", 0.9), _row("c", "v", 0.5)], 30, "b", False),
    ],
)
def test_decision_rule(rows, n, reader, consensus):
    decision = bench.decide(rows, n)
    assert decision.reader == reader
    assert decision.consensus_step is consensus


def test_each_arm_takes_its_best_configuration_ties_by_aliases_then_speed():
    rows = [
        _row("c", "slow", 0.9, 0.5, seconds=90),
        _row("c", "fast", 0.9, 0.5, seconds=20),
        _row("c", "aliases", 0.9, 0.7, seconds=200),
        _row("c", "worse", 0.8, 0.9, seconds=1),
    ]
    assert bench.decide(rows, 30).best["c"]["model"] == "aliases"
    assert bench.decide(rows[:2], 30).best["c"]["model"] == "fast"


def test_an_ocr_models_loop_is_cut_off():
    receipt = "LANTERN GROCERY\nPLUM JAM 4.25\nPLUM JAM 4.25\nRYE LOAF 3.50\nTOTAL 12.00"
    looping = receipt + "\nPLUM JAM 4.25\nRYE LOAF 3.50\nTOTAL 12.00\nPLUM JAM 4.25"
    # The same item twice is a receipt; the same three lines twice is a loop.
    assert bench.drop_repeated_tail(receipt) == receipt
    assert bench.drop_repeated_tail(looping) == receipt


def test_the_estimate_uses_stored_speeds_where_there_are_any():
    configs = bench.default_configs(text_models=["t"], ocr_model="o", vision_models=["v", "w"])
    history = [{"arm": "c", "model": "v", "seconds_p50": "40"}]
    # a 120 + b 130 + v 40 (stored) + w 150 (default), per receipt.
    assert bench.estimate_seconds(configs, 10, history) == 10 * (120 + 130 + 40 + 150)


def test_arm_b_runs_once_per_text_model_under_its_own_key():
    configs = bench.default_configs(
        text_models=["t1", "t2"], ocr_model="o", vision_models=["v"], arms=["b"]
    )
    assert [(c.arm, c.model, c.text_model) for c in configs] == [("b", "o", "t1"), ("b", "o", "t2")]
    # Two text models are two configurations: their checkpoints must not mix.
    assert len({c.key for c in configs}) == 2
    everything = bench.default_configs(text_models=["t"], ocr_model="o", vision_models=["v"])
    assert [c.arm for c in everything] == ["a", "b", "c"]


def _stored(arm: str, model: str, share: str, text_model: str | None = None) -> dict[str, str]:
    row = {"arm": arm, "model": model, "prompt_version": "p", "receipt_set_hash": "h"}
    row |= {"reconcile_share": share, "alias_hit_rate": "", "seconds_p50": "60"}
    return row if text_model is None else row | {"text_model": text_model}


def test_a_new_text_model_is_compared_with_the_stored_arm_b_row_not_hidden_by_it():
    # The stored (b) row was written before the text_model column existed.
    history = [_stored("a", "t", "0.2"), _stored("b", "o", "0.5"), _stored("c", "v", "0.4")]
    current = [{"arm": "b", "model": "o", "prompt_version": "p", "text_model": "t2"}]
    kept = bench._stored_rows(history, "h", current)
    assert sorted((r["arm"], r["model"]) for r in kept) == [("a", "t"), ("b", "o"), ("c", "v")]
    # A configuration run again replaces its stored row.
    history.append(_stored("b", "o", "0.6", text_model="t2"))
    kept = bench._stored_rows(history, "h", current)
    assert ("b", "t2") not in {(r["arm"], r.get("text_model")) for r in kept}
    # The summary names the text model an arm (b) row ends in.
    assert bench.label({"arm": "b", "model": "o", "text_model": "t2"}) == "o→t2"
    assert bench.label({"arm": "a", "model": "t", "text_model": "t"}) == "t"


def test_a_stored_csv_from_before_a_new_column_is_rewritten_under_the_new_header(
    tmp_path: Path,
):
    path = tmp_path / "reading.csv"
    old_columns = [c for c in bench.CSV_COLUMNS if c != "text_model"]
    path.write_text(",".join(old_columns) + "\n" + ",".join("1" for _ in old_columns) + "\n")
    row = dict.fromkeys(bench.CSV_COLUMNS, "2") | {"text_model": "t2"}
    bench._append_csv(path, [row])
    rows = bench.read_history(path)
    assert list(rows[0]) == bench.CSV_COLUMNS
    assert (rows[0]["run_id"], rows[0]["text_model"]) == ("1", "")
    assert (rows[1]["run_id"], rows[1]["text_model"]) == ("2", "t2")


# --- receipts and their truth --------------------------------------------------------


async def _committed_receipt(
    admin_client: httpx.AsyncClient,
    fixture: ih.ReceiptFixture,
    seed: str,
    *,
    flags: list[str] | None = None,
    status: str = "committed",
    location_id: str | None = None,
) -> tuple[str, str]:
    """Upload, read and commit one receipt. Returns (document id, purchase id)."""
    r = await upload(admin_client, png_bytes(seed), ocr_text=fixture.ocr_text)
    assert r.status_code == 201, r.text
    body = r.json()
    job = await run_job(body["job"]["id"])
    if location_id is None and status != "draft":
        # A committed purchase has a location (ck_purchase_location).
        location = await make_location(admin_client, f"Synthetic Grocer {seed}", f"Branch {seed}")
        location_id = location["id"]
    async with get_sessionmaker()() as db:
        values: dict[str, Any] = {"status": status}
        if flags is not None:
            values["flags"] = flags
        if location_id is not None:
            values["vendor_location_id"] = uuid.UUID(location_id)
        if status == "voided":
            values.update(voided_at=func.now(), voided_by=Purchase.entered_by)
        await db.execute(update(Purchase).where(Purchase.id == job.purchase_id).values(**values))
        await db.commit()
    return body["document"]["id"], str(job.purchase_id)


async def _select(expected: Path | None = None) -> bench.Selection:
    async with get_sessionmaker()() as db:
        try:
            return await bench.select_receipts(db, expected)
        finally:
            await db.rollback()


async def test_committed_receipts_are_scored_and_the_exclusions_counted(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded
):
    fixture = load_fixture("supermarket_produce_crv")
    recorded(fixture)
    kept, _ = await _committed_receipt(admin_client, fixture, "kept")
    await _committed_receipt(admin_client, fixture, "missing", flags=["total_missing"])
    await _committed_receipt(admin_client, fixture, "mismatch", flags=["reconcile_mismatch"])
    await _committed_receipt(admin_client, fixture, "draft", status="draft")
    await _committed_receipt(admin_client, fixture, "voided", status="voided")
    # The model misread the total and the printed TOTAL line corrected it; nobody
    # then edited it, so the old pipeline's reading would be scored as the truth.
    misread = dict(fixture.llm_responses)
    misread["header"] = {**fixture.llm_responses["header"], "total": "2.08"}
    recorded(misread)
    await _committed_receipt(admin_client, fixture, "text-total")

    selection = await _select()

    assert [str(r.document_id) for r in selection.receipts] == [kept]
    assert selection.excluded == {
        "total_missing": 1,
        "reconcile_mismatch": 1,
        "unedited_text_total": 1,
    }
    receipt = selection.receipts[0]
    assert receipt.expected_total == Decimal(fixture.expected_header["total"])
    items = [line for line in fixture.expected_lines if line["line_kind"] == "item"]
    assert receipt.expected_items == len(items)
    assert sorted(receipt.item_amounts) == sorted(Decimal(line["line_total"]) for line in items)


async def test_receipts_never_committed_come_from_expected_csv(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded, tmp_path: Path
):
    fixture = load_fixture("independent_minimal")
    recorded(fixture)
    document, _ = await _committed_receipt(admin_client, fixture, "uploaded", status="draft")
    expected = tmp_path / "expected.csv"
    expected.write_text(
        "document_id,total,item_line_count\n"
        f"{document},24.41,6\n"
        f"{uuid.uuid4()},1.00,1\n"  # not a document here
    )

    selection = await _select(expected)

    (receipt,) = selection.receipts
    assert (str(receipt.document_id), receipt.expected_total, receipt.expected_items) == (
        document,
        Decimal("24.41"),
        6,
    )
    assert receipt.item_amounts == () and receipt.vendor_id is None
    assert selection.excluded["expected_csv_unknown_document"] == 1


async def test_a_connected_worker_is_seen():
    async with get_sessionmaker()() as db:
        assert await bench.worker_running(db) is False
    engine = create_async_engine(
        get_settings().database_url,
        connect_args={"server_settings": {"application_name": WORKER_APPLICATION_NAME}},
    )
    try:
        async with engine.connect() as worker:
            await worker.execute(text("SELECT 1"))
            async with get_sessionmaker()() as db:
                assert await bench.worker_running(db) is True
    finally:
        await engine.dispose()


# --- a run ---------------------------------------------------------------------------


def _responses(fixture: ih.ReceiptFixture) -> dict[str, Any]:
    """The text path's recorded answers, and the same reading from the image."""
    lines = fixture.llm_responses["lines"]
    boxed = {
        "lines": [
            {**line, "box": [40, 100 + 30 * i, 900, 120 + 30 * i]}
            for i, line in enumerate(lines["lines"])
        ]
    }
    return {
        **fixture.llm_responses,
        f"chat@{OCR_MODEL}": fixture.ocr_text,
        f"header@{VISION}": fixture.llm_responses["header"],
        f"lines@{VISION}": boxed,
    }


async def _counts() -> dict[str, int]:
    async with get_sessionmaker()() as db:
        out = {}
        for model in (IngestStageResult, Purchase, PurchaseLine, PriceObservation, ReceiptAlias):
            out[model.__tablename__] = (
                await db.execute(select(func.count()).select_from(model))
            ).scalar_one()
        return out


def _configs(*vision: str) -> list[bench.Config]:
    return bench.default_configs(
        text_models=[TEXT_MODEL], ocr_model=OCR_MODEL, vision_models=vision or (VISION,)
    )


async def _two_receipts(admin_client: httpx.AsyncClient, fixture: ih.ReceiptFixture) -> str:
    location = await make_location(admin_client, "Tidewater Provisions", "Tidewater Harbour")
    for seed in ("first", "second"):
        await _committed_receipt(admin_client, fixture, seed, location_id=location["id"])
    return location["vendor"]["id"]


async def test_a_run_scores_every_configuration_and_writes_no_app_data(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded, tmp_path: Path
):
    fixture = load_fixture("supermarket_produce_crv")
    recorded(fixture)
    vendor_id = await _two_receipts(admin_client, fixture)
    # One alias known before the receipts were drafted: it counts as a hit.
    first_item = next(x for x in fixture.expected_lines if x["line_kind"] == "item")
    async with get_sessionmaker()() as db:
        await db.execute(
            text(
                "INSERT INTO receipt_alias (id, vendor_id, raw_text_norm, disposition, "
                "confirmed_count, last_seen_at, created_at, updated_at) VALUES "
                "(:id, :vendor, :norm, 'ignore', 1, now(), now() - interval '1 day', now())"
            ),
            {
                "id": uuid.uuid4(),
                "vendor": uuid.UUID(vendor_id),
                "norm": normalize_receipt_text(first_item["raw_text"]),
            },
        )
        await db.commit()
    transport = recorded(_responses(fixture))
    selection = await _select()
    before = await _counts()

    result = await bench.run(
        selection, bench.RunOptions(out_dir=tmp_path, configs=_configs()), echo=lambda _: None
    )

    assert await _counts() == before  # criterion 67
    assert {(r["arm"], r["n"]) for r in result.rows} == {("a", 2), ("b", 2), ("c", 2)}
    for row in result.rows:
        assert row["reconcile_share"] == 1.0, row["arm"]
        assert row["header_total_exact"] == 1.0
        assert row["line_amount_exact_share"] == 1.0
        assert row["runaway_share"] == 0.0
    by_arm = {row["arm"]: row for row in result.rows}
    items = sum(1 for line in fixture.expected_lines if line["line_kind"] == "item")
    assert by_arm["a"]["alias_hit_rate"] == pytest.approx(1 / items)
    assert by_arm["c"]["box_valid_share"] == 1.0
    assert by_arm["a"]["box_valid_share"] is None
    assert by_arm["a"]["ocr_support"] == 1.0  # every right amount is in the OCR text
    # Vision calls send images, think off, and the vision context.
    ocr_call = next(r for r in transport.requests if r["model"] == OCR_MODEL)
    assert ocr_call["body"]["messages"][0]["content"] == llm.TRANSCRIBE_SYSTEM_PROMPT
    assert ocr_call["body"]["options"]["num_predict"] == bench.OCR_NUM_PREDICT
    vision_calls = [r for r in transport.requests if r["model"] == VISION]
    assert len(vision_calls) == 4  # header and lines, two receipts
    assert all(r["body"]["think"] is False for r in vision_calls)
    assert all(r["body"]["messages"][1]["images"] for r in vision_calls)

    run_dir = result.run_dir
    readings = (run_dir / "readings.jsonl").read_text().splitlines()
    assert len(readings) == 3 * 2
    with (tmp_path / "reading.csv").open() as handle:
        stored = list(csv.DictReader(handle))
    assert [row["arm"] for row in stored] == ["a", "b", "c"]
    assert set(stored[0]) == set(bench.CSV_COLUMNS)
    summary = (run_dir / "summary.txt").read_text()
    assert "decision:" in summary and "n=2" in summary
    # Ids, counts and scores only: no receipt wording in anything it writes.
    for path in (run_dir / "readings.jsonl", run_dir / "summary.txt", tmp_path / "reading.csv"):
        content = path.read_text()
        for line in fixture.expected_lines:
            assert line["raw_text"].split()[0] not in content, path.name


async def test_a_stopped_run_resumes_where_it_left_off(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded, tmp_path: Path
):
    fixture = load_fixture("supermarket_produce_crv")
    recorded(fixture)
    await _two_receipts(admin_client, fixture)
    recorded(_responses(fixture))
    selection = await _select()
    first = await bench.run(
        selection, bench.RunOptions(out_dir=tmp_path, configs=_configs()), echo=lambda _: None
    )
    checkpoint = first.run_dir / "readings.jsonl"
    kept = checkpoint.read_text().splitlines()[:3]
    checkpoint.write_text("\n".join(kept) + "\n")  # stopped after three readings

    transport = recorded(_responses(fixture))
    resumed = await bench.run(
        selection,
        bench.RunOptions(out_dir=tmp_path, configs=[], resume=first.run_id),
        echo=lambda _: None,
    )

    assert len(checkpoint.read_text().splitlines()) == 6
    # Only the missing readings asked a model: (b)'s second receipt (transcribe,
    # header, lines), then (c) twice (header and lines each).
    chats = [r for r in transport.requests if r["url"].endswith("/api/chat")]
    assert [r["model"] for r in chats] == [OCR_MODEL, TEXT_MODEL, TEXT_MODEL] + [VISION] * 4
    assert [r["n"] for r in resumed.rows] == [2, 2, 2]
    with (tmp_path / "reading.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 3  # written once per run


class _MissingModel(httpx.AsyncBaseTransport):
    """Ollama's 404 for one model; recorded answers for the rest."""

    def __init__(self, inner: RecordedTransport, missing: str) -> None:
        self.inner, self.missing = inner, missing

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if json.loads(request.content or b"{}").get("model") == self.missing:
            return httpx.Response(404, json={"error": f"model '{self.missing}' not found"})
        return await self.inner.handle_async_request(request)


async def test_a_missing_model_is_skipped_not_scored_as_zero(
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    recorded,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fixture = load_fixture("supermarket_produce_crv")
    recorded(fixture)
    await _two_receipts(admin_client, fixture)
    inner = RecordedTransport(_responses(fixture))
    monkeypatch.setattr(llm, "http_transport", _MissingModel(inner, "not-pulled:1b"))
    selection = await _select()

    result = await bench.run(
        selection,
        bench.RunOptions(out_dir=tmp_path, configs=_configs(VISION, "not-pulled:1b")),
        echo=lambda _: None,
    )

    assert [r["model"] for r in result.rows] == [TEXT_MODEL, OCR_MODEL, VISION]
    (reason,) = result.skipped.values()
    assert reason == "not_found"
    assert "not-pulled:1b: skipped (not_found)" in result.summary


async def test_a_reply_that_never_validates_is_a_failed_reading(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded, tmp_path: Path
):
    """Criterion 69: never scored from a partial reply."""
    fixture = load_fixture("supermarket_produce_crv")
    recorded(fixture)
    await _two_receipts(admin_client, fixture)
    responses = _responses(fixture)
    responses[f"lines@{VISION}"] = '{"lines": [{"raw_text": "half a'
    recorded(responses)
    selection = await _select()

    result = await bench.run(
        selection,
        bench.RunOptions(out_dir=tmp_path, configs=[_configs()[2]]),
        echo=lambda _: None,
    )

    (row,) = result.rows
    assert row["reconcile_share"] == 0.0
    readings = [json.loads(line) for line in (result.run_dir / "readings.jsonl").open()]
    assert {r["error"] for r in readings} == {"invalid_model_output"}
    assert all(r["item_count"] == 0 for r in readings)


# --- the command ---------------------------------------------------------------------


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def rollback(self):
        return None


def _cli(monkeypatch: pytest.MonkeyPatch, selection: bench.Selection, worker: bool, *args: str):
    from typer.testing import CliRunner

    from app import cli as cli_module
    from app.core import db as db_module

    async def select(db, expected=None):
        return selection

    async def running(db):
        return worker

    async def dispose():
        return None

    monkeypatch.setattr(bench, "select_receipts", select)
    monkeypatch.setattr(bench, "worker_running", running)
    monkeypatch.setattr(db_module, "get_sessionmaker", lambda: _FakeSession)
    monkeypatch.setattr(db_module, "dispose_engine", dispose)
    return CliRunner().invoke(cli_module.cli, ["reading-benchmark", *args])


def _one_receipt() -> bench.Selection:
    receipt = bench.Receipt(uuid.uuid4(), "x.png", "image/png", None, Decimal("1.00"), 1)
    return bench.Selection([receipt])


def test_the_command_refuses_while_a_worker_is_connected(monkeypatch: pytest.MonkeyPatch):
    result = _cli(monkeypatch, _one_receipt(), worker=True)
    assert result.exit_code == 1
    assert "docker compose stop worker" in result.output and "--force" in result.output


def test_the_command_says_why_there_is_nothing_to_read(monkeypatch: pytest.MonkeyPatch):
    empty = bench.Selection([], {"total_missing": 2})
    result = _cli(monkeypatch, empty, worker=False)
    assert result.exit_code == 1
    assert "no eligible receipts" in result.output and "--expected" in result.output


def test_the_command_runs_only_the_arms_asked_for(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, bench.RunOptions] = {}

    async def run(selection, options, echo=print):
        seen["options"] = options
        return bench.RunResult("r", Path("."), [], bench.decide([], 1), {}, "summary")

    monkeypatch.setattr(bench, "run", run)
    result = _cli(monkeypatch, _one_receipt(), False, "--arms", "b", "--text-model", "t1, t2")
    assert result.exit_code == 0, result.output
    options = seen["options"]
    assert [(c.arm, c.text_model) for c in options.configs] == [("b", "t1"), ("b", "t2")]
    assert options.compare_with_stored  # (a) and (c) come from the stored rows

    # --models alone still means arm (c) only.
    result = _cli(monkeypatch, _one_receipt(), False, "--models", "v")
    assert [(c.arm, c.model) for c in seen["options"].configs] == [("c", "v")]

    result = _cli(monkeypatch, _one_receipt(), False, "--arms", "b,x")
    assert result.exit_code == 2
    assert "unknown arm(s) x" in result.output
