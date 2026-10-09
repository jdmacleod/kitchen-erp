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
