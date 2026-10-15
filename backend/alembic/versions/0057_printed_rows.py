"""Receipt lines keep the row they were read from; savings already in a price (2S).

``purchase_line.printed_text`` is the OCR row a line was matched to after the
model answered. The model's own ``raw_text`` often leaves out a row's codes and
prices; the printed row keeps them, for the reading passes and for review.

``purchase_line.in_price_amount`` is a saving the receipt prints but that its
item's price already includes. The line counts for nothing (``line_total`` 0)
and keeps the printed amount, so the regular price stays known.

``purchase.printed_checks`` holds what the receipt prints about itself beyond
its total: the savings total, the number of items sold, the payment amount.

Downgrade drops the three columns; the reading passes still work without them.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_line", sa.Column("printed_text", sa.Text()))
    op.add_column("purchase_line", sa.Column("in_price_amount", sa.Numeric(12, 4)))
    op.add_column("purchase", sa.Column("printed_checks", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("purchase", "printed_checks")
    op.drop_column("purchase_line", "in_price_amount")
    op.drop_column("purchase_line", "printed_text")
