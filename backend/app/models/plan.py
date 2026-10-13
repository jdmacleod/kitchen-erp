"""Shopping lists, the current kitchen, drive times and trip plans (17, Phase 4).

A kitchen has exactly one open list (a partial unique index, migration 0050). An
item is an ingredient, optionally with a minimum quality, or an exact product,
never both; its amounts are (amount, unit) rows, never JSONB, because
aggregation keeps the pairs it cannot convert. A claim is soft: who is getting
the item, cleared by a check. ``updated_at`` is the optimistic token a ``PATCH``
must carry back.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKey
from app.models.catalog import Ingredient, Product
from app.models.geo import HomeBase, VendorLocation
from app.models.identity import AppUser

LIST_STATUSES = ("open", "archived")
ITEM_KINDS = ("ingredient", "product")
ADDED_FROM = ("manual", "ingredient", "recipe", "stock")


class ShoppingList(UUIDPrimaryKey, Base):
    __tablename__ = "shopping_list"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'archived')", name="ck_shopping_list_status"),
        CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)",
            name="ck_shopping_list_archived_at",
        ),
        Index(
            "uq_shopping_list_open",
            "home_base_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    home_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("home_base.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    home_base: Mapped[HomeBase] = relationship(lazy="joined")
    items: Mapped[list[ShoppingListItem]] = relationship(
        back_populates="shopping_list",
        cascade="all, delete-orphan",
        order_by="ShoppingListItem.created_at",
    )


class ShoppingListItem(UUIDPrimaryKey, Base):
    __tablename__ = "shopping_list_item"
    __table_args__ = (
        CheckConstraint("kind IN ('ingredient', 'product')", name="ck_shopping_list_item_kind"),
        CheckConstraint(
            "(kind = 'ingredient' AND ingredient_id IS NOT NULL AND product_id IS NULL) OR "
            "(kind = 'product' AND product_id IS NOT NULL AND ingredient_id IS NULL)",
            name="ck_shopping_list_item_target",
        ),
        CheckConstraint(
            "min_quality IS NULL OR (kind = 'ingredient' AND min_quality BETWEEN 1 AND 5)",
            name="ck_shopping_list_item_min_quality",
        ),
        CheckConstraint(
            "added_from IN ('manual', 'ingredient', 'recipe', 'stock')",
            name="ck_shopping_list_item_added_from",
        ),
        CheckConstraint(
            "(claimed_by IS NULL) = (claimed_at IS NULL)", name="ck_shopping_list_item_claim"
        ),
    )

    list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shopping_list.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ingredient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), index=True
    )
    min_quality: Mapped[int | None] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(Text)
    deferrable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    added_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    added_from: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe.id", ondelete="SET NULL")
    )
    carried_from_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shopping_list_item.id", ondelete="SET NULL")
    )
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    shopping_list: Mapped[ShoppingList] = relationship(back_populates="items")
    amounts: Mapped[list[ShoppingListItemQty]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="ShoppingListItemQty.seq"
    )
    ingredient: Mapped[Ingredient | None] = relationship(lazy="joined")
    product: Mapped[Product | None] = relationship(lazy="joined")
    added_by_user: Mapped[AppUser] = relationship(foreign_keys=[added_by], lazy="joined")
    claimed_by_user: Mapped[AppUser | None] = relationship(foreign_keys=[claimed_by], lazy="joined")
    checked_by_user: Mapped[AppUser | None] = relationship(foreign_keys=[checked_by], lazy="joined")


class ShoppingListItemQty(UUIDPrimaryKey, Base):
    """One (amount, unit) pair of an item; several when units could not convert."""

    __tablename__ = "shopping_list_item_qty"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_shopping_list_item_qty_amount"),
        UniqueConstraint("item_id", "seq", name="uq_shopping_list_item_qty_seq"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shopping_list_item.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), ForeignKey("unit.code"), nullable=False)

    item: Mapped[ShoppingListItem] = relationship(back_populates="amounts")


class UserSetting(Base):
    """One typed row per user, one column per setting (17, 4A)."""

    __tablename__ = "user_setting"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    current_home_base_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("home_base.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --- drive times (17, 4B) ----------------------------------------------------------------

LEG_SOURCES = ("osrm", "estimate")


class TravelLeg(Base):
    """The free-flow time and distance from one place to another (migration 0052).

    One row per ordered pair; no multiplier is stored, because time of day is
    applied when a plan is made. ``source`` is ``osrm`` when the routing engine
    answered and ``estimate`` when the leg is haversine times a road factor; an
    estimate is replaced when the engine next answers, never the reverse.
    Derived and rebuildable: ``TRUNCATABLE`` in ``app/core/grants.py``.
    """

    __tablename__ = "travel_leg"
    __table_args__ = (
        CheckConstraint("from_place_id <> to_place_id", name="ck_travel_leg_distinct"),
        CheckConstraint("seconds >= 0", name="ck_travel_leg_seconds"),
        CheckConstraint("meters >= 0", name="ck_travel_leg_meters"),
        CheckConstraint("source IN ('osrm', 'estimate')", name="ck_travel_leg_source"),
        Index("ix_travel_leg_to_place_id", "to_place_id"),
    )

    from_place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("place.id", ondelete="CASCADE"), primary_key=True
    )
    to_place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("place.id", ondelete="CASCADE"), primary_key=True
    )
    seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    meters: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    profile: Mapped[str] = mapped_column(String(16), nullable=False, default="car")
    engine_version: Mapped[str | None] = mapped_column(String(80))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --- trip plans (17, 4B) -----------------------------------------------------------------

PLAN_STATUSES = ("planned", "underway", "done", "abandoned")
VARIANT_KINDS = ("fastest", "cheapest", "balanced")


class TripPlan(UUIDPrimaryKey, Base):
    """The planner's inputs and, once chosen, the choice (migration 0053).

    Derived until chosen: the three variants, their stops and stop items may be
    recomputed only while ``chosen_variant_id`` is null; ``choose`` freezes one
    as the prediction 4C compares against, and replanning makes a new plan (P1).
    """

    __tablename__ = "trip_plan"
    __table_args__ = (
        CheckConstraint("max_stops BETWEEN 1 AND 4", name="ck_trip_plan_max_stops"),
        CheckConstraint("value_of_time_per_hour >= 0", name="ck_trip_plan_value_of_time"),
        CheckConstraint("mileage_rate IS NULL OR mileage_rate >= 0", name="ck_trip_plan_mileage"),
        CheckConstraint("stop_overhead_min >= 0", name="ck_trip_plan_stop_overhead"),
        CheckConstraint(
            "status IN ('planned', 'underway', 'done', 'abandoned')", name="ck_trip_plan_status"
        ),
        CheckConstraint(
            "(chosen_variant_id IS NULL) = (chosen_at IS NULL)", name="ck_trip_plan_chosen_at"
        ),
    )

    list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shopping_list.id"), nullable=False, index=True
    )
    home_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("home_base.id"), nullable=False, index=True
    )
    origin_place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("place.id"), nullable=False
    )
    destination_place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("place.id"), nullable=False
    )
    planned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_of_time_per_hour: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    max_stops: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mileage_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    stop_overhead_min: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planned")
    chosen_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trip_plan_variant.id", name="fk_trip_plan_chosen_variant", use_alter=True),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    chosen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    variants: Mapped[list[TripPlanVariant]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="TripPlanVariant.plan_id",
        order_by="TripPlanVariant.kind",
    )


class TripPlanVariant(UUIDPrimaryKey, Base):
    """One of ``fastest``, ``cheapest`` or ``balanced``: its totals, whether any leg
    is an estimate, and its annotations as ``[{kind, item_id?, location_id?,
    amount?, minutes?, text}]``. ``driving_seconds`` is projected (the free-flow
    legs times their multipliers); ``stop_seconds`` is the overhead at each stop."""

    __tablename__ = "trip_plan_variant"
    __table_args__ = (
        UniqueConstraint("plan_id", "kind", name="uq_trip_plan_variant_kind"),
        CheckConstraint(
            "kind IN ('fastest', 'cheapest', 'balanced')", name="ck_trip_plan_variant_kind"
        ),
        CheckConstraint("driving_seconds >= 0", name="ck_trip_plan_variant_driving"),
        CheckConstraint("stop_seconds >= 0", name="ck_trip_plan_variant_stop"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_plan.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    item_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    driving_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    mileage_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    estimated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    annotations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    plan: Mapped[TripPlan] = relationship(back_populates="variants", foreign_keys=[plan_id])
    stops: Mapped[list[TripPlanStop]] = relationship(
        back_populates="variant",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TripPlanStop.seq",
    )


class TripPlanStop(UUIDPrimaryKey, Base):
    """One stop of a variant, in order. The leg is the free-flow leg that reaches
    it; ``multiplier`` and ``arrive_at`` are what the planner applied at that
    leg's projected departure (P2). ``done_at`` and ``purchase_id`` are 4C's."""

    __tablename__ = "trip_plan_stop"
    __table_args__ = (
        UniqueConstraint("variant_id", "seq", name="uq_trip_plan_stop_seq"),
        CheckConstraint("seq >= 1", name="ck_trip_plan_stop_seq"),
        CheckConstraint("leg_source IN ('osrm', 'estimate')", name="ck_trip_plan_stop_leg_source"),
        CheckConstraint("leg_seconds >= 0 AND leg_meters >= 0", name="ck_trip_plan_stop_leg"),
        CheckConstraint("multiplier > 0", name="ck_trip_plan_stop_multiplier"),
        CheckConstraint("overhead_min >= 0", name="ck_trip_plan_stop_overhead"),
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trip_plan_variant.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor_location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_location.id"), nullable=False, index=True
    )
    leg_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    leg_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    leg_source: Mapped[str] = mapped_column(String(16), nullable=False)
    multiplier: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    arrive_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overhead_min: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    hours_known: Mapped[bool] = mapped_column(Boolean, nullable=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase.id", ondelete="SET NULL")
    )

    variant: Mapped[TripPlanVariant] = relationship(back_populates="stops")
    location: Mapped[VendorLocation] = relationship(lazy="joined")
    items: Mapped[list[TripPlanStopItem]] = relationship(
        back_populates="stop",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TripPlanStopItem.id",
    )


class TripPlanStopItem(UUIDPrimaryKey, Base):
    """What to buy at a stop: the product, the packs and the price the plan rests
    on (``observation_id``), marked stale or forced. The ``actual_*`` columns and
    ``got_here`` are edited in the aisle (4C)."""

    __tablename__ = "trip_plan_stop_item"
    __table_args__ = (
        UniqueConstraint("stop_id", "list_item_id", name="uq_trip_plan_stop_item"),
        CheckConstraint("packs IS NULL OR packs >= 0", name="ck_trip_plan_stop_item_packs"),
        CheckConstraint(
            "(packs IS NULL) = (pack_price IS NULL)", name="ck_trip_plan_stop_item_pack_pair"
        ),
        CheckConstraint("line_cost >= 0", name="ck_trip_plan_stop_item_line_cost"),
    )

    stop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_plan_stop.id", ondelete="CASCADE"), nullable=False
    )
    list_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shopping_list_item.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), nullable=False
    )
    packs: Mapped[Decimal | None] = mapped_column(Numeric)
    pack_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    line_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_observation.id"), nullable=False
    )
    price_stale: Mapped[bool] = mapped_column(Boolean, nullable=False)
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actual_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    actual_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    actual_unit: Mapped[str | None] = mapped_column(String(16), ForeignKey("unit.code"))
    got_here: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    stop: Mapped[TripPlanStop] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(lazy="joined")
