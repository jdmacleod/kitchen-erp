"""Ingredients, named measures, products, and the optional USDA reference table."""

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
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamped, UUIDPrimaryKey

BRIDGE_SOURCES = ("usda", "label", "measured", "llm", "manual")


class Ingredient(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "ingredient"
    __table_args__ = (
        CheckConstraint(
            "canonical_unit IN ('g', 'ml', 'each')", name="ck_ingredient_canonical_unit"
        ),
        CheckConstraint("yield_pct > 0 AND yield_pct <= 1", name="ck_ingredient_yield_pct"),
        CheckConstraint(
            "perishability IN ('shelf_stable', 'refrigerated', 'fresh')",
            name="ck_ingredient_perishability",
        ),
        CheckConstraint(
            "density_source IS NULL OR density_source IN "
            "('usda', 'label', 'measured', 'llm', 'manual')",
            name="ck_ingredient_density_source",
        ),
        CheckConstraint(
            "(density_g_per_ml IS NULL) = (density_source IS NULL)",
            name="ck_ingredient_density_pair",
        ),
        Index("uq_ingredient_name_lower", func.lower(name := "name"), unique=True),
        CheckConstraint(
            "reconcile_state IN ('unreviewed', 'linked', 'skipped', 'not_applicable')",
            name="ck_ingredient_reconcile_state",
        ),
        UniqueConstraint("slug", name="uq_ingredient_slug"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    canonical_unit: Mapped[str] = mapped_column(
        String(16), ForeignKey("unit.code"), nullable=False, default="g"
    )
    density_g_per_ml: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    density_source: Mapped[str | None] = mapped_column(String(16))
    density_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    yield_pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("1"))
    perishability: Mapped[str] = mapped_column(String(16), nullable=False, default="shelf_stable")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    # 1G: the standard key when created from, or linked to, the standard list;
    # otherwise "local.<name>", assigned on flush (models/keys.py).
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    reconcile_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="not_applicable", index=True
    )
    usda_reviewed_fdc_id: Mapped[int | None] = mapped_column(Integer)
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id")
    )

    measures: Mapped[list[IngredientMeasure]] = relationship(
        back_populates="ingredient",
        cascade="all, delete-orphan",
        order_by="IngredientMeasure.label",
    )
    products: Mapped[list[Product]] = relationship(back_populates="ingredient")


class IngredientMeasure(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "ingredient_measure"
    __table_args__ = (
        CheckConstraint(
            "source IN ('usda', 'label', 'measured', 'llm', 'manual')", name="ck_measure_source"
        ),
        CheckConstraint("canonical_qty > 0", name="ck_measure_qty_positive"),
        Index(
            "uq_measure_ingredient_label_lower", "ingredient_id", func.lower("label"), unique=True
        ),
    )

    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    canonical_qty: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    ingredient: Mapped[Ingredient] = relationship(back_populates="measures")


ALIAS_KINDS = ("synonym", "inflection", "legacy")
ALIAS_SOURCES = ("standard", "generated", "rename", "merge", "manual")


class IngredientAlias(UUIDPrimaryKey, Timestamped, Base):
    """Another spelling of an ingredient (1G). The canonical name is never a row here."""

    __tablename__ = "ingredient_alias"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('synonym', 'inflection', 'legacy')", name="ck_ingredient_alias_kind"
        ),
        CheckConstraint(
            "source IN ('standard', 'generated', 'rename', 'merge', 'manual')",
            name="ck_ingredient_alias_source",
        ),
        UniqueConstraint("name_norm", name="uq_ingredient_alias_name_norm"),
        Index(
            "ix_ingredient_alias_name_norm_trgm",
            "name_norm",
            postgresql_using="gin",
            postgresql_ops={"name_norm": "gin_trgm_ops"},
        ),
    )

    name_norm: Mapped[str] = mapped_column(Text, nullable=False)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingredient.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngredientRef(UUIDPrimaryKey, Timestamped, Base):
    """An external identifier for an ingredient; only USDA FoodData Central (``fdc``) in 1G."""

    __tablename__ = "ingredient_ref"
    __table_args__ = (
        CheckConstraint("system IN ('fdc')", name="ck_ingredient_ref_system"),
        CheckConstraint(
            "system <> 'fdc' OR external_id ~ '^[0-9]+$'", name="ck_ingredient_ref_fdc_digits"
        ),
        UniqueConstraint(
            "system", "external_id", "ingredient_id", name="uq_ingredient_ref_system_external"
        ),
        Index(
            "uq_ingredient_ref_one_preferred",
            "ingredient_id",
            "system",
            unique=True,
            postgresql_where="is_preferred",
        ),
    )

    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingredient.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    system: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Product(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "product"
    __table_args__ = (
        CheckConstraint("(pack_qty IS NULL) = (pack_unit IS NULL)", name="ck_product_pack_pair"),
        CheckConstraint("pack_qty IS NULL OR pack_qty > 0", name="ck_product_pack_positive"),
        CheckConstraint(
            "quality_rating IS NULL OR (quality_rating BETWEEN 1 AND 5)", name="ck_product_quality"
        ),
        CheckConstraint(
            "density_override_source IS NULL OR density_override_source IN "
            "('usda', 'label', 'measured', 'llm', 'manual')",
            name="ck_product_density_source",
        ),
        CheckConstraint(
            "(density_override IS NULL) = (density_override_source IS NULL)",
            name="ck_product_density_pair",
        ),
        Index("uq_product_barcode", "barcode", unique=True, postgresql_where="barcode IS NOT NULL"),
    )

    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id"), nullable=False, index=True
    )
    brand: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    pack_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    pack_unit: Mapped[str | None] = mapped_column(String(16), ForeignKey("unit.code"))
    barcode: Mapped[str | None] = mapped_column(String(32))
    quality_rating: Mapped[int | None] = mapped_column(SmallInteger)
    # Foreign key to vendor.id is added by the geography migration; the model
    # keeps it a plain column so the catalog does not import the geo mappers.
    exclusive_vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    density_override: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    density_override_source: Mapped[str | None] = mapped_column(String(16))
    density_override_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    ingredient: Mapped[Ingredient] = relationship(back_populates="products")


class RefUsdaPortion(UUIDPrimaryKey, Base):
    """Optional reference data from a local FoodData Central download."""

    __tablename__ = "ref_usda_portion"

    fdc_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    food_description: Mapped[str] = mapped_column(Text, nullable=False)
    portion_label: Mapped[str] = mapped_column(Text, nullable=False)
    portion_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    portion_unit: Mapped[str] = mapped_column(Text, nullable=False)
    gram_weight: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    data_type: Mapped[str] = mapped_column(String(40), nullable=False)
