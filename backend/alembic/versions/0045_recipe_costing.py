"""Recipe cost snapshots and their lines (07, Phase 3, 3D).

``recipe_cost_snapshot`` is one costing of a recipe at one content hash under
one basis (latest, average over a window, cheapest) and optional minimum
quality; ``recipe_cost_line`` is one row per recipe ingredient at snapshot
time. Both are derived and rebuildable, as ``price_norm`` is: ``kerp
recompute-costs`` truncates them, so the runtime role gets TRUNCATE and both
join ``TRUNCATABLE`` in ``app/core/grants.py``.

The snapshot key is ``UNIQUE (recipe_id, content_hash, basis, window_days,
min_quality)`` with ``NULLS NOT DISTINCT`` (PostgreSQL 15+, as 0017 did for
product identifiers), so two snapshots with no window and no minimum quality
collide instead of piling up: the key is really unique.

Downgrade drops the two tables; the grants go with them.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"
TABLES = ("recipe_cost_snapshot", "recipe_cost_line")


def upgrade() -> None:
    op.create_table(
        "recipe_cost_snapshot",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "recipe_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(40), nullable=False),
        sa.Column("head_commit", sa.String(40), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("min_quality", sa.SmallInteger(), nullable=True),
        sa.Column("consumed_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("consumed_cost_high", sa.Numeric(12, 4), nullable=True),
        sa.Column("basket_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("basket_cost_high", sa.Numeric(12, 4), nullable=True),
        sa.Column("per_serving", sa.Numeric(12, 4), nullable=True),
        sa.Column("lines_total", sa.Integer(), nullable=False),
        sa.Column("lines_priced", sa.Integer(), nullable=False),
        sa.Column("lines_unpriced", sa.Integer(), nullable=False),
        sa.Column("lines_unconvertible", sa.Integer(), nullable=False),
        sa.Column("lines_unmapped", sa.Integer(), nullable=False),
        sa.Column("lines_negligible", sa.Integer(), nullable=False),
        sa.Column("unconfirmed_share", sa.Numeric(5, 4), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "basis IN ('latest', 'average', 'cheapest')", name="ck_recipe_cost_snapshot_basis"
        ),
        sa.CheckConstraint(
            "window_days IS NULL OR window_days > 0", name="ck_recipe_cost_snapshot_window"
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_recipe_cost_snapshot_key ON recipe_cost_snapshot "
        "(recipe_id, content_hash, basis, window_days, min_quality) NULLS NOT DISTINCT"
    )
    op.create_index(
        "ix_recipe_cost_snapshot_recipe_id", "recipe_cost_snapshot", ["recipe_id", "computed_at"]
    )
    op.create_table(
        "recipe_cost_line",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe_cost_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipe_ingredient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe_ingredient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("canonical_qty", sa.Numeric(), nullable=True),
        sa.Column("canonical_qty_high", sa.Numeric(), nullable=True),
        sa.Column("canonical_unit", sa.String(16), nullable=True),
        sa.Column("yield_applied", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "observation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("price_observation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("norm_unit_price", sa.Numeric(14, 6), nullable=True),
        sa.Column("consumed_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("consumed_cost_high", sa.Numeric(12, 4), nullable=True),
        sa.Column("basket_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("basket_cost_high", sa.Numeric(12, 4), nullable=True),
        sa.Column("packs", sa.Numeric(), nullable=True),
        sa.Column("packs_high", sa.Numeric(), nullable=True),
        sa.Column("bridge_kind", sa.String(20), nullable=True),
        sa.Column("bridge_confirmed", sa.Boolean(), nullable=True),
        sa.Column("failure_code", sa.String(20), nullable=True),
        sa.CheckConstraint(
            "status IN ('priced', 'unpriced', 'unconvertible', 'unmapped', 'negligible')",
            name="ck_recipe_cost_line_status",
        ),
        sa.UniqueConstraint("snapshot_id", "recipe_ingredient_id", name="uq_recipe_cost_line"),
    )
    op.create_index("ix_recipe_cost_line_product_id", "recipe_cost_line", ["product_id"])
    op.create_index("ix_recipe_cost_line_observation_id", "recipe_cost_line", ["observation_id"])

    # Derived, rebuildable tables: ordinary DML plus TRUNCATE for `kerp recompute-costs`.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TABLES)} TO {APP_ROLE}")
    for table in TABLES:
        op.execute(f"GRANT TRUNCATE ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
