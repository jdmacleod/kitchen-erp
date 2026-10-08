"""Ingredients, named measures, products, and the optional USDA reference table."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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

from app.models.base import Base, Timestamped, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.photos import ProductImage

BRIDGE_SOURCES = ("usda", "label", "measured", "llm", "manual")


class Ingredient(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "ingredient"
    __table_args__ = (
        CheckConstraint(
            "canonical_unit IN ('g', 'ml', 'each')", name="ck_ingredient_canonical_unit"
        ),
        CheckConstraint("yield_pct > 0 AND yield_pct <= 1", name="ck_ingredient_yield_pct"),
        CheckConstraint(
            "perishability IN ('shelf_stable', 'shelf_months', 'refrigerated', 'fresh', 'frozen')",
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
        CheckConstraint("keep_room_days >= 0", name="ck_ingredient_keep_room_days"),
        CheckConstraint("keep_fridge_days >= 0", name="ck_ingredient_keep_fridge_days"),
        CheckConstraint("keep_freezer_days >= 0", name="ck_ingredient_keep_freezer_days"),
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
    # Whole days it keeps unopened in each place (2Q); null when the charts give none.
    keep_room_days: Mapped[int | None] = mapped_column(Integer)
    keep_fridge_days: Mapped[int | None] = mapped_column(Integer)
    keep_freezer_days: Mapped[int | None] = mapped_column(Integer)
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
    # {field: {source, ref, checked_at, imported}}, as on vendor (1F): what an
    # ingredient file last wrote there (#241; services.interchange).
    field_source: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
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
ALIAS_SOURCES = ("standard", "generated", "rename", "merge", "manual", "import")


class IngredientAlias(UUIDPrimaryKey, Timestamped, Base):
    """Another spelling of an ingredient (1G). The canonical name is never a row here."""

    __tablename__ = "ingredient_alias"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('synonym', 'inflection', 'legacy')", name="ck_ingredient_alias_kind"
        ),
        CheckConstraint(
            "source IN ('standard', 'generated', 'rename', 'merge', 'manual', 'import')",
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
            "pack_count IS NULL OR (pack_count > 0 AND pack_qty IS NOT NULL)",
            name="ck_product_pack_count",
        ),
        CheckConstraint(
            "piece_name IS NULL OR pack_count IS NOT NULL", name="ck_product_piece_name"
        ),
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
        CheckConstraint(
            "kind IN ('branded', 'private_label', 'random_weight', 'loose', 'unbranded_vendor')",
            name="ck_product_kind",
        ),
        CheckConstraint(
            "merged_into IS NULL OR (NOT active AND merged_into <> id)",
            name="ck_product_merged_inactive",
        ),
        Index(
            "ix_product_merged_into",
            "merged_into",
            postgresql_where=text("merged_into IS NOT NULL"),
        ),
    )

    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id"), nullable=False, index=True
    )
    brand: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    pack_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    pack_unit: Mapped[str | None] = mapped_column(String(16), ForeignKey("unit.code"))
    # The pieces a mass or volume pack holds (19 oz, 5 links); the size stays the total.
    pack_count: Mapped[int | None] = mapped_column(Integer)
    piece_name: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # Validated per kind and category by app.catalog.attributes.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # {field: {source, ref, checked_at, imported}}, as on vendor (1F).
    field_source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quality_rating: Mapped[int | None] = mapped_column(SmallInteger)
    # Foreign key to vendor.id is added by the geography migration; the model
    # keeps it a plain column so the catalog does not import the geo mappers.
    exclusive_vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # The main photo, chosen by app.catalog.photos.select_primary (1I). Plain
    # column: the deferrable foreign key to product_image is in migration 0018.
    primary_image_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    density_override: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    density_override_source: Mapped[str | None] = mapped_column(String(16))
    density_override_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    # The product this one was merged into (#179). Its prices are reported under
    # that product by the price views; merges are kept one level deep.
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", name="fk_product_merged_into")
    )

    ingredient: Mapped[Ingredient] = relationship(back_populates="products")
    # The main photo, loaded with the product so a list can show it.
    photo: Mapped[ProductImage | None] = relationship(
        "ProductImage",
        primaryjoin="foreign(Product.primary_image_id) == ProductImage.id",
        viewonly=True,
        lazy="selectin",
    )
    identifiers: Mapped[list[ProductIdentifier]] = relationship(
        back_populates="product",
        lazy="selectin",
        order_by="(ProductIdentifier.created_at, ProductIdentifier.id)",
        cascade="all, delete-orphan",
    )

    @property
    def barcode_identifier(self) -> ProductIdentifier | None:
        """The identifier the API's ``barcode`` field shows: the earliest GTIN or other code."""
        return next((i for i in self.identifiers if i.scheme in ("gtin", "other")), None)

    @property
    def barcode(self) -> str | None:
        from app.catalog.identifiers import display

        ident = self.barcode_identifier
        return display(ident.scheme, ident.value) if ident else None


