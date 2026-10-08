"""Tidy product names and brands as they are saved (2P). Pure: no database, no I/O.

Names arrive from store pages, receipts and typing. A page's name with a trademark
sign after the brand should be stored without it, so a person can type the name and
a search or a duplicate check finds it with confidence:

- trademark signs (U+2122, U+00AE, U+00A9, U+2120) are removed;
- curly quotes and apostrophes become straight ones, and a double prime (U+2033)
  becomes an inch mark ("), which is kept;
- en and em dashes and the minus sign become a hyphen;
- non-breaking and other unusual spaces become a plain space, and invisible
  characters (zero-width spaces, byte-order marks) are removed;
- runs of spaces collapse to one, and the ends are trimmed. A space left in front of
  a comma, colon or semicolon by a removed sign is dropped too.

Nothing else changes: letters, case, digits and other punctuation are kept.
"""

from __future__ import annotations

import re

_MARKS = [0x2122, 0x00AE, 0x00A9, 0x2120]
_INVISIBLE = [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF]
_SINGLE_QUOTES = [0x2018, 0x2019, 0x201A, 0x201B, 0x2032, 0x02BC]
_DOUBLE_QUOTES = [0x201C, 0x201D, 0x201E, 0x201F, 0x2033]
_DASHES = [0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212]
_SPACES_ODD = [0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000]
_WHITESPACE = [ord(c) for c in "\t\n\r\v\f"]

_TABLE: dict[int, str | None] = {
    **dict.fromkeys(_MARKS + _INVISIBLE),
    **dict.fromkeys(_SINGLE_QUOTES, "'"),
    **dict.fromkeys(_DOUBLE_QUOTES, '"'),
    **dict.fromkeys(_DASHES, "-"),
    **dict.fromkeys(_SPACES_ODD + _WHITESPACE, " "),
}
_SPACES = re.compile(r" {2,}")
_BEFORE_PUNCT = re.compile(r" +([,;:])")


def tidy(text: str | None) -> str | None:
    """The text with signs removed and typography made plain; None stays None."""
    if text is None:
        return None
    out = text.translate(_TABLE)
    out = _SPACES.sub(" ", out)
    return _BEFORE_PUNCT.sub(r"\1", out).strip()
