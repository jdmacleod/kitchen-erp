"""Weighed-item labels (03, 1H): the item code and price inside a store's barcode.

In-store labels for meat, deli and cheese use UPC-A numbers starting with 2
(the GS1 variable-measure range). Where the item code and the price or weight
sit varies by retailer, so a vendor carries its layout (``vendor.rw_layout``);
positions are 0-based into the twelve digits, the leading 2 at 0 and the check
digit at 11. Pure; no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from app.catalog.identifiers import gs1_ok

PriceKind = Literal["price_cents", "weight_hundredths_lb", "none"]


@dataclass(frozen=True)
class RwLayout:
    item_start: int = 1
    item_len: int = 5
    price_start: int = 6
    price_len: int = 5
    price_kind: PriceKind = "price_cents"

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> RwLayout:
        if not data:
            return DEFAULT_LAYOUT
        layout = cls(**data)
        for start, length in (
            (layout.item_start, layout.item_len),
            (layout.price_start, layout.price_len),
        ):
            if start < 1 or length < 1 or start + length > 11:
                raise ValueError(
                    "rw_layout fields must sit between the leading 2 and the check digit"
                )
        return layout


DEFAULT_LAYOUT = RwLayout()


@dataclass(frozen=True)
class RandomWeight:
    item: str
    price: Decimal | None
    weight: Decimal | None


def as_upca(code: str) -> str | None:
    """Twelve digits of a weighed-item UPC-A, from 12 digits or a zero-padded 13 or 14."""
    code = code.strip()
    if not code.isdigit():
        return None
    if len(code) in (13, 14) and code[: len(code) - 12] == "0" * (len(code) - 12):
        code = code[-12:]
    if len(code) != 12 or code[0] != "2" or not gs1_ok(code):
        return None
    return code


def parse_random_weight(code: str, layout: RwLayout = DEFAULT_LAYOUT) -> RandomWeight | None:
    """The item code and price or weight in a weighed-item label, or None if it isn't one.

    A zeroed price field (as on a web listing) gives no price rather than 0.00.
    """
    upca = as_upca(code)
    if upca is None:
        return None
    item = upca[layout.item_start : layout.item_start + layout.item_len]
    field = upca[layout.price_start : layout.price_start + layout.price_len]
    amount = int(field)
    price = weight = None
    if amount and layout.price_kind == "price_cents":
        price = Decimal(amount) / 100
    elif amount and layout.price_kind == "weight_hundredths_lb":
        weight = Decimal(amount) / 100
    return RandomWeight(item=item, price=price, weight=weight)
