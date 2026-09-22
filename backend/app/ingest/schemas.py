"""Pydantic models that model output must validate against.

The JSON schema of each model is sent to the model server as the ``format``
constraint; the reply is parsed with ``parse_float=Decimal`` and validated
here. Anything that does not validate is discarded (non-negotiable 7).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator

# Money and quantities are asked for as decimal strings so that the constrained
# decoder never emits a JSON float; a numeric reply still parses (as Decimal).
_DECIMAL_SCHEMA = {"type": "string", "pattern": r"^-?[0-9]+(\.[0-9]+)?$"}
Money = Annotated[Decimal, WithJsonSchema(_DECIMAL_SCHEMA)]

LineKind = Literal["item", "discount", "tax", "deposit", "fee"]


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", str_max_length=2000)


class ReceiptHeader(ModelOutput):
    """What the header stage asks the model for."""

    merchant_name: str | None = Field(
        default=None, description="The store or merchant name as printed, or null."
    )
    store_identifier: str | None = Field(
        default=None,
        description="Store number or code printed near the top, digits or short code, or null.",
    )
    address_text: str | None = Field(
        default=None, description="Address line(s) as printed, joined with ', ', or null."
    )
    phone_text: str | None = Field(default=None, description="Phone number as printed, or null.")
    purchased_at_local: str | None = Field(
        default=None,
        description="Purchase date and time as printed, formatted YYYY-MM-DD HH:MM in 24-hour "
        "time; YYYY-MM-DD alone when no time is printed; null when no date is printed.",
    )
    subtotal: Money | None = Field(default=None, description="Printed subtotal, or null.")
    tax: Money | None = Field(default=None, description="Printed tax total, or null.")
    total: Money | None = Field(default=None, description="Printed grand total, or null.")

    @field_validator("merchant_name", "store_identifier", "address_text", "phone_text")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ReceiptLine(ModelOutput):
    raw_text: str = Field(
        min_length=1, description="The line exactly as printed, including codes and prices."
    )
    line_kind: LineKind = Field(
        description="item for something bought; discount for a reduction (coupon, loyalty, "
        "member saving); tax for a tax line; deposit for a container deposit or CRV / "
        "redemption value; fee for a bag fee or surcharge."
    )
    qty: Money | None = Field(
        default=None,
        description="Quantity bought: the weight for weighed items (2.31 for '2.31 lb @ "
        "3.99/lb'), the count for '2 @ 1.99' (2), 1 for a plain item, null when unknown.",
    )
    unit: str | None = Field(
        default=None,
        description="Unit of qty: lb, kg, oz, g for weighed items; each for counted items; null "
        "when qty is null.",
    )
    unit_price: Money | None = Field(
        default=None, description="Price per unit when printed (3.99 for '@ 3.99/lb'), else null."
    )
    line_total: Money = Field(
        description="The amount printed for this line as a positive number. Discounts are "
        "given as the positive amount taken off."
    )
    parent_index: int | None = Field(
        default=None,
        ge=0,
        description="For a discount or deposit printed directly beneath the item it belongs "
        "to: the 0-based index of that item line in this list. Null otherwise.",
    )

    @field_validator("line_total", "qty", "unit_price")
    @classmethod
    def _magnitude(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else abs(value)


class ReceiptLines(ModelOutput):
    """What the lines stage asks the model for."""

    lines: list[ReceiptLine] = Field(
        max_length=500,
        description="Every purchased item, discount, tax, deposit and fee line in receipt "
        "order. Do not include headers, footers, subtotal, total, tender, change, "
        "loyalty summaries or blank lines.",
    )
