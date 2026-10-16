"""Version 3 of the receipt-text normalizer, frozen for the differential test.

A copy of app/services/normalize.py as it was before version 4 (#399). It must
never change: tests/test_normalize.py checks that the live normalizer differs
from it only on text with a point or comma between two digits.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_CODE_TOKEN = re.compile(r"[A-Z]?\d{4,}")
_PRICE = re.compile(
    r"(?:(?:\d++(?:[.,]\d++)?\s*+(?:LBS|LB|KG|OZ|EA)?\s*+@\s*+)?"
    r"\$?-?\d++[.,]\d{2}(?:\s*+/\s*+(?:LB|KG|OZ|EA))?)"
)
_BARE_AT = re.compile(r"@+")
_NON_TEXT = re.compile(r"[^A-Z0-9&/%\'\- ]+")
_FLAG_TOKEN = frozenset({"F", "T", "N", "B", "E", "X", "TX", "FS", "NF", "TF"})
_DASH = "-"


def _is_flag(token: str) -> bool:
    return token in _FLAG_TOKEN or set(token) == {"*"}


def _clean_ends(tokens: list[str]) -> list[str]:
    start, end = 0, len(tokens)
    while True:
        changed = False
        while end - start > 1 and _CODE_TOKEN.fullmatch(tokens[start]):
            start += 1
            changed = True
        while end - start > 1 and _is_flag(tokens[end - 1]):
            end -= 1
            changed = True
        while start < end and not tokens[start].lstrip(_DASH):
            start += 1
            changed = True
        if start < end and tokens[start].startswith(_DASH):
            tokens[start] = tokens[start].lstrip(_DASH)
            changed = True
        while start < end and not tokens[end - 1].rstrip(_DASH):
            end -= 1
            changed = True
        if start < end and tokens[end - 1].endswith(_DASH):
            tokens[end - 1] = tokens[end - 1].rstrip(_DASH)
            changed = True
        if not changed:
            return tokens[start:end]


def normalize_v3(raw: str) -> str:
    text = raw.upper()
    text = _PRICE.sub(" ", text)
    text = _BARE_AT.sub(" ", text)
    text = _NON_TEXT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return " ".join(_clean_ends(text.split(" ") if text else []))
