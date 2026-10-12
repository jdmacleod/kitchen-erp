"""Shopping lists and the current kitchen (17, Phase 4A).

``shopping_list`` is one row per list; a kitchen has exactly one open list,
which a partial unique index enforces. ``shopping_list_item`` is a fact a person
stated: an ingredient, optionally with a minimum quality, or an exact product,
never both. ``shopping_list_item_qty`` holds an item's (amount, unit) pairs as
``numeric`` with a unit foreign key, never JSONB, because aggregation keeps the
pairs it cannot convert. ``user_setting`` is one typed row per user: the current
kitchen the client sends on kitchen-scoped calls.

All four are ordinary tables under the runtime role's DML grants. This is the
migration ``/health`` keys the ``plan`` feature on.

Downgrade drops the four tables.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"
TABLES = ("shopping_list", "shopping_list_item", "shopping_list_item_qty", "user_setting")


def _now() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _user(name: str, *, nullable: bool, ondelete: str | None) -> sa.Column:
    return sa.Column(
        name,
        UUID(as_uuid=True),
        sa.ForeignKey("app_user.id", ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "shopping_list",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "home_base_id", UUID(as_uuid=True), sa.ForeignKey("home_base.id"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        _now(),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'archived')", name="ck_shopping_list_status"),
        sa.CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)",
            name="ck_shopping_list_archived_at",
        ),
    )
    op.create_index("ix_shopping_list_home_base_id", "shopping_list", ["home_base_id"])
    # "The {kitchen} list": one open list per kitchen, settled by the database.
    op.create_index(
        "uq_shopping_list_open",
        "shopping_list",
        ["home_base_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "shopping_list_item",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "list_id",
            UUID(as_uuid=True),
            sa.ForeignKey("shopping_list.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "ingredient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("min_quality", sa.SmallInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("deferrable", sa.Boolean(), nullable=False, server_default=sa.false()),
        _user("added_by", nullable=False, ondelete=None),
        sa.Column("added_from", sa.String(16), nullable=False),
        sa.Column(
            "recipe_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "carried_from_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("shopping_list_item.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _user("claimed_by", nullable=True, ondelete="SET NULL"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        _user("checked_by", nullable=True, ondelete="SET NULL"),
        _now(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('ingredient', 'product')", name="ck_shopping_list_item_kind"),
        # Exactly one target, and the one the kind names.
        sa.CheckConstraint(
            "(kind = 'ingredient' AND ingredient_id IS NOT NULL AND product_id IS NULL) OR "
            "(kind = 'product' AND product_id IS NOT NULL AND ingredient_id IS NULL)",
            name="ck_shopping_list_item_target",
        ),
        sa.CheckConstraint(
            "min_quality IS NULL OR (kind = 'ingredient' AND min_quality BETWEEN 1 AND 5)",
            name="ck_shopping_list_item_min_quality",
        ),
        sa.CheckConstraint(
            "added_from IN ('manual', 'ingredient', 'recipe', 'stock')",
            name="ck_shopping_list_item_added_from",
        ),
        sa.CheckConstraint(
            "(claimed_by IS NULL) = (claimed_at IS NULL)", name="ck_shopping_list_item_claim"
        ),
    )
    op.create_index("ix_shopping_list_item_list_id", "shopping_list_item", ["list_id"])
    op.create_index("ix_shopping_list_item_ingredient_id", "shopping_list_item", ["ingredient_id"])
    op.create_index("ix_shopping_list_item_product_id", "shopping_list_item", ["product_id"])

    op.create_table(
        "shopping_list_item_qty",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("shopping_list_item.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(16), sa.ForeignKey("unit.code"), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_shopping_list_item_qty_amount"),
        sa.UniqueConstraint("item_id", "seq", name="uq_shopping_list_item_qty_seq"),
    )

    op.create_table(
        "user_setting",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "current_home_base_id",
            UUID(as_uuid=True),
            sa.ForeignKey("home_base.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # Ordinary tables: the default privileges from 0001 already cover them. Said
    # again here so the grant is visible beside the tables it applies to.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TABLES)} TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
