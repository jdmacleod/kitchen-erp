"""Recipe body and relink dismissals (07, 3E; package 8).

``recipe.body`` keeps the parsed file as plain JSON so the recipe page can
render the steps, not only the ingredient rows: a list of sections, each a
name and its steps, each step a list of items (text, ingredient, cookware,
timer) with decimals as strings. It is derived from the file like the
``recipe_ingredient`` rows and, like them, kept when the file stops parsing.

``recipe.relink_dismissed_paths`` lists the paths of the relink candidates a
person refused with "Not the same", so a later scan that meets a new file at
one of them does not propose it again. Proposals are made when a file first
appears in the index, so the path is the thing that can recur, not the row.

Downgrade drops both columns.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipe",
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "recipe",
        sa.Column("relink_dismissed_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recipe", "relink_dismissed_paths")
    op.drop_column("recipe", "body")
