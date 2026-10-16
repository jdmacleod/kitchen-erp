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

Version 2 (#125) reads a comma as a decimal point inside a price, because OCR
often prints "7,25" for 7.25: the fragment is removed exactly as "7.25" is, where
version 1 left "7 25" in the wording and split one product into two groups. No
other output changed; tests/test_normalize.py checks that against a frozen copy
of version 1. Migration 0030 re-keys the stored lines, aliases and suggestions.

Version 3 (#258) repeats the clean-up of the ends until nothing more comes off.
Version 2 cleaned them once, so stripping a trailing dash could leave a flag at
the end ("0.B-" gave "0 B", which normalized again to "0"), and stripping a
leading dash could leave an item code at the start. Version 3 gives what
version 2 gave when run again and again until the text stopped changing, which
for every other text is what version 2 gave once. Migration 0049 re-keys the
stored lines, aliases and suggestions.

Version 4 (#399) keeps a decimal point between two digits, so a pack size reads
as printed: "OAT MILK 1.5L" stays "OAT MILK 1.5L" where version 3 gave
"OAT MILK 1 5L", which named no size at all. Every other point still goes, and
so does a price ("1.25" is removed before this step, as before). Text with no
point or comma between two digits normalizes exactly as in version 3. A volume printed
with two decimals ("1.75L") is no longer taken for a price. A comma between digits
outside a price is still punctuation: "6,4" is as likely a count as a size.
Migration 0059 re-keys the stored lines, aliases and suggestions.

Any edit that alters output for any input must bump NORMALIZE_VERSION, because
`purchase_line.raw_text_norm` is stored and aliases are matched against it.
"""

from __future__ import annotations

import re

NORMALIZE_VERSION = "4"

_WS = re.compile(r"\s+")
# A leading item code: 4+ digits, optionally with a letter prefix. Matched against
# one token, so it is anchored at both ends.
_CODE_TOKEN = re.compile(r"[A-Z]?\d{4,}")
# Money and price-per-unit tokens: $3.99, -1.50, 3.99/LB, @ 3.99, 2 @ 1.99, 2.31 LB @ 3.99/LB,
# and the same with a comma for the decimal point (version 2).
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
    r"(?:(?:\d++(?:[.,]\d++)?\s*+(?:LBS|LB|KG|OZ|EA)?\s*+@\s*+)?"
    r"\$?-?\d++[.,]\d{2}(?!M?L\b)(?:\s*+/\s*+(?:LB|KG|OZ|EA))?)"
)
# The (?!M?L\b) above is version 4's: a volume glued to its number ("1.75L",
# "0.25ML") is a pack size, not a price. A weight glued to its number ("2.31LB")
# is still removed, because a weighed item's weight changes from one purchase to
# the next and would split one product's wording into many keys.
# A bare "@" left over from a quantity. The surrounding whitespace used to be part
# of this pattern, which made it rescan every run of spaces; the whitespace
# collapse below already handles that, so matching the character alone is both
# linear and equivalent.
_BARE_AT = re.compile(r"@+")
_NON_TEXT = re.compile(r"[^A-Z0-9&/%\'\-. ]+")
# A point kept by _NON_TEXT that is not between two digits (version 4). Each
# lookaround reads one character, so this is linear.
_STRAY_POINT = re.compile(r"(?<!\d)\.|\.(?!\d)")

# Trailing tax and category flags: F, T, N, B, TX, FS, NF, or an asterisk. By the
# time this runs the text is whitespace-collapsed, so "is the last token a flag"
# is a token question rather than a regex one, and popping tokens is linear where
# the anchored pattern it replaces was quadratic.
_FLAG_TOKEN = frozenset({"F", "T", "N", "B", "E", "X", "TX", "FS", "NF", "TF"})
_DASH = "-"


def _is_flag(token: str) -> bool:
    return token in _FLAG_TOKEN or set(token) == {"*"}


def _clean_ends(tokens: list[str]) -> list[str]:
    """Drop leading item codes, trailing flags and the dashes at either end, until
    none is left. Each removal can uncover another (a dash stripped from the last
    token can leave a flag there), so the three are repeated rather than run once.

    Leading codes go only while another token follows, and flags only while
    another token precedes: a lone code or flag is the whole name and is kept.
    Each pass that changes anything removes at least one character from an end,
    so the loop is linear in the text, not quadratic.
    """
    start, end = 0, len(tokens)  # tokens[start:end] is the text kept so far
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


def normalize_receipt_text(raw: str) -> str:
    text = raw.upper()
    text = _PRICE.sub(" ", text)
    text = _BARE_AT.sub(" ", text)
    text = _NON_TEXT.sub(" ", text)
    text = _STRAY_POINT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return " ".join(_clean_ends(text.split(" ") if text else []))
