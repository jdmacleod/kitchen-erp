"""Shopping lists and the current kitchen (17, Phase 4A).

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKey
from app.models.catalog import Ingredient, Product
from app.models.geo import HomeBase
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
