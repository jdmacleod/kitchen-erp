"""Shopping lists and the current kitchen (17, Phase 4A).

Amounts cross the wire as strings and come back as (amount, unit) pairs; an
item names an ingredient or a product, never both or neither, which the
database checks again.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import IngredientSummary

ListStatus = Literal["open", "archived"]
ItemKind = Literal["ingredient", "product"]
AddedFrom = Literal["manual", "ingredient", "recipe", "stock"]
Note = Annotated[str, Field(max_length=500)]
MinQuality = Annotated[int, Field(ge=1, le=5)]
UnitCode = Annotated[str, Field(min_length=1, max_length=16)]


# --- the current kitchen -------------------------------------------------------------


class SettingsOut(ApiModel):
    """``current_home_base_id`` is what the client sends on kitchen-scoped calls;
    ``suggested_home_base_id`` is the only kitchen when there is exactly one, for
    the client's fallback chain when the setting is empty."""

    current_home_base_id: uuid.UUID | None
    suggested_home_base_id: uuid.UUID | None


class SettingsIn(ApiModel):
    current_home_base_id: uuid.UUID | None


# --- amounts -----------------------------------------------------------------------------


class AmountIn(ApiModel):
    amount: Annotated[Decimal, Field(gt=0)]
    unit: UnitCode


class AmountOut(ApiModel):
    seq: int
    amount: DecimalStr
    unit: str


# --- items --------------------------------------------------------------------------------


class UserBrief(ApiModel):
    id: uuid.UUID
    display_name: str


class ProductBrief(ApiModel):
    id: uuid.UUID
    name: str
    ingredient_id: uuid.UUID
    pack_qty: DecimalStr | None
    pack_unit: str | None
    pack_count: int | None
    piece_name: str | None
    quality_rating: int | None
    active: bool


class ItemOut(ApiModel):
    id: uuid.UUID
    list_id: uuid.UUID
    kind: ItemKind
    ingredient: IngredientSummary | None
    product: ProductBrief | None
    amounts: list[AmountOut]
    min_quality: int | None
    note: str | None
    deferrable: bool
    added_by: UserBrief
    added_from: AddedFrom
    recipe_id: uuid.UUID | None
    carried_from_item_id: uuid.UUID | None
    claimed_by: UserBrief | None
    claimed_at: datetime | None
    checked_by: UserBrief | None
    checked_at: datetime | None
    created_at: datetime
    # The optimistic token: a PATCH carries back the value it last saw.
    updated_at: datetime


class _ItemFields(ApiModel):
    @staticmethod
    def _check_target(kind: str, ingredient_id: object, product_id: object) -> None:
        if kind == "ingredient" and (ingredient_id is None or product_id is not None):
            raise ValueError("an ingredient item names an ingredient and no product")
        if kind == "product" and (product_id is None or ingredient_id is not None):
            raise ValueError("a product item names a product and no ingredient")


class ItemCreate(_ItemFields):
    home_base_id: uuid.UUID
    kind: ItemKind
    ingredient_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    amounts: list[AmountIn] = Field(default_factory=list, max_length=20)
    min_quality: MinQuality | None = None
    note: Note | None = None
    deferrable: bool = False
    added_from: AddedFrom = "manual"
    recipe_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ItemCreate:
        self._check_target(self.kind, self.ingredient_id, self.product_id)
        if self.kind == "product" and self.min_quality is not None:
            raise ValueError("min_quality applies to ingredient items only")
        return self


class ItemPatch(ApiModel):
    """Edits the stated fields; ``updated_at`` is required and must be the value
    the client last saw (409 ``stale_item`` otherwise)."""

    updated_at: datetime
    amounts: list[AmountIn] | None = Field(default=None, max_length=20)
    min_quality: MinQuality | None = None
    note: Note | None = None
    deferrable: bool | None = None


# --- lists --------------------------------------------------------------------------------


class ListOut(ApiModel):
    id: uuid.UUID
    home_base_id: uuid.UUID
    home_base_name: str
    status: ListStatus
    created_at: datetime
    archived_at: datetime | None
    unchecked: list[ItemOut]
    checked: list[ItemOut]


class ListSummary(ApiModel):
    id: uuid.UUID
    home_base_id: uuid.UUID
    status: ListStatus
    created_at: datetime
    archived_at: datetime | None
    item_count: int
    unchecked_count: int


class ListsOut(ApiModel):
    items: list[ListSummary]


class FromRecipeIn(ApiModel):
    home_base_id: uuid.UUID
    recipe_id: uuid.UUID
    scale: Annotated[Decimal, Field(gt=0, le=100)] = Decimal(1)


class SkippedLine(ApiModel):
    raw_name: str
    reason: Literal["unmatched"]


class FromRecipeOut(ApiModel):
    list: ListOut
    added: int  # items created
    merged: int  # lines folded into an item already on the list
    skipped: list[SkippedLine]


class ArchiveIn(ApiModel):
    home_base_id: uuid.UUID
    carry_over: bool = True


class ArchiveOut(ApiModel):
    archived: ListOut
    list: ListOut  # the new open list
