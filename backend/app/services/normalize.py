"""Receipt line text normalization. Pure and versioned: alias quality depends on it.

Uppercase, collapse whitespace, strip leading item codes and PLU digits, strip
trailing tax and category flags, remove embedded price and quantity tokens.
Idempotent: normalize(normalize(x)) == normalize(x).

Every pattern here is applied to text that came from OCR or from a language
model reading a photograph, which `CLAUDE.md` treats as untrusted. A regular
expression that backtracks quadratically is a way to spend a worker's CPU on a
crafted receipt, so the patterns below are written to fail fast instead:
possessive quantifiers where a quantifier could otherwise give characters back,
and plain string operations where a regex bought nothing.

Behaviour is unchanged from version 1 and the version is therefore unchanged.
Any edit that alters output for any input must bump NORMALIZE_VERSION, because
`purchase_line.raw_text_norm` is stored and aliases are matched against it.
"""

from __future__ import annotations

import re

NORMALIZE_VERSION = "1"

_WS = re.compile(r"\s+")
# Leading item codes: 4+ digits, optionally with a letter prefix, possibly several.
_LEADING_CODE = re.compile(r"^(?:[A-Z]?\d{4,}\s+)+")
# Money and price-per-unit tokens: $3.99, -1.50, 3.99/LB, @ 3.99, 2 @ 1.99, 2.31 LB @ 3.99/LB.
#
# Possessive quantifiers against the quadratic scan CodeQL flagged. A digit run
# can never usefully be handed back here — what follows a number is always ".",
# whitespace or "@", never another digit — so refusing to backtrack changes no
# match while removing the backtracking inside each attempt. LBS precedes LB so
# the longer unit wins without relying on backtracking to find it.
#
# A leading (?<![\d.]) was tried here to stop the attempt being made from inside
# a digit run at all, which would have made the scan linear. It is wrong: after
# a match is substituted the scan resumes at the next character, and the next
# price can legitimately begin there with a digit behind it — "$38.64644.58"
# holds two. The differential test against version 1 caught it.
_PRICE = re.compile(
    r"(?:(?:\d++(?:\.\d++)?\s*+(?:LBS|LB|KG|OZ|EA)?\s*+@\s*+)?"
    r"\$?-?\d++\.\d{2}(?:\s*+/\s*+(?:LB|KG|OZ|EA))?)"
)
# A bare "@" left over from a quantity. The surrounding whitespace used to be part
# of this pattern, which made it rescan every run of spaces; the whitespace
# collapse below already handles that, so matching the character alone is both
# linear and equivalent.
_BARE_AT = re.compile(r"@+")
_NON_TEXT = re.compile(r"[^A-Z0-9&/%\'\- ]+")

# Trailing tax and category flags: F, T, N, B, TX, FS, NF, or an asterisk. By the
# time this runs the text is whitespace-collapsed, so "is the last token a flag"
# is a token question rather than a regex one, and popping tokens is linear where
# the anchored pattern it replaces was quadratic.
_FLAG_TOKEN = frozenset({"F", "T", "N", "B", "E", "X", "TX", "FS", "NF", "TF"})
_DASH_AND_SPACE = "- "


def _strip_trailing_flags(text: str) -> str:
    """Drop trailing flag tokens. A lone flag is the whole name and is kept."""
    parts = text.split(" ")
    while len(parts) > 1 and (parts[-1] in _FLAG_TOKEN or set(parts[-1]) == {"*"}):
        parts.pop()
    return " ".join(parts)


def normalize_receipt_text(raw: str) -> str:
    text = raw.upper()
    text = _PRICE.sub(" ", text)
    text = _BARE_AT.sub(" ", text)
    text = _NON_TEXT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    text = _LEADING_CODE.sub("", text)
    text = _strip_trailing_flags(text)
    # Leading and trailing runs of dashes and spaces. str.strip is linear; the
    # pattern it replaces nested two optional whitespace matches inside a repeat,
    # which is the shape CodeQL flagged as an inefficient regular expression.
    text = text.strip(_DASH_AND_SPACE)
    text = _WS.sub(" ", text).strip()
    return text
