"""Recipes indexed from the Cooklang repository (07, Phase 3).

``recipe`` is one row per ``.cook`` file, identified by its path; the indexer
moves rows across renames rather than creating new ones. ``recipe_ingredient``
rows are derived from the file and rebuilt when its hash changes. Pins and
ignored names are facts a person stated.
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
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamped, UUIDPrimaryKey

RECIPE_STATUSES = ("ok", "parse_error", "missing")
QTY_KINDS = ("number", "range", "text", "none")
RESOLUTIONS = ("alias", "manual", "unmatched", "negligible", "ignored")
YIELD_MODES = ("auto", "as_purchased", "edible")


class Recipe(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "recipe"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'parse_error', 'missing')", name="ck_recipe_status"),
        UniqueConstraint("path", name="uq_recipe_path"),
    )

    path: Mapped[str] = mapped_column(Text, nullable=False)  # relative to the repository root
    title: Mapped[str] = mapped_column(Text, nullable=False)
    servings: Mapped[Decimal | None] = mapped_column(Numeric)
    servings_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(40), nullable=False)  # git blob hash on disk
    head_commit: Mapped[str | None] = mapped_column(String(40))  # HEAD when last indexed
    dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    parse_error_message: Mapped[str | None] = mapped_column(Text)
    front_matter: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # An uncommitted move with an edit (3A, step 3): the new file the indexer
    # believes this missing recipe became, until a person confirms or refuses.
    relink_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe.id", ondelete="SET NULL")
    )
    relink_reason: Mapped[str | None] = mapped_column(Text)

    ingredients: Mapped[list[RecipeIngredient]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="RecipeIngredient.seq"
    )
    pins: Mapped[list[RecipePin]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )


class RecipeIngredient(UUIDPrimaryKey, Base):
    """One ingredient line as written; rebuilt when the recipe's hash changes."""

    __tablename__ = "recipe_ingredient"
    __table_args__ = (
        CheckConstraint(
            "qty_kind IN ('number', 'range', 'text', 'none')", name="ck_recipe_ingredient_qty_kind"
        ),
        CheckConstraint(
            "resolution IN ('alias', 'manual', 'unmatched', 'negligible', 'ignored')",
            name="ck_recipe_ingredient_resolution",
        ),
        CheckConstraint(
            "yield_mode IN ('auto', 'as_purchased', 'edible')",
            name="ck_recipe_ingredient_yield_mode",
        ),
        UniqueConstraint("recipe_id", "seq", name="uq_recipe_ingredient_seq"),
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(Text)
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)  # exactly as written
    name_norm: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    qty_kind: Mapped[str] = mapped_column(String(8), nullable=False, default="none")
    qty: Mapped[Decimal | None] = mapped_column(Numeric)
    qty_high: Mapped[Decimal | None] = mapped_column(Numeric)
    qty_text: Mapped[str | None] = mapped_column(Text)
    unit_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(16), ForeignKey("unit.code"))
    note: Mapped[str | None] = mapped_column(Text)  # `@name{qty}(note)`, as written (0043)
    ingredient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id", ondelete="SET NULL"), index=True
    )
    resolution: Mapped[str] = mapped_column(String(16), nullable=False, default="unmatched")
    negligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    yield_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")


class RecipeNameIgnore(UUIDPrimaryKey, Base):
    """A recipe ingredient name a person marked as not an ingredient (3C)."""

    __tablename__ = "recipe_name_ignore"
    __table_args__ = (UniqueConstraint("name_norm", name="uq_recipe_name_ignore_name_norm"),)

    name_norm: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )


class RecipePin(UUIDPrimaryKey, Timestamped, Base):
    """A single-source line pinned to a product; survives re-indexing while the name stays."""

    __tablename__ = "recipe_pin"
    __table_args__ = (UniqueConstraint("recipe_id", "name_norm", name="uq_recipe_pin_name"),)

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name_norm: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False, index=True
    )

    recipe: Mapped[Recipe] = relationship(back_populates="pins")


# --- 3D: cost snapshots, derived and rebuildable like price_norm ---------------------

COST_BASES = ("latest", "average", "cheapest")
COST_LINE_STATUSES = ("priced", "unpriced", "unconvertible", "unmapped", "negligible")


class RecipeCostSnapshot(UUIDPrimaryKey, Base):
    """One costing of a recipe at one content hash under one basis (3D).

    The key ``(recipe_id, content_hash, basis, window_days, min_quality)`` is a
    unique index with ``NULLS NOT DISTINCT`` (migration 0045), so a recompute
    replaces the snapshot in place and history holds one row per content version.
    """

    __tablename__ = "recipe_cost_snapshot"
    __table_args__ = (
        CheckConstraint(
            "basis IN ('latest', 'average', 'cheapest')", name="ck_recipe_cost_snapshot_basis"
        ),
        CheckConstraint(
            "window_days IS NULL OR window_days > 0", name="ck_recipe_cost_snapshot_window"
        ),
        # The unique key is NULLS NOT DISTINCT, created in migration 0045.
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    head_commit: Mapped[str | None] = mapped_column(String(40))
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False)
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    window_days: Mapped[int | None] = mapped_column(Integer)
    min_quality: Mapped[int | None] = mapped_column(SmallInteger)
    consumed_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    consumed_cost_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    basket_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    basket_cost_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    per_serving: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    lines_total: Mapped[int] = mapped_column(Integer, nullable=False)
    lines_priced: Mapped[int] = mapped_column(Integer, nullable=False)
    lines_unpriced: Mapped[int] = mapped_column(Integer, nullable=False)
    lines_unconvertible: Mapped[int] = mapped_column(Integer, nullable=False)
    lines_unmapped: Mapped[int] = mapped_column(Integer, nullable=False)
    lines_negligible: Mapped[int] = mapped_column(Integer, nullable=False)
    # Fraction of consumed cost resting on unconfirmed bridges.
    unconfirmed_share: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lines: Mapped[list[RecipeCostLine]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class RecipeCostLine(UUIDPrimaryKey, Base):
    """One recipe ingredient as costed in a snapshot.

    Quantities and costs are the as-purchased figures; ``*_high`` is set only
    for a range quantity. ``yield_applied`` is the yield the quantity was
    grossed up by, null when the line was taken as purchased.
    """

    __tablename__ = "recipe_cost_line"
    __table_args__ = (
        CheckConstraint(
            "status IN ('priced', 'unpriced', 'unconvertible', 'unmapped', 'negligible')",
            name="ck_recipe_cost_line_status",
        ),
        UniqueConstraint("snapshot_id", "recipe_ingredient_id", name="uq_recipe_cost_line"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipe_cost_snapshot.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipe_ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipe_ingredient.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    canonical_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    canonical_qty_high: Mapped[Decimal | None] = mapped_column(Numeric)
    canonical_unit: Mapped[str | None] = mapped_column(String(16))
    yield_applied: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="SET NULL"), index=True
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_observation.id", ondelete="SET NULL"), index=True
    )
    norm_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    consumed_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    consumed_cost_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    basket_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    basket_cost_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    packs: Mapped[Decimal | None] = mapped_column(Numeric)
    packs_high: Mapped[Decimal | None] = mapped_column(Numeric)
    bridge_kind: Mapped[str | None] = mapped_column(String(20))
    bridge_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    failure_code: Mapped[str | None] = mapped_column(String(20))

    snapshot: Mapped[RecipeCostSnapshot] = relationship(back_populates="lines")
