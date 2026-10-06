"""Version 1 of the receipt-text normalizer, frozen for the differential test.

A copy of app/services/normalize.py as it was before version 2 (#125). It must
never change: tests/test_normalize.py compares the live normalizer against it, so
every difference between the versions is one the test lists on purpose.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_LEADING_CODE = re.compile(r"^(?:[A-Z]?\d{4,}\s+)+")
_PRICE = re.compile(
    r"(?:(?:\d++(?:\.\d++)?\s*+(?:LBS|LB|KG|OZ|EA)?\s*+@\s*+)?"
    r"\$?-?\d++\.\d{2}(?:\s*+/\s*+(?:LB|KG|OZ|EA))?)"
)
_BARE_AT = re.compile(r"@+")
_NON_TEXT = re.compile(r"[^A-Z0-9&/%\'\- ]+")

_FLAG_TOKEN = frozenset({"F", "T", "N", "B", "E", "X", "TX", "FS", "NF", "TF"})
_DASH_AND_SPACE = "- "


def _strip_trailing_flags(text: str) -> str:
    parts = text.split(" ")
    while len(parts) > 1 and (parts[-1] in _FLAG_TOKEN or set(parts[-1]) == {"*"}):
        parts.pop()
    return " ".join(parts)


def normalize_v1(raw: str) -> str:
    text = raw.upper()
    text = _PRICE.sub(" ", text)
    text = _BARE_AT.sub(" ", text)
    text = _NON_TEXT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    text = _LEADING_CODE.sub("", text)
    text = _strip_trailing_flags(text)
    text = text.strip(_DASH_AND_SPACE)
    text = _WS.sub(" ", text).strip()
    return text