class ProductDistinctPair(Base):
    """ "Not the same" on a possible duplicate (2P, 03): never offered again. A decision,
    not a fact; the pair is stored in id order so either order finds it."""

    __tablename__ = "product_distinct_pair"
    __table_args__ = (
        CheckConstraint("product_a < product_b", name="ck_product_distinct_pair_order"),
        Index("ix_product_distinct_pair_b", "product_b"),
    )

    product_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), primary_key=True
    )
    product_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), primary_key=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id")
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductIdentifier(UUIDPrimaryKey, Base):
    """One code a product is known by (03, 1H). Vendor-scoped schemes carry a vendor."""

    __tablename__ = "product_identifier"
    __table_args__ = (
        CheckConstraint(
            "scheme IN ('gtin', 'plu', 'vendor_sku', 'rw_item', 'other')",
            name="ck_product_identifier_scheme",
        ),
        CheckConstraint(
            "(scheme IN ('plu', 'vendor_sku', 'rw_item')) = (vendor_id IS NOT NULL)",
            name="ck_product_identifier_vendor",
        ),
        # The unique index is NULLS NOT DISTINCT, created in migration 0017.
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), nullable=False
    )
    scheme: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    # Plain column like product.exclusive_vendor_id: the catalog doesn't import the geo mappers.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    legacy_value: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="identifiers")


class VendorListing(UUIDPrimaryKey, Timestamped, Base):
    """A vendor's page for a product (03, 1H), keyed by vendor and canonical address."""

    __tablename__ = "vendor_listing"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'gone', 'ignored')", name="ck_listing_status"),
        UniqueConstraint("vendor_id", "canonical_url", name="uq_listing_vendor_url"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="SET NULL")
    )
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    vendor_sku: Mapped[str | None] = mapped_column(String(64))
    store_ref: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    last_captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


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


class FdcFood(Base):
    """A USDA FoodData Central food (1G): Foundation, SR Legacy or FNDDS survey."""

    __tablename__ = "fdc_food"

    fdc_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    data_type: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    fndds_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FdcBranded(Base):
    """A USDA branded food by GTIN-14 (2L, opt-in): what an unknown barcode is looked up in."""

    __tablename__ = "fdc_branded"
    __table_args__ = (CheckConstraint("gtin ~ '^[0-9]{14}$'", name="ck_fdc_branded_gtin"),)

    gtin: Mapped[str] = mapped_column(String(14), primary_key=True)
    fdc_id: Mapped[int] = mapped_column(Integer, nullable=False)
    brand: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    package_size: Mapped[str | None] = mapped_column(Text)
    release_date: Mapped[date | None] = mapped_column(Date)


class FdcRelease(UUIDPrimaryKey, Base):
    """One ``kerp import usda`` run; the latest row describes the loaded data."""

    __tablename__ = "fdc_release"

    release_date: Mapped[date | None] = mapped_column(Date)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    foods: Mapped[int] = mapped_column(Integer, nullable=False)
    portions: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
