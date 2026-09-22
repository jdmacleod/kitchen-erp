"""Receipt line text normalization. Pure and versioned: alias quality depends on it.

Uppercase, collapse whitespace, strip leading item codes and PLU digits, strip
trailing tax and category flags, remove embedded price and quantity tokens.
Idempotent: normalize(normalize(x)) == normalize(x).
"""

from __future__ import annotations

import re

NORMALIZE_VERSION = "1"

_WS = re.compile(r"\s+")
# Leading item codes: 4+ digits, optionally with a letter prefix, possibly several.
_LEADING_CODE = re.compile(r"^(?:[A-Z]?\d{4,}\s+)+")
# Trailing flags such as F, T, N, B, TX, FS, NF, or an asterisk, possibly repeated.
_TRAILING_FLAG = re.compile(r"(?:\s+(?:[FTNBEX]|TX|FS|NF|TF|\*+))+$")
# Money and price-per-unit tokens: $3.99, -1.50, 3.99/LB, @ 3.99, 2 @ 1.99, 2.31 LB @ 3.99/LB.
_PRICE = re.compile(
    r"(?:(?:\d+(?:\.\d+)?\s*(?:LB|LBS|KG|OZ|EA)?\s*@\s*)?\$?-?\d+\.\d{2}(?:\s*/\s*(?:LB|KG|OZ|EA))?)"
)
_BARE_AT = re.compile(r"\s*@\s*")
_NON_TEXT = re.compile(r"[^A-Z0-9&/%'\- ]+")
_DASH_RUN = re.compile(r"(?:\s*-\s*)+$|^(?:\s*-\s*)+")


def normalize_receipt_text(raw: str) -> str:
    text = raw.upper()
    text = _PRICE.sub(" ", text)
    text = _BARE_AT.sub(" ", text)
    text = _NON_TEXT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    text = _LEADING_CODE.sub("", text)
    text = _TRAILING_FLAG.sub("", text)
    text = _DASH_RUN.sub("", text)
    text = _WS.sub(" ", text).strip()
    return text
