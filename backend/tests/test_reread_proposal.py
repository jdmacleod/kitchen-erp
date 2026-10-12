"""The re-read proposal on its own: alignment, a person's lines, attachments (04, 2S).

Pure: no database. Every wording here is invented.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

from app.ingest.lines import ParsedLine
from app.ingest.reread import Line, has_changes, propose

D = Decimal


def written(*rows: tuple) -> list[Line]:
    """Baseline lines: (text, kind, total[, qty, unit, parent index, flags])."""
    out: list[Line] = []
    for at, row in enumerate(rows):
        text, kind, total, *rest = row
        qty, unit, parent, flags = (rest + [None, None, None, None])[:4]
        out.append(
            Line(
                raw_text=text,
                line_kind=kind,
                qty=qty,
                unit=unit,
                unit_price=None,
                line_total=D(total),
                flags=list(flags or []),
                id=uuid.uuid4(),
                parent_id=None if parent is None else out[parent].id,
                seq=at + 1,
            )
        )
    return out


def reading(*rows: tuple) -> list[ParsedLine]:
    out = []
    for at, row in enumerate(rows):
        text, kind, total, *rest = row
        qty, unit, parent_seq, flags = (rest + [None, None, None, None])[:4]
        out.append(
            ParsedLine(
                seq=at + 1,
                raw_text=text,
                line_kind=kind,
                qty=qty,
                unit=unit,
                unit_price=None,
                line_total=D(total),
                parent_seq=parent_seq,
                flags=list(flags or []),
            )
        )
    return out


def run(baseline, present, new, total="10.00", flags=None, reading_flags=None):
    return propose(
        baseline=baseline,
        present=present,
        reading=new,
        printed_total=None if total is None else D(total),
        header_tax=None,
        present_flags=flags or [],
        reading_flags=reading_flags or [],
    )


def actions(proposal) -> list[tuple[str, str | None]]:
    return [
        (c["action"], (c.get("after") or c.get("before") or c.get("reading"))["raw_text"])
        for c in proposal["changes"]
    ]


def test_the_same_reading_changes_nothing():
    base = written(("MOSS BAY KELP", "item", "4.00"), ("FERN TEA", "item", "6.00"))
    same = reading(("MOSS BAY KELP", "item", "4.00"), ("FERN TEA", "item", "6.00"))
    p = run(base, list(base), same)
    assert p["changes"] == []
    assert not has_changes(p)
    assert [r["line_id"] for r in p["rows"]] == [str(base[0].id), str(base[1].id)]


def test_a_changed_amount_keeps_the_line_and_its_id():
    base = written(("MOSS BAY KELP", "item", "40.0", None, None, None, ["decimal_missing"]))
    p = run(base, list(base), reading(("MOSS BAY KELP", "item", "4.00")), total="4.00")
    assert actions(p) == [("changed", "MOSS BAY KELP")]
    change = p["changes"][0]
    assert change["line_id"] == str(base[0].id)
    assert change["fields"] == ["line_total", "flags"]
    assert p["before"]["reconciliation"]["mismatch"] is True
    assert p["after"]["reconciliation"]["mismatch"] is False
    assert p["rows"][0]["line_id"] == str(base[0].id)


def test_lines_added_and_removed_in_receipt_order():
    base = written(
        ("MOSS BAY KELP", "item", "4.00"),
        ("BALANCE DUE", "tax", "2.00"),
        ("FERN TEA", "item", "6.00"),
    )
    new = reading(
        ("MOSS BAY KELP", "item", "4.00"),
        ("FERN TEA", "item", "6.00"),
        ("BAG FEE", "fee", "0.10"),
    )
    p = run(base, list(base), new, total="10.10")
    assert actions(p) == [("removed", "BALANCE DUE"), ("added", "BAG FEE")]
    assert [r["raw_text"] for r in p["rows"]] == ["MOSS BAY KELP", "FERN TEA", "BAG FEE"]
    assert p["rows"][2]["line_id"] is None
    assert p["counts"] == {"changed": 0, "added": 1, "removed": 1, "kept": 0}


def test_a_person_edited_line_is_kept_as_they_left_it():
    base = written(("MOSS BAY KELP", "item", "40.0"), ("FERN TEA", "item", "6.00"))
    present = [replace(base[0], line_total=D("4.25")), base[1]]
    p = run(base, present, reading(("MOSS BAY KELP", "item", "4.00"), ("FERN TEA", "item", "6.00")))
    assert actions(p) == [("kept", "MOSS BAY KELP")]
    assert p["changes"][0]["why"] == "edited"
    assert p["rows"][0]["line_total"] == "4.25"
    assert p["rows"][0]["owner"] == "person"
    assert not has_changes(p)


def test_a_person_edit_the_reading_now_agrees_with_is_not_listed():
    base = written(("MOSS BAY KELP", "item", "40.0"))
    present = [replace(base[0], line_total=D("4.00"))]
    p = run(base, present, reading(("MOSS BAY KELP", "item", "4.00")), total="4.00")
    assert p["changes"] == []


def test_a_line_a_person_removed_is_not_added_back():
    base = written(("MOSS BAY KELP", "item", "4.00"), ("CARD PAYMENT", "item", "10.00"))
    p = run(
        base,
        [base[0]],
        reading(("MOSS BAY KELP", "item", "4.00"), ("CARD PAYMENT", "item", "9.00")),
    )
    assert actions(p) == [("kept", "CARD PAYMENT")]
    assert p["changes"][0]["why"] == "removed"
    assert [r["raw_text"] for r in p["rows"]] == ["MOSS BAY KELP"]


def test_a_line_a_person_added_stays_and_is_not_doubled():
    base = written(("MOSS BAY KELP", "item", "4.00"))
    added = Line("FERN TEA", "item", None, None, None, D("6.00"), id=uuid.uuid4())
    p = run(
        base,
        [base[0], added],
        reading(("MOSS BAY KELP", "item", "4.00"), ("FERN TEA", "item", "6.00")),
    )
    assert p["changes"] == []
    assert [r["line_id"] for r in p["rows"]] == [str(base[0].id), str(added.id)]


def test_a_line_a_person_added_keeps_its_place():
    base = written(("MOSS BAY KELP", "item", "4.00"), ("FERN TEA", "item", "6.00"))
    added = Line("PLUM BAR", "item", None, None, None, D("2.00"), id=uuid.uuid4())
    present = [base[0], added, base[1]]
    p = run(base, present, reading(("MOSS BAY KELP", "item", "4.00"), ("FERN TEA", "item", "6.00")))
    assert [r["raw_text"] for r in p["rows"]] == ["MOSS BAY KELP", "PLUM BAR", "FERN TEA"]


def test_a_discount_attaches_to_its_item_by_position():
    base = written(("MOSS BAY KELP", "item", "4.00"), ("FERN TEA", "item", "6.00"))
    new = reading(
        ("MOSS BAY KELP", "item", "4.00"),
        ("FERN TEA", "item", "6.00"),
        ("MEMBER SAVINGS", "discount", "1.00", None, None, 2),
    )
    p = run(base, list(base), new, total="9.00")
    assert actions(p) == [("added", "MEMBER SAVINGS")]
    assert p["rows"][2]["attach_to"] == 1
    assert p["changes"][0]["after"]["attached"] == "FERN TEA"


def test_a_line_that_changes_kind_is_paired_only_with_its_kind():
    base = written(("TOTAL SAVINGS", "item", "3.00"))
    p = run(base, list(base), reading(("TOTAL SAVINGS", "discount", "3.00")), total=None)
    assert [a for a, _ in actions(p)] == ["removed", "added"]


def test_a_resolution_flag_is_not_counted_as_a_change():
    base = written(("MOSS BAY KELP", "item", "4.00"))
    present = [replace(base[0], flags=["price_outlier"])]
    p = run(base, present, reading(("MOSS BAY KELP", "item", "4.00")), total="4.00")
    assert p["changes"] == []


def test_line_reading_flags_before_and_after():
    base = written(("MOSS BAY KELP", "item", "4.00"))
    p = run(
        base,
        list(base),
        reading(("MOSS BAY KELP", "item", "4.00")),
        total="9.00",
        flags=["reconcile_mismatch", "ocr_fallback"],
        reading_flags=["rows_not_read", "reconcile_mismatch"],
    )
    assert p["before"]["flags"] == ["reconcile_mismatch"]
    assert p["after"]["flags"] == ["rows_not_read", "reconcile_mismatch"]
