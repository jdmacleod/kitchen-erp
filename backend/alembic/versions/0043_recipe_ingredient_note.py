"""Recipe ingredient note (07, Phase 3, package 3).

``recipe_ingredient.note`` keeps the parenthesised note on an ingredient
reference, ``@name{qty}(note)``, as written. 07's data model lists no such
column; the parser (3B) carries the note and 3C's prep-word stripping reads and
writes it, so it lives beside the other derived columns.

Downgrade drops the column.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipe_ingredient", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recipe_ingredient", "note")
