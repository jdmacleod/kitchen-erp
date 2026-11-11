"""Re-reads gain a third mode: transcribe the receipt again (04, 2S; #491).

``line_reread.mode`` takes ``transcribe``, which is longer than the column's
eight characters, so the column widens to sixteen.

Downgrade removes transcribe re-reads (ordinary mutable state, made again on
request), then restores the old check and width.

Revision ID: 0069
Revises: 0068
Create Date: 2026-11-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_line_reread_mode", "line_reread", type_="check")
    op.alter_column("line_reread", "mode", type_=sa.String(16), existing_nullable=False)
    op.create_check_constraint(
        "ck_line_reread_mode", "line_reread", "mode IN ('check', 'read', 'transcribe')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM line_reread WHERE mode = 'transcribe'")
    op.drop_constraint("ck_line_reread_mode", "line_reread", type_="check")
    op.alter_column("line_reread", "mode", type_=sa.String(8), existing_nullable=False)
    op.create_check_constraint("ck_line_reread_mode", "line_reread", "mode IN ('check', 'read')")
