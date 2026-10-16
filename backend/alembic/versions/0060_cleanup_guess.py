"""Tidy the catalog: the slow guesses, kept between looks (issue 372).

``cleanup_guess`` holds, per ingredient name, what a USDA search guessed: the
category of the best fitting food's group, and the food to relink a misfit USDA
reference to. Each row names the catalog version it was guessed at (the loaded
USDA release and the guessing rules), so a new release or new rules guess again.
Derived data, not facts: the runtime role writes it through the default
privileges, and emptying it only makes the next look slower.

Downgrade drops the table; the page guesses again on every look.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cleanup_guess",
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("category", sa.String(20)),
        sa.Column("fdc_id", sa.Integer()),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('category', 'usda_link')", name="ck_cleanup_guess_kind"),
    )


def downgrade() -> None:
    op.drop_table("cleanup_guess")
