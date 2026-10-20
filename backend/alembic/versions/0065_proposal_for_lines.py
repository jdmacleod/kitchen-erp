"""The receipt lines a clipped product was for (04, 2D).

``product_proposal.for_vendor_id`` and ``for_raw_text_norm`` name a group of the
To identify queue: the vendor's waiting lines with that wording. Accepting the
proposal identifies them as its product. Both are set or neither; a deleted vendor
leaves the proposal without them.

Downgrade drops the columns.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_proposal",
        sa.Column(
            "for_vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendor.id", ondelete="SET NULL", name="fk_product_proposal_for_vendor"),
            nullable=True,
        ),
    )
    op.add_column("product_proposal", sa.Column("for_raw_text_norm", sa.Text(), nullable=True))
    # A deleted vendor nulls only the vendor, so the wording alone is allowed too.
    op.create_check_constraint(
        "ck_product_proposal_for_lines",
        "product_proposal",
        "for_vendor_id IS NULL OR for_raw_text_norm IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_product_proposal_for_lines", "product_proposal", type_="check")
    op.drop_column("product_proposal", "for_raw_text_norm")
    op.drop_column("product_proposal", "for_vendor_id")
