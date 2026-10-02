"""OCR text as a soft witness to the amounts a reader found (R2-16).

A vision reader sees the printed total, so a reading can fit its lines to its own
misread total. OCR is a second, independent look: an amount that also appears in
the OCR text is "supported". Support is evidence for review, never a gate; the
reading benchmark (spec 04, 2J) measures how often it is right, and how often a
wrong amount finds support anyway, before Phase 1 relies on it.

Pure and deterministic: no I/O, no model.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# A numeric token: digits, with "." or "," allowed inside. Anything else ends it.
_TOKEN = re.compile(r"\d(?:[\d.,]*\d)?")
# A digit run shorter than this matches too much by chance ("12" is everywhere).
DIGIT_RUN_MIN = 3


def numeric_tokens(text: str) -> list[str]:
    """The numeric tokens of ``text``, in reading order."""
    return _TOKEN.findall(text)


def _value(token: str) -> Decimal | None:
    """A token as an amount: "3.49", "3,49" (decimal comma) or "1,234.56"."""
    # With a point present, commas group thousands; alone, a comma is the decimal.
    token = token.replace(",", "") if "." in token else token.replace(",", ".")
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def _digits(amount: Decimal) -> str:
    """The digits an amount prints as, separator dropped: 3.49 -> "349"."""
    return format(abs(amount).quantize(Decimal("0.01")), "f").replace(".", "")


def _exact(amount: Decimal, token: str) -> bool:
    value = _value(token)
    return value is not None and value == abs(amount)


def _digit_run(amount: Decimal, token: str) -> bool:
    # Leading zeros don't count toward the length: 0.45 is the run "45", too short.
    digits = _digits(amount).lstrip("0")
    run = token.replace(".", "").replace(",", "").lstrip("0")
    return len(digits) >= DIGIT_RUN_MIN and run == digits


def witness_amounts(amounts: list[Decimal], ocr_text: str, *, digit_runs: bool) -> list[bool]:
    """Which of ``amounts`` the OCR text supports, in the same order.

    Exact matches are assigned first, in line order; then, if ``digit_runs``,
    matches of the digits alone (OCR often drops the decimal point). Each token
    supports at most one amount, so a price printed once cannot vouch for two
    lines.
    """
    tokens = numeric_tokens(ocr_text)
    used = [False] * len(tokens)
    supported = [False] * len(amounts)
    passes = [_exact, _digit_run] if digit_runs else [_exact]
    for matches in passes:
        for i, amount in enumerate(amounts):
            if supported[i]:
                continue
            for j, token in enumerate(tokens):
                if not used[j] and matches(amount, token):
                    used[j] = supported[i] = True
                    break
    return supported


def any_support(amount: Decimal, ocr_text: str, *, digit_runs: bool) -> bool:
    """Whether any token supports ``amount`` alone, ignoring other lines."""
    return witness_amounts([amount], ocr_text, digit_runs=digit_runs)[0]
