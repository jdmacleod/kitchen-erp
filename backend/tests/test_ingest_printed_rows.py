"""Printed rows, savings already in a price, and a receipt's own checks (2S).

Every receipt here is invented: store, codes, item words and amounts.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ingest import structure
from app.ingest.lines import ParsedLine
from app.ingest.printed_rows import anchor_rows, printed_checks
from app.ingest.schemas import ReceiptLine, ReceiptLines
from tests import geo_helpers as gh
from tests import ingest_helpers as ih

# A till that prints Price and You Pay, with the saving beneath for information.
# You Pay already has the saving off; the balance is the You Pay column.
TWO_COLUMNS = """\
LARKSPUR GROCERS
                              Price  You Pay
44112  MEADOW TORTILLAS   3.49   2.99 S
            Club Savings -0.50
55223  RIVERBEND YOGURT   6.29   5.79 S
            Club Savings -0.50
3781        PEPPERS FRESNO     0.63   0.63 S
WT     0.07 lb @ $8.99 /lb
4041        GALA APPLES        4.10   2.95 S
WT     1.48 lb @ $1.99 /lb
            Club Savings -1.15
     TAX                        0.00
**** BALANCE                   12.36
PAYMENT AMOUNT                 12.36
YOUR SAVINGS
Club Savings                    2.15
Total                           2.15
Total Savings Value              15%
TOTAL NUMBER OF ITEMS SOLD =      4
"""


def _model(*rows: tuple) -> ReceiptLines:
    """What the model answered: (raw_text, kind, total[, parent_index])."""
    return ReceiptLines(
        lines=[
            ReceiptLine(
                raw_text=row[0],
                line_kind=row[1],
                line_total=Decimal(row[2]),
                parent_index=row[3] if len(row) > 3 else None,
            )
            for row in rows
        ]
    )


# The model's answer with each item's codes and prices left out, as it often is.
STRIPPED = (
    ("MEADOW TORTILLAS", "item", "2.99"),
    ("Club Savings", "discount", "0.50", 0),
    ("RIVERBEND YOGURT", "item", "5.79"),
    ("Club Savings", "discount", "0.50", 2),
    ("PEPPERS FRESNO", "item", "0.63"),
    ("WT 0.07 lb @ $8.99 /lb", "item", "0"),
    ("GALA APPLES", "item", "2.95"),
    ("WT 1.48 lb @ $1.99 /lb", "item", "0"),
    ("Club Savings", "discount", "1.15"),
    ("TAX 0.00", "tax", "0.00"),
)


def _read(rows=STRIPPED, text: str = TWO_COLUMNS, total: str | None = "12.36"):
    return structure.post_passes(_model(*rows), text, None if total is None else Decimal(total))


def _line(text: str, kind: str = "item") -> ParsedLine:
    return ParsedLine(1, text, kind, None, None, None, Decimal("0"))


# --- Printed rows ------------------------------------------------------------


def test_each_line_is_matched_to_its_row_in_order():
    lines = [_line("MEADOW TORTILLAS"), _line("Club Savings", "discount")]
    lines += [_line("RIVERBEND YOGURT"), _line("Club Savings", "discount")]
    anchor_rows(lines, TWO_COLUMNS)
    assert [line.printed_row for line in lines] == [
        "44112 MEADOW TORTILLAS 3.49 2.99 S",
        "Club Savings -0.50",
        "55223 RIVERBEND YOGURT 6.29 5.79 S",
        "Club Savings -0.50",
    ]
    assert lines[0].raw_text == "MEADOW TORTILLAS"  # what the model said is kept


def test_a_line_with_no_row_is_left_unmatched():
    lines = [_line("SOMETHING NOT PRINTED")]
    anchor_rows(lines, TWO_COLUMNS)
    assert lines[0].printed_row is None


def test_weight_rows_join_their_items_through_the_printed_row():
    lines = _read().lines
    names = [line.raw_text for line in lines if line.line_kind == "item"]
    assert names == ["MEADOW TORTILLAS", "RIVERBEND YOGURT", "PEPPERS FRESNO", "GALA APPLES"]
    gala = next(line for line in lines if line.raw_text == "GALA APPLES")
    assert (gala.qty, gala.unit, gala.unit_price) == (Decimal("1.48"), "lb", Decimal("1.99"))


# --- Savings already in the price -------------------------------------------


def test_savings_under_a_you_pay_column_count_for_nothing_and_keep_their_amount():
    passes = _read()
    savings = [line for line in passes.lines if line.line_kind == "discount"]
    assert [line.line_total for line in savings] == [Decimal("0")] * 3
    assert [line.in_price_amount for line in savings] == [
        Decimal("0.50"),
        Decimal("0.50"),
        Decimal("1.15"),
    ]
    assert all(line.flags.count("saving_in_price") == 1 for line in savings)
    gala = next(line for line in passes.lines if line.raw_text == "GALA APPLES")
    assert savings[2].parent_seq == gala.seq  # under the weight row, but Gala's saving


def test_the_receipt_then_adds_up():
    from app.ingest.lines import reconcile

    passes = _read()
    assert reconcile(passes.lines, Decimal("12.36"), None)["mismatch"] is False


def test_a_saving_read_one_digit_off_takes_its_items_difference():
    rows = list(STRIPPED)
    rows[3] = ("Club Savings", "discount", "0.60", 2)
    saving = [line for line in _read(rows).lines if line.line_kind == "discount"][1]
    assert saving.in_price_amount == Decimal("0.50")
    assert {"saving_in_price", "saving_amount_from_row"} <= set(saving.flags)


def test_savings_are_subtracted_when_the_till_prints_one_price():
    # The usual loyalty layout: the item at its regular price, the saving off it.
    text = "OAKHOLLOW BREAD   4.49 F\n  CLUB SAVINGS   1.00-\nTOTAL   3.49\n"
    rows = (
        ("OAKHOLLOW BREAD 4.49 F", "item", "4.49"),
        ("CLUB SAVINGS 1.00-", "discount", "1.00", 0),
    )
    saving = _read(rows, text, "3.49").lines[1]
    assert saving.line_total == Decimal("1.00") and saving.in_price_amount is None


def test_without_rows_every_saving_can_count_as_in_price_when_the_totals_agree():
    # Rows print only what was paid, so no item proves its saving; the balance
    # and the printed savings total both say the savings are already in.
    text = (
        "MEADOW TORTILLAS   2.99\n Club Savings 0.50\nRIVERBEND YOGURT  5.79\n"
        " Club Savings 0.50\nBALANCE   8.78\nYOU SAVED   1.00\n"
    )
    rows = (
        ("MEADOW TORTILLAS 2.99", "item", "2.99"),
        ("Club Savings 0.50", "discount", "0.50", 0),
        ("RIVERBEND YOGURT 5.79", "item", "5.79"),
        ("Club Savings 0.50", "discount", "0.50", 2),
    )
    savings = [line for line in _read(rows, text, "8.78").lines if line.line_kind == "discount"]
    assert all("saving_in_price_by_total" in line.flags for line in savings)
    assert [line.in_price_amount for line in savings] == [Decimal("0.50")] * 2


def test_the_group_step_waits_for_a_printed_savings_total_that_agrees():
    text = (
        "MEADOW TORTILLAS   2.99\n Club Savings 0.50\nRIVERBEND YOGURT  5.79\n"
        " Club Savings 0.50\nBALANCE   8.78\nYOU SAVED   2.00\n"
    )
    rows = (
        ("MEADOW TORTILLAS 2.99", "item", "2.99"),
        ("Club Savings 0.50", "discount", "0.50", 0),
        ("RIVERBEND YOGURT 5.79", "item", "5.79"),
        ("Club Savings 0.50", "discount", "0.50", 2),
    )
    savings = [line for line in _read(rows, text, "8.78").lines if line.line_kind == "discount"]
    assert all(line.in_price_amount is None for line in savings)


# --- What the receipt prints about itself -----------------------------------


def test_printed_checks_reads_savings_items_and_payment():
    assert printed_checks(TWO_COLUMNS) == {
        "savings_total": "2.15",
        "item_count": 4,
        "payment": "12.36",
    }
    assert _read().checks == printed_checks(TWO_COLUMNS)


def test_a_savings_total_on_its_own_row():
    assert printed_checks("TOTAL 30.39\nYOU SAVED TODAY 6.48\n") == {"savings_total": "6.48"}


def test_cash_and_change_are_not_a_payment():
    assert printed_checks("TOTAL 8.12\nCASH 10.00\nCHANGE 1.88\n") is None


def test_cards_that_disagree_give_no_payment():
    text = "VISA ****0000 5.00\nMASTERCARD ****0000 3.12\n"
    assert printed_checks(text) is None


# --- Through the pipeline ------------------------------------------------------

no_network = gh.no_network
receipts_dir = ih.receipts_dir
recorded = ih.recorded


@pytest.mark.usefixtures("no_network", "receipts_dir")
async def test_a_read_receipt_keeps_its_rows_savings_and_checks(admin_client, recorded):
    fixture = ih.load_fixture("price_and_you_pay")
    recorded(fixture)
    _, job = await ih.upload_fixture(admin_client, fixture)
    done = await ih.run_job(job["id"])
    body = (await admin_client.get(f"/api/v1/purchases/{done.purchase_id}")).json()
    assert body["trust"] == "adds_up"
    assert body["printed_checks"] == {"savings_total": "2.15", "item_count": 4, "payment": "12.36"}
    lines = body["lines"]
    assert [line["raw_text"] for line in lines if line["line_kind"] == "item"] == [
        "MEADOW TORTILLAS",
        "RIVERBEND YOGURT",
        "PEPPERS FRESNO",
        "GALA APPLES",
    ]
    tortillas = lines[0]
    assert tortillas["printed_text"] == "44112 MEADOW TORTILLAS 3.49 2.99 S"
    savings = [line for line in lines if line["line_kind"] == "discount"]
    assert [(s["line_total"], s["in_price_amount"]) for s in savings] == [
        ("0.0000", "0.5000"),
        ("0.0000", "0.5000"),
        ("0.0000", "1.1500"),
    ]
