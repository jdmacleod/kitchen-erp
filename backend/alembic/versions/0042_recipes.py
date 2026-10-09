"""Recipes (07, Phase 3): the tables the repository indexer and resolution write.

``recipe`` is one row per ``.cook`` file in the mounted repository, identified by
its path and carried across renames by the indexer (3A). ``recipe_ingredient`` is
derived from the file and rebuilt when its hash changes (3B, 3C). ``recipe_pin``
and ``recipe_name_ignore`` are facts a person stated (3C). The costing tables
arrive with 3D in their own migration.

``ingredient_alias.source`` gains ``recipe`` for spellings a recipe decision
writes. This is also the migration ``/health`` keys the ``cook`` feature on.

Downgrade drops the four tables, turns recipe-sourced spellings into ``manual``
ones so none is lost, and restores the previous constraint text.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"
TABLES = ("recipe", "recipe_ingredient", "recipe_name_ignore", "recipe_pin")

OLD_SOURCES = "'standard', 'generated', 'rename', 'merge', 'manual', 'import'"
NEW_SOURCES = OLD_SOURCES + ", 'recipe'"


def _timestamps() -> list[sa.Column]:
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
        "recipe",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("servings", sa.Numeric(), nullable=True),
        sa.Column("servings_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(40), nullable=False),
        sa.Column("head_commit", sa.String(40), nullable=True),
        sa.Column("dirty", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("parse_error_message", sa.Text(), nullable=True),
        sa.Column("front_matter", JSONB(), nullable=True),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "relink_candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("relink_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('ok', 'parse_error', 'missing')", name="ck_recipe_status"),
        sa.UniqueConstraint("path", name="uq_recipe_path"),
    )
    op.create_table(
        "recipe_ingredient",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "recipe_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.Column("qty_kind", sa.String(8), nullable=False),
        sa.Column("qty", sa.Numeric(), nullable=True),
        sa.Column("qty_high", sa.Numeric(), nullable=True),
        sa.Column("qty_text", sa.Text(), nullable=True),
        sa.Column("unit_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(16), sa.ForeignKey("unit.code"), nullable=True),
        sa.Column(
            "ingredient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution", sa.String(16), nullable=False),
        sa.Column("negligible", sa.Boolean(), nullable=False),
        sa.Column("yield_mode", sa.String(16), nullable=False, server_default="auto"),
        sa.CheckConstraint(
            "qty_kind IN ('number', 'range', 'text', 'none')", name="ck_recipe_ingredient_qty_kind"
        ),
        sa.CheckConstraint(
            "resolution IN ('alias', 'manual', 'unmatched', 'negligible')",
            name="ck_recipe_ingredient_resolution",
        ),
        sa.CheckConstraint(
            "yield_mode IN ('auto', 'as_purchased', 'edible')",
            name="ck_recipe_ingredient_yield_mode",
        ),
        sa.UniqueConstraint("recipe_id", "seq", name="uq_recipe_ingredient_seq"),
    )
    op.create_index("ix_recipe_ingredient_recipe_id", "recipe_ingredient", ["recipe_id"])
    op.create_index("ix_recipe_ingredient_name_norm", "recipe_ingredient", ["name_norm"])
    op.create_index("ix_recipe_ingredient_ingredient_id", "recipe_ingredient", ["ingredient_id"])
    op.create_table(
        "recipe_name_ignore",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("name_norm", name="uq_recipe_name_ignore_name_norm"),
    )
    op.create_table(
        "recipe_pin",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "recipe_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_timestamps(),
        sa.UniqueConstraint("recipe_id", "name_norm", name="uq_recipe_pin_name"),
    )
    op.create_index("ix_recipe_pin_recipe_id", "recipe_pin", ["recipe_id"])
    op.create_index("ix_recipe_pin_product_id", "recipe_pin", ["product_id"])

    # Ordinary tables: the default privileges from 0001 already cover them. Said
    # again here so the grant is visible beside the tables it applies to.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TABLES)} TO {APP_ROLE}")

    op.drop_constraint("ck_ingredient_alias_source", "ingredient_alias", type_="check")
    op.create_check_constraint(
        "ck_ingredient_alias_source", "ingredient_alias", f"source IN ({NEW_SOURCES})"
    )


def downgrade() -> None:
    op.execute("UPDATE ingredient_alias SET source = 'manual' WHERE source = 'recipe'")
    op.drop_constraint("ck_ingredient_alias_source", "ingredient_alias", type_="check")
    op.create_check_constraint(
        "ck_ingredient_alias_source", "ingredient_alias", f"source IN ({OLD_SOURCES})"
    )
    for table in reversed(TABLES):
        op.drop_table(table)
