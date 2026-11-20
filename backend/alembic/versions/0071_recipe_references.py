"""Recipe references and what a recipe makes (07, 3B and 3D; #526, rulings S1–S4).

- ``recipe`` gains ``makes_qty``, ``makes_unit`` and ``makes_text``, from front
  matter ``yield: 500 ml``.
- ``recipe_ingredient`` gains ``ref_path`` and ``ref_recipe_id`` for a recipe
  reference (``@./basics/simple-syrup{1%fl oz}``), and its resolution check
  gains ``recipe``.
- ``ingredient`` gains ``made_by_recipe_id``: a line on it that no product price
  covers costs from that recipe.
- ``recipe_cost_line`` gains ``source_recipe_id`` and ``source_partial``;
  ``recipe_cost_snapshot`` gains ``lines_partial``.

Existing files are not re-read by the upgrade: run ``kerp recipes rescan
--reparse`` after it, so unchanged files fill ``makes_*`` and their recipe
references, then ``kerp recompute-costs``. ``COSTING_VERSION`` 2 makes ``kerp
migrate`` rebuild the current snapshots once.

Downgrade turns ``recipe`` lines back into ``unmatched`` ones and loses
``ref_path``: their base names then wait in the resolve queue until an upgrade
and a ``--reparse`` rescan. Cost lines and snapshots are derivations; their new
columns drop with no fix-up (a partial-sourced line was counted priced already).

Revision ID: 0071
Revises: 0070
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

_OLD = "resolution IN ('alias', 'manual', 'unmatched', 'negligible', 'ignored')"
_NEW = "resolution IN ('alias', 'manual', 'unmatched', 'negligible', 'ignored', 'recipe')"


def upgrade() -> None:
    op.add_column("recipe", sa.Column("makes_qty", sa.Numeric()))
    op.add_column(
        "recipe", sa.Column("makes_unit", sa.String(16), sa.ForeignKey("unit.code"), nullable=True)
    )
    op.add_column("recipe", sa.Column("makes_text", sa.Text()))

    op.drop_constraint("ck_recipe_ingredient_resolution", "recipe_ingredient", type_="check")
    op.create_check_constraint("ck_recipe_ingredient_resolution", "recipe_ingredient", _NEW)
    op.add_column("recipe_ingredient", sa.Column("ref_path", sa.Text()))
    op.add_column(
        "recipe_ingredient",
        sa.Column(
            "ref_recipe_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_recipe_ingredient_ref_recipe_id", "recipe_ingredient", ["ref_recipe_id"])

    op.add_column(
        "ingredient",
        sa.Column(
            "made_by_recipe_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_ingredient_made_by_recipe_id", "ingredient", ["made_by_recipe_id"])

    op.add_column(
        "recipe_cost_line",
        sa.Column(
            "source_recipe_id",
            UUID(as_uuid=True),
            sa.ForeignKey("recipe.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "recipe_cost_line",
        sa.Column("source_partial", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "recipe_cost_snapshot",
        sa.Column("lines_partial", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("recipe_cost_snapshot", "lines_partial")
    op.drop_column("recipe_cost_line", "source_partial")
    op.drop_column("recipe_cost_line", "source_recipe_id")
    op.drop_index("ix_ingredient_made_by_recipe_id", table_name="ingredient")
    op.drop_column("ingredient", "made_by_recipe_id")
    op.drop_index("ix_recipe_ingredient_ref_recipe_id", table_name="recipe_ingredient")
    op.drop_column("recipe_ingredient", "ref_recipe_id")
    op.drop_column("recipe_ingredient", "ref_path")
    op.execute(
        "UPDATE recipe_ingredient SET resolution = 'unmatched', negligible = false "
        "WHERE resolution = 'recipe'"
    )
    op.drop_constraint("ck_recipe_ingredient_resolution", "recipe_ingredient", type_="check")
    op.create_check_constraint("ck_recipe_ingredient_resolution", "recipe_ingredient", _OLD)
    op.drop_column("recipe", "makes_text")
    op.drop_column("recipe", "makes_unit")
    op.drop_column("recipe", "makes_qty")
