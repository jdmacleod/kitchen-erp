"""The products helper contract (04, 2N): the lookup queue, answers, posted-price changes.

``lookup_request`` is what the app hands the optional helper: barcodes and pages
a person asked about, unknown scanned barcodes when the household turns that on,
and ``cutout`` requests for the household's photos without a mask. The helper
reads it with a ``products:read`` token and answers with ``products:suggest``.

``lookup_answer`` records every answer, merged or refused, and is append-only
like the other records of what arrived (registered in ``app/core/grants.py``).
``listing_price_change`` holds refreshed posted prices until a person accepts or
rejects each one; nothing is recorded in the price book before that.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"


def upgrade() -> None:
    op.create_table(
        "lookup_request",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column(
            "proposal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_proposal.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "product_image_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_image.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('gtin', 'page', 'cutout')", name="ck_lookup_request_kind"),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'closed')", name="ck_lookup_request_status"
        ),
        # Exactly one owner, matching the kind.
        sa.CheckConstraint(
            "(kind = 'cutout' AND product_image_id IS NOT NULL AND proposal_id IS NULL "
            "AND product_id IS NULL AND value IS NULL) OR "
            "(kind <> 'cutout' AND product_image_id IS NULL AND value IS NOT NULL "
            "AND (proposal_id IS NULL) <> (product_id IS NULL))",
            name="ck_lookup_request_owner",
        ),
    )
    op.create_index(
        "ix_lookup_request_open",
        "lookup_request",
        ["created_at"],
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "uq_lookup_request_open_cutout",
        "lookup_request",
        ["product_image_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND kind = 'cutout'"),
    )

    op.create_table(
        "lookup_answer",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id", UUID(as_uuid=True), sa.ForeignKey("lookup_request.id"), nullable=True
        ),
        sa.Column("token_id", UUID(as_uuid=True), nullable=True),
        sa.Column("body", JSONB(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "outcome IN ('merged', 'refused', 'update_opened', 'no_change', 'closed')",
            name="ck_lookup_answer_outcome",
        ),
    )
    op.create_index("ix_lookup_answer_request_id", "lookup_answer", ["request_id"])

    op.create_table(
        "listing_price_change",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id", UUID(as_uuid=True), sa.ForeignKey("vendor_listing.id"), nullable=False
        ),
        sa.Column("amount", sa.Numeric(12, 4), nullable=False),
        sa.Column("qty", sa.Numeric(), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(16), sa.ForeignKey("unit.code"), nullable=False),
        sa.Column("is_promo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column(
            "observation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("price_observation.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')", name="ck_listing_price_change_status"
        ),
        sa.CheckConstraint("amount >= 0 AND qty > 0", name="ck_listing_price_change_amount"),
    )
    op.create_index(
        "ix_listing_price_change_pending",
        "listing_price_change",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # Append-only, as in 0005: privileges first, trigger second.
    op.execute(f"REVOKE UPDATE, DELETE ON lookup_answer FROM {APP_ROLE}")
    op.execute(
        "CREATE TRIGGER trg_lookup_answer_append_only BEFORE UPDATE OR DELETE "
        "ON lookup_answer FOR EACH ROW EXECUTE FUNCTION kerp_reject_modification()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_lookup_answer_append_only ON lookup_answer")
    op.drop_table("listing_price_change")
    op.drop_table("lookup_answer")
    op.drop_table("lookup_request")
