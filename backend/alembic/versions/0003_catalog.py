"""Catalog: ingredient, ingredient_measure, product, ref_usda_portion.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SOURCES = "('usda', 'label', 'measured', 'llm', 'manual')"


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "ingredient",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column(
            "canonical_unit",
            sa.String(16),
            sa.ForeignKey("unit.code"),
            nullable=False,
            server_default="g",
        ),
        sa.Column("density_g_per_ml", sa.Numeric(10, 5)),
        sa.Column("density_source", sa.String(16)),
        sa.Column("density_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("yield_pct", sa.Numeric(5, 4), nullable=False, server_default="1"),
        sa.Column("perishability", sa.String(16), nullable=False, server_default="shelf_stable"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        *_ts(),
        sa.CheckConstraint(
            "canonical_unit IN ('g', 'ml', 'each')", name="ck_ingredient_canonical_unit"
        ),
        sa.CheckConstraint("yield_pct > 0 AND yield_pct <= 1", name="ck_ingredient_yield_pct"),
        sa.CheckConstraint(
            "perishability IN ('shelf_stable', 'refrigerated', 'fresh')",
            name="ck_ingredient_perishability",
        ),
        sa.CheckConstraint(
            f"density_source IS NULL OR density_source IN {SOURCES}",
            name="ck_ingredient_density_source",
        ),
        sa.CheckConstraint(
            "(density_g_per_ml IS NULL) = (density_source IS NULL)",
            name="ck_ingredient_density_pair",
        ),
    )
    op.create_index("uq_ingredient_name_lower", "ingredient", [sa.text("lower(name)")], unique=True)
    op.execute(
        "CREATE INDEX ix_ingredient_name_trgm ON ingredient USING gin (lower(name) gin_trgm_ops)"
    )

    op.create_table(
        "ingredient_measure",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingredient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("canonical_qty", sa.Numeric(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_ts(),
        sa.CheckConstraint(f"source IN {SOURCES}", name="ck_measure_source"),
        sa.CheckConstraint("canonical_qty > 0", name="ck_measure_qty_positive"),
    )
    op.create_index(
        "uq_measure_ingredient_label_lower",
        "ingredient_measure",
        ["ingredient_id", sa.text("lower(label)")],
        unique=True,
    )

    op.create_table(
        "product",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingredient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id"),
            nullable=False,
        ),
        sa.Column("brand", sa.String(200)),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("pack_qty", sa.Numeric()),
        sa.Column("pack_unit", sa.String(16), sa.ForeignKey("unit.code")),
        sa.Column("barcode", sa.String(32)),
        sa.Column("quality_rating", sa.SmallInteger()),
        sa.Column("exclusive_vendor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("density_override", sa.Numeric(10, 5)),
        sa.Column("density_override_source", sa.String(16)),
        sa.Column(
            "density_override_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        *_ts(),
        sa.CheckConstraint("(pack_qty IS NULL) = (pack_unit IS NULL)", name="ck_product_pack_pair"),
        sa.CheckConstraint("pack_qty IS NULL OR pack_qty > 0", name="ck_product_pack_positive"),
        sa.CheckConstraint(
            "quality_rating IS NULL OR (quality_rating BETWEEN 1 AND 5)", name="ck_product_quality"
        ),
        sa.CheckConstraint(
            f"density_override_source IS NULL OR density_override_source IN {SOURCES}",
            name="ck_product_density_source",
        ),
        sa.CheckConstraint(
            "(density_override IS NULL) = (density_override_source IS NULL)",
            name="ck_product_density_pair",
        ),
    )
    op.create_index("ix_product_ingredient_id", "product", ["ingredient_id"])
    op.create_index(
        "uq_product_barcode",
        "product",
        ["barcode"],
        unique=True,
        postgresql_where=sa.text("barcode IS NOT NULL"),
    )
    op.execute("CREATE INDEX ix_product_name_trgm ON product USING gin (lower(name) gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_product_brand_trgm ON product USING gin (lower(brand) gin_trgm_ops)"
    )

    op.create_table(
        "ref_usda_portion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("fdc_id", sa.Integer(), nullable=False),
        sa.Column("food_description", sa.Text(), nullable=False),
        sa.Column("portion_label", sa.Text(), nullable=False),
        sa.Column("portion_amount", sa.Numeric(), nullable=False),
        sa.Column("portion_unit", sa.String(100), nullable=False),
        sa.Column("gram_weight", sa.Numeric(), nullable=False),
        sa.Column("data_type", sa.String(40), nullable=False),
    )
    op.create_index("ix_ref_usda_portion_fdc_id", "ref_usda_portion", ["fdc_id"])
    op.execute(
        "CREATE INDEX ix_ref_usda_desc_trgm ON ref_usda_portion "
        "USING gin (lower(food_description) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_table("ref_usda_portion")
    op.drop_table("product")
    op.drop_table("ingredient_measure")
    op.drop_table("ingredient")
