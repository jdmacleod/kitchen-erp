"""Recipe lines can be ignored (07, Phase 3, 3C).

A name a person marks as not an ingredient goes in ``recipe_name_ignore`` and
its lines take ``resolution = 'ignored'``, so they are neither queued nor
counted as unmapped. The check constraint on ``recipe_ingredient.resolution``
gains that value.

Downgrade turns ignored lines back to ``unmatched`` (the ``recipe_name_ignore``
rows stay, so an upgrade's next scan ignores them again) and restores the
constraint.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_recipe_ingredient_resolution"
_BEFORE = "resolution IN ('alias', 'manual', 'unmatched', 'negligible')"
_AFTER = "resolution IN ('alias', 'manual', 'unmatched', 'negligible', 'ignored')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "recipe_ingredient", type_="check")
    op.create_check_constraint(_CONSTRAINT, "recipe_ingredient", _AFTER)


def downgrade() -> None:
    op.execute("UPDATE recipe_ingredient SET resolution = 'unmatched' WHERE resolution = 'ignored'")
    op.drop_constraint(_CONSTRAINT, "recipe_ingredient", type_="check")
    op.create_check_constraint(_CONSTRAINT, "recipe_ingredient", _BEFORE)
