"""Store brands and brand families (spec 16, 2R-1).

Five tables hold the kitchen-erp-brands dataset as ``kerp import brands`` writes
it: ``brand_family``, its ``brand_family_banner`` rows, ``brand``, every
spelling of a brand by name key in ``brand_alias``, and the wholesale labels a
retailer family sells in ``brand_family_carries``. ``vendor.brand_family_id``
says whose house brands a vendor's stores sell, and ``product.brand_id`` which
brand a product's printed brand names. Both are nullable and empty here; import
and ``kerp products brand-families`` fill them.

The runtime role's privileges need no change: the default privileges give it
full DML on new tables (app/core/grants.py BASE).

Downgrade drops the two columns, then the tables.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_TIERS = "'value', 'standard', 'premium', 'organic', 'natural', 'prepared', 'specialty'"


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "brand_family",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("wikidata", sa.String(20)),
        sa.Column("owner_name", sa.String(200)),
        sa.Column("owner_wikidata", sa.String(20)),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("field_source", postgresql.JSONB(), nullable=False, server_default="{}"),
        *_stamps(),
        sa.UniqueConstraint("key", name="uq_brand_family_key"),
        sa.CheckConstraint(
            "kind IN ('retailer', 'wholesaler', 'cooperative')", name="ck_brand_family_kind"
        ),
        sa.CheckConstraint(
            "wikidata IS NULL OR wikidata ~ '^Q[0-9]+$'", name="ck_brand_family_wikidata"
        ),
    )
    op.create_table(
        "brand_family_banner",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "family_id",
            _UUID,
            sa.ForeignKey("brand_family.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("wikidata", sa.String(20)),
        sa.Column("domains", postgresql.ARRAY(sa.String(253)), nullable=False, server_default="{}"),
        *_stamps(),
        sa.UniqueConstraint("key", name="uq_brand_family_banner_key"),
        sa.CheckConstraint(
            "wikidata IS NULL OR wikidata ~ '^Q[0-9]+$'", name="ck_brand_family_banner_wikidata"
        ),
    )
    op.create_index("ix_brand_family_banner_family_id", "brand_family_banner", ["family_id"])
    op.create_index("ix_brand_family_banner_wikidata", "brand_family_banner", ["wikidata"])
    op.create_table(
        "brand",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("family_id", _UUID, sa.ForeignKey("brand_family.id"), nullable=False),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column(
            "categories", postgresql.ARRAY(sa.String(32)), nullable=False, server_default="{}"
        ),
        sa.Column("since", sa.String(10)),
        sa.Column("until", sa.String(10)),
        sa.Column("replaced_by_id", _UUID, sa.ForeignKey("brand.id", ondelete="SET NULL")),
        sa.Column("field_source", postgresql.JSONB(), nullable=False, server_default="{}"),
        *_stamps(),
        sa.UniqueConstraint("key", name="uq_brand_key"),
        sa.CheckConstraint(f"tier IN ({_TIERS})", name="ck_brand_tier"),
        sa.CheckConstraint(
            "replaced_by_id IS NULL OR replaced_by_id <> id", name="ck_brand_replaced_self"
        ),
    )
    op.create_index("ix_brand_family_id", "brand", ["family_id"])
    op.create_table(
        "brand_alias",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("brand_id", _UUID, sa.ForeignKey("brand.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("alias_key", sa.String(200), nullable=False),
        sa.UniqueConstraint("alias_key", name="uq_brand_alias_key"),
    )
    op.create_index("ix_brand_alias_brand_id", "brand_alias", ["brand_id"])
    op.create_table(
        "brand_family_carries",
        sa.Column(
            "family_id",
            _UUID,
            sa.ForeignKey("brand_family.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "brand_id", _UUID, sa.ForeignKey("brand.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    op.add_column(
        "vendor",
        sa.Column("brand_family_id", _UUID, sa.ForeignKey("brand_family.id", ondelete="SET NULL")),
    )
    op.create_index("ix_vendor_brand_family_id", "vendor", ["brand_family_id"])
    op.add_column(
        "product",
        sa.Column("brand_id", _UUID, sa.ForeignKey("brand.id", ondelete="SET NULL")),
    )
    op.create_index("ix_product_brand_id", "product", ["brand_id"])


def downgrade() -> None:
    op.drop_index("ix_product_brand_id", table_name="product")
    op.drop_column("product", "brand_id")
    op.drop_index("ix_vendor_brand_family_id", table_name="vendor")
    op.drop_column("vendor", "brand_family_id")
    op.drop_table("brand_family_carries")
    op.drop_table("brand_alias")
    op.drop_table("brand")
    op.drop_table("brand_family_banner")
    op.drop_table("brand_family")
