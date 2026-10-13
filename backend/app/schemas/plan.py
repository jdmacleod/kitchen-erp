"""Shopping lists, the current kitchen, drive times and trip plans (17, Phase 4).

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
    # Remove the recipe's unchecked items first and add its lines again at this scale.
    replace: bool = False


class SkippedLine(ApiModel):
    raw_name: str
    reason: Literal["unmatched"]


class FromRecipeOut(ApiModel):
    list: ListOut
    added: int  # items created
    merged: int  # lines folded into an item already on the list
    skipped: list[SkippedLine]
    # The recipe already had unchecked items on the list and nothing was changed
    # (criterion 4: "again, nothing changes"); ``existing_items`` counts them.
    already_on_list: bool
    existing_items: int


class ArchiveIn(ApiModel):
    home_base_id: uuid.UUID
    carry_over: bool = True


class ArchiveOut(ApiModel):
    archived: ListOut
    list: ListOut  # the new open list


# --- drive times (17, 4B) ----------------------------------------------------------------

LegSource = Literal["osrm", "estimate"]


class TravelLegOut(ApiModel):
    """A stored free-flow leg; ``estimate`` means the routing engine did not answer
    for this pair and the figures are haversine times a road factor."""

    from_place_id: uuid.UUID
    to_place_id: uuid.UUID
    seconds: int
    meters: int
    source: LegSource
    profile: str
    engine_version: str | None
    computed_at: datetime


class RecomputeLegsIn(ApiModel):
    """``only_estimates`` drops and recomputes estimated legs; false drops every leg."""

    only_estimates: bool = True


class RecomputeLegsOut(ApiModel):
    dropped: int
    osrm: int
    estimated: int


# --- trip plans (17, 4B) -------------------------------------------------------------------

PlanStatus = Literal["planned", "underway", "done", "abandoned"]
VariantKind = Literal["fastest", "cheapest", "balanced"]
ValueOfTime = Annotated[Decimal, Field(ge=0, max_digits=8, decimal_places=2)]


class TripCreate(ApiModel):
    """Plan a trip from the kitchen's open list. Origin defaults to the kitchen's
    place, destination to the origin, value of time to the household setting,
    overhead to ten minutes."""

    home_base_id: uuid.UUID
    origin_place_id: uuid.UUID | None = None
    destination_place_id: uuid.UUID | None = None
    planned_at: datetime
    value_of_time_per_hour: ValueOfTime | None = None
    max_stops: Annotated[int, Field(ge=1, le=4)] = 3
    mileage_rate: Annotated[Decimal, Field(ge=0, max_digits=8, decimal_places=4)] | None = None
    stop_overhead_min: Annotated[int, Field(ge=0, le=32767)] | None = None


class Annotation(ApiModel):
    """``amount`` is money and ``minutes`` a duration, both as strings; ``reason``
    accompanies ``unsourceable``."""

    model_config = ApiModel.model_config | {"extra": "allow"}

    kind: str
    item_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    amount: DecimalStr | None = None
    minutes: DecimalStr | None = None
    text: str


class StopItemOut(ApiModel):
    id: uuid.UUID
    list_item_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    packs: DecimalStr | None
    pack_price: DecimalStr | None
    line_cost: DecimalStr
    observation_id: uuid.UUID
    price_stale: bool
    forced: bool
    actual_price: DecimalStr | None
    actual_qty: DecimalStr | None
    actual_unit: str | None
    got_here: bool


class StopOut(ApiModel):
    """``leg_seconds`` and ``leg_meters`` are the free-flow leg that reaches the
    stop; ``multiplier`` and ``arrive_at`` are what the planner applied at its
    projected departure."""

    id: uuid.UUID
    seq: int
    vendor_location_id: uuid.UUID
    vendor_location_name: str
    leg_seconds: int
    leg_meters: int
    leg_source: LegSource
    multiplier: DecimalStr
    arrive_at: datetime
    overhead_min: int
    subtotal: DecimalStr
    hours_known: bool
    done_at: datetime | None
    purchase_id: uuid.UUID | None
    items: list[StopItemOut]


class Trade(ApiModel):
    """The marginal trade against ``balanced``: more (positive) or less money
    out of pocket (items plus mileage), and more or fewer minutes."""

    money: DecimalStr
    minutes: DecimalStr


class VariantSummary(ApiModel):
    id: uuid.UUID
    kind: VariantKind
    item_cost: DecimalStr
    driving_seconds: int
    stop_seconds: int
    mileage_cost: DecimalStr
    total_cost: DecimalStr
    estimated: bool
    trade: Trade
    stop_count: int


class VariantOut(VariantSummary):
    annotations: list[Annotation]
    stops: list[StopOut]


class TripBase(ApiModel):
    id: uuid.UUID
    list_id: uuid.UUID
    home_base_id: uuid.UUID
    origin_place_id: uuid.UUID
    destination_place_id: uuid.UUID
    planned_at: datetime
    value_of_time_per_hour: DecimalStr
    max_stops: int
    mileage_rate: DecimalStr | None
    stop_overhead_min: int
    status: PlanStatus
    chosen_variant_id: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    chosen_at: datetime | None
    finished_at: datetime | None


class TripSummary(TripBase):
    variants: list[VariantSummary]


class TripOut(TripBase):
    variants: list[VariantOut]


class TripsOut(ApiModel):
    items: list[TripSummary]


class ChooseIn(ApiModel):
    variant_id: uuid.UUID
