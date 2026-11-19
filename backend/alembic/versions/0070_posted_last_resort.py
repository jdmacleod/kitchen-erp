"""A posted price as a recipe line's last resort (07, 3D and 27a; #523, ruling P1–P3).

``recipe_cost_line.status`` gains ``posted``: a line nothing else prices takes
the latest posted (listing) price, marked so, and left out of the counted
totals. ``recipe_cost_snapshot`` gains the posted twins of the counted totals,
``posted_share``, ``lines_posted`` and ``costing_version``. Existing snapshots
are stamped version 0, so ``kerp migrate`` recomputes the current ones once
(``derived_versions``) and leaves the history of earlier content as it was.

Downgrade turns posted lines back into unpriced ones (they are rebuildable
derivations) and adds ``lines_posted`` to ``lines_unpriced``, so every kept
snapshot's counts still add up without a recompute; then it drops the columns
and restores the old check.

Revision ID: 0070
Revises: 0069
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

_OLD = "status IN ('priced', 'unpriced', 'unconvertible', 'unmapped', 'negligible')"
_NEW = "status IN ('priced', 'posted', 'unpriced', 'unconvertible', 'unmapped', 'negligible')"
_MONEY = (
    "posted_consumed_cost",
    "posted_consumed_cost_high",
    "posted_basket_cost",
    "posted_basket_cost_high",
)


def upgrade() -> None:
    op.drop_constraint("ck_recipe_cost_line_status", "recipe_cost_line", type_="check")
    op.create_check_constraint("ck_recipe_cost_line_status", "recipe_cost_line", _NEW)
    for name in _MONEY:
        op.add_column("recipe_cost_snapshot", sa.Column(name, sa.Numeric(12, 4)))
    op.add_column(
        "recipe_cost_snapshot",
        sa.Column("posted_share", sa.Numeric(5, 4), nullable=False, server_default="0"),
    )
    for name in ("lines_posted", "costing_version"):
        op.add_column(
            "recipe_cost_snapshot",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.execute(
        """
        UPDATE recipe_cost_line
        SET status = 'unpriced', observation_id = NULL, product_id = NULL,
            norm_unit_price = NULL, consumed_cost = NULL, consumed_cost_high = NULL,
            basket_cost = NULL, basket_cost_high = NULL, packs = NULL, packs_high = NULL
        WHERE status = 'posted'
        """
    )
    op.execute("UPDATE recipe_cost_snapshot SET lines_unpriced = lines_unpriced + lines_posted")
    for name in (*_MONEY, "posted_share", "lines_posted", "costing_version"):
        op.drop_column("recipe_cost_snapshot", name)
    op.drop_constraint("ck_recipe_cost_line_status", "recipe_cost_line", type_="check")
    op.create_check_constraint("ck_recipe_cost_line_status", "recipe_cost_line", _OLD)
