"""Item codes on receipt lines (04, 2K). Pure: no I/O.

A code is read from the raw line, before normalization strips leading codes
from ``raw_text_norm``, with anchored patterns that cannot backtrack:

- a 12–14 digit run with a valid check digit is a GTIN (the barcode rung);
- a weighed-item label (UPC-A starting with 2) is its ``rw_item``, read with the
  vendor's label layout;
- a token in the position the vendor prints codes (``vendor.code_position``,
  for example the leading token) is a ``vendor_sku``, ``rw_item`` or ``plu``.

Only those resolve a line. A digit run anywhere else, or on a receipt from a
vendor without ``code_position``, is only ever a suggestion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.catalog.barcodes import RwLayout, as_upca, parse_random_weight
from app.catalog.identifiers import gs1_ok

# Anchored, one digit class, one quantifier: linear on any input.
_LEADING = re.compile(r"^\s{0,8}(\d{3,14})(?=\s)")
_GTIN = re.compile(r"(?<!\d)(\d{12,14})(?!\d)")
_DIGITS = re.compile(r"(?<!\d)(\d{4,14})(?!\d)")
VENDOR_SCHEMES = ("vendor_sku", "rw_item", "plu")


@dataclass(frozen=True)
class LineCodes:
    """What a line's raw text says about codes."""

    gtins: tuple[str, ...] = ()
    # (scheme, value) pairs that may resolve the line for its vendor.
    positioned: tuple[tuple[str, str], ...] = ()
    # Digit runs that may only ever suggest.
    loose: tuple[str, ...] = ()

    @property
    def offer(self) -> tuple[str, str] | None:
        """The code a reviewer may be offered to remember, as (scheme, value)."""
        return self.positioned[0] if self.positioned else None


def gtins_in(raw: str) -> tuple[str, ...]:
    return tuple(m.zfill(14) for m in _GTIN.findall(raw or "") if gs1_ok(m))


def read(
    raw: str, code_position: dict[str, Any] | None, rw_layout: dict[str, Any] | None
) -> LineCodes:
    raw = raw or ""
    gtins = gtins_in(raw)
    positioned: list[tuple[str, str]] = []
    # A weighed-item label: its item code, by the vendor's layout.
    for run in _GTIN.findall(raw):
        if as_upca(run) is not None:
            label = parse_random_weight(run, RwLayout.from_json(rw_layout))
            if label is not None:
                positioned.append(("rw_item", label.item))
    if code_position and code_position.get("kind") == "leading_token":
        found = _LEADING.match(raw)
        if found:
            code = found.group(1)
            low, high = int(code_position["min_len"]), int(code_position["max_len"])
            if low <= len(code) <= high and code not in {g.lstrip("0") for g in gtins}:
                positioned.extend((scheme, code) for scheme in ("vendor_sku", "rw_item", "plu"))
    taken = {value for _, value in positioned} | set(gtins) | {g.lstrip("0") for g in gtins}
    loose = tuple(m for m in _DIGITS.findall(raw) if m not in taken)
    return LineCodes(gtins=gtins, positioned=tuple(dict.fromkeys(positioned)), loose=loose)
