"""How far a receipt's reading can be trusted, and when a draft is held (#121, R2)."""

from __future__ import annotations

from decimal import Decimal

from app.services.purchases import assess, reading_gap

D = Decimal


def check(**overrides):
    values = {
        "source": "receipt",
        "status": "draft",
        "flags": [],
        "lines_sum": D("20.00"),
        "total": D("20.00"),
        "item_lines": 4,
        "unscanned": 0,
    }
    values.update(overrides)
    return assess(**values)


def test_lines_that_match_the_printed_total_add_up():
    found = check(lines_sum=D("20.01"))
    assert (found.trust, found.held) == ("adds_up", False)


def test_a_small_gap_is_checked_but_not_held():
    found = check(lines_sum=D("23.00"))  # 15% over
    assert (found.trust, found.held) == ("check_lines", False)


def test_a_gap_over_a_quarter_of_the_total_is_held():
    assert check(lines_sum=D("25.01")).held
    assert check(lines_sum=D("14.99")).held
    assert not check(lines_sum=D("25.00")).held


def test_an_amount_the_scan_does_not_print_holds_a_receipt_that_misses():
    assert check(lines_sum=D("21.00"), unscanned=1).held
    assert check(lines_sum=D("21.00"), flags=["total_not_in_scan"]).held


def test_an_amount_the_scan_does_not_print_never_holds_a_receipt_that_adds_up():
    found = check(unscanned=2)
    assert (found.trust, found.held) == ("adds_up", False)


def test_nothing_read_is_couldnt_read():
    found = check(item_lines=0, lines_sum=D("0"))
    assert (found.trust, found.held) == ("couldnt_read", False)


def test_without_a_printed_total_the_lines_are_checked_never_held():
    found = check(flags=["total_missing"], lines_sum=D("90.00"))
    assert (found.trust, found.held) == ("check_lines", False)


def test_the_gap_is_from_the_printed_total_and_none_without_one():
    assert reading_gap(check(lines_sum=D("20.30"))) == D("0.30")
    assert reading_gap(check(lines_sum=D("20.00"))) is None  # adds up
    # The stored total stands in for one the receipt never printed: nothing to be off from.
    assert reading_gap(check(flags=["total_missing"])) is None
    assert reading_gap(check(flags=["total_missing"], lines_sum=D("90.00"))) is None


def test_only_a_draft_is_held():
    for status in ("reviewed", "committed"):
        found = check(status=status, lines_sum=D("90.00"))
        assert (found.trust, found.held) == ("check_lines", False), status


def test_a_purchase_entered_by_hand_has_no_trust():
    found = check(source="manual", lines_sum=D("90.00"))
    assert (found.trust, found.held) == (None, False)
