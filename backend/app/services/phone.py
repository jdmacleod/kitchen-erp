"""Store phone numbers: kept as typed, checked for shape, matched on digits (1F).

No country is assumed and nothing is reformatted: "(555) 555-0100" stays as the
person typed it. A number is accepted when it has 7 to 15 digits and nothing but
digits, spaces and ``+ ( ) - .``. Matching compares digits only.
"""

from __future__ import annotations

import re

MAX_LENGTH = 40
_ALLOWED = re.compile(r"^[0-9 +().\-]+$")


def digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def same_number(a: str, b: str) -> bool:
    """Whether two digit strings are one number, allowing a country code (1 to 3
    digits) on one side only: the digits of "+1 555 555 0142" and of
    "555 555 0142" match."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= 7 and long.endswith(short) and len(long) - len(short) <= 3


def phone_error(value: str) -> str | None:
    """Why ``value`` is not a phone number, or None when it is one."""
    if len(value) > MAX_LENGTH or not _ALLOWED.match(value):
        return "Use digits, spaces and + ( ) - . only."
    if not 7 <= len(digits(value)) <= 15:
        return "Needs 7 to 15 digits."
    return None


def from_tag(value: object) -> str | None:
    """The first well-formed number in an OSM ``phone`` tag, which may list several."""
    if not isinstance(value, str):
        return None
    for raw in value.split(";"):
        part = raw.strip()
        if part and phone_error(part) is None:
            return part
    return None
