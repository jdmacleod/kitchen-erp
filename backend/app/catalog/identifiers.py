"""Product codes (03, 1H): normalize, validate and classify. Pure; no I/O.

Barcodes are stored as GTIN-14: UPC-A, EAN-13, EAN-8 and GTIN-14 all pad to
fourteen digits, and UPC-E expands to UPC-A first. An eight-digit code that is
valid both as EAN-8 and as UPC-E is never read silently: the caller names the
symbology. Anything that is not a barcode is kept exactly as entered, as
``other``. Produce codes, store item numbers and weighed-item codes belong to a
vendor and are classified by their caller, not here.
"""

from __future__ import annotations

from typing import Literal

Scheme = Literal["gtin", "plu", "vendor_sku", "rw_item", "other"]
Symbology = Literal["ean8", "upce"]

VENDOR_SCOPED: frozenset[str] = frozenset({"plu", "vendor_sku", "rw_item"})
GTIN_LENGTHS = (8, 12, 13, 14)


class InvalidGtin(ValueError):
    """A barcode-length run of digits whose check digit is wrong."""


class AmbiguousCode(ValueError):
    """Eight digits valid both as EAN-8 and as UPC-E, with no symbology given."""


def check_digit(body: str) -> int:
    """The GS1 check digit for ``body`` (every digit but the last)."""
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def gs1_ok(digits: str) -> bool:
    return len(digits) >= 2 and digits.isdigit() and check_digit(digits[:-1]) == int(digits[-1])


def upce_to_upca(code: str) -> str | None:
    """Expand an eight-digit UPC-E (number system 0 or 1) to UPC-A, or None."""
    if len(code) != 8 or not code.isdigit() or code[0] not in "01":
        return None
    ns, d, check = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = f"{ns}{d[0]}{d[1]}{last}0000{d[2]}{d[3]}{d[4]}"
    elif last == "3":
        body = f"{ns}{d[0]}{d[1]}{d[2]}00000{d[3]}{d[4]}"
    elif last == "4":
        body = f"{ns}{d[0]}{d[1]}{d[2]}{d[3]}00000{d[4]}"
    else:
        body = f"{ns}{d[0]}{d[1]}{d[2]}{d[3]}{d[4]}0000{last}"
    return body + check


def _upce_ok(code: str) -> bool:
    upca = upce_to_upca(code)
    return upca is not None and gs1_ok(upca)


def gtin14(code: str, symbology: Symbology | None = None) -> str:
    """Normalize a barcode to GTIN-14, or raise InvalidGtin or AmbiguousCode."""
    if not code.isdigit() or len(code) not in GTIN_LENGTHS:
        raise InvalidGtin(code)
    if len(code) == 8:
        as_ean8, as_upce = gs1_ok(code), _upce_ok(code)
        if symbology == "ean8" or (as_ean8 and not as_upce and symbology is None):
            if not as_ean8:
                raise InvalidGtin(code)
            return code.zfill(14)
        if symbology == "upce" or (as_upce and not as_ean8 and symbology is None):
            if not as_upce:
                raise InvalidGtin(code)
            return str(upce_to_upca(code)).zfill(14)
        if as_ean8 and as_upce:
            if code.zfill(14) == str(upce_to_upca(code)).zfill(14):
                return code.zfill(14)  # both readings name the same product
            raise AmbiguousCode(code)
        raise InvalidGtin(code)
    if not gs1_ok(code):
        raise InvalidGtin(code)
    return code.zfill(14)


def classify_barcode(raw: str, symbology: Symbology | None = None) -> tuple[Scheme, str]:
    """What the product form's barcode field holds: a GTIN-14, or another code as entered.

    Eight, twelve, thirteen or fourteen digits must be a valid barcode
    (InvalidGtin, AmbiguousCode); every other code is ``other``.
    """
    code = raw.strip()
    if code.isdigit() and len(code) in GTIN_LENGTHS:
        return "gtin", gtin14(code, symbology)
    return "other", code


def display(scheme: str, value: str) -> str:
    """How a code is shown: a GTIN-14 at its shortest form (8, 12, 13 or 14 digits)."""
    if scheme != "gtin":
        return value
    if value.startswith("000000"):
        return value[6:]
    if value.startswith("00"):
        return value[2:]
    if value.startswith("0"):
        return value[1:]
    return value


def lookup_keys(raw: str) -> list[tuple[Scheme, str]]:
    """The (scheme, value) pairs a typed or scanned code could be stored under.

    Used to search: an invalid barcode finds nothing rather than raising, and an
    ambiguous eight-digit code is looked up both ways.
    """
    code = raw.strip()
    if not code:
        return []
    if code.isdigit() and len(code) in GTIN_LENGTHS:
        keys: list[tuple[Scheme, str]] = []
        for symbology in (None,) if len(code) != 8 else ("ean8", "upce"):
            try:
                keys.append(("gtin", gtin14(code, symbology)))
            except (InvalidGtin, AmbiguousCode):
                continue
        return list(dict.fromkeys(keys))
    return [("other", code)]
