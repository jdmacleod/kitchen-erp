"""Product captures and proposals (04, 2L).

``product_capture`` is the evidence: what a page, a scan, a photo or the helper
sent. It is append-only, apart from removing the page text (``dom_text``) from
its payload once its proposal is decided: the runtime role may update only
``payload``, and a trigger allows only that removal (F3, PV12). ``app/core/grants.py``
lists the column grant so a restore re-applies it.

``product_proposal`` is what the evidence says. ``listing_key`` and ``gtin_key``
are generated from it, and a partial unique index on each allows one *pending*
proposal per vendor page, and one per GTIN among proposals without a page (PR5).

Photos and product jobs can now belong to a proposal (PR13): a candidate photo
has a proposal and no product until it is accepted.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"
CHANNELS = ("clip", "paste_url", "barcode", "photo", "helper")
KINDS = ("new_product", "product_update")
STATUSES = ("pending", "accepted", "rejected", "superseded")

PURGE_ONLY = """
CREATE OR REPLACE FUNCTION kerp_capture_purge_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'product_capture is append-only'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.id, NEW.sha256, NEW.channel, NEW.source_url, NEW.captured_at, NEW.created_by,
        NEW.created_at) IS DISTINCT FROM
       (OLD.id, OLD.sha256, OLD.channel, OLD.source_url, OLD.captured_at, OLD.created_by,
        OLD.created_at)
       OR NEW.capture_geo::text IS DISTINCT FROM OLD.capture_geo::text
       OR NEW.payload IS DISTINCT FROM (OLD.payload - 'dom_text') THEN
        RAISE EXCEPTION 'product_capture is append-only apart from removing dom_text'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "product_capture",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("channel", CHANNELS), name="ck_product_capture_channel"),
    )
    op.execute("ALTER TABLE product_capture ADD COLUMN capture_geo geography(Point, 4326)")
    op.create_index("ix_product_capture_sha256", "product_capture", ["sha256"])

    op.create_table(
        "product_proposal",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "capture_id", UUID(as_uuid=True), sa.ForeignKey("product_capture.id"), nullable=True
        ),
        sa.Column("kind", sa.String(16), nullable=False, server_default="new_product"),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("fields", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("match", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("listing", JSONB(), nullable=True),
        sa.Column("price", JSONB(), nullable=True),
        sa.Column(
            "listing_key",
            sa.Text(),
            sa.Computed("(listing ->> 'vendor_id') || ' ' || (listing ->> 'canonical_url')"),
        ),
        sa.Column(
            "gtin_key",
            sa.Text(),
            sa.Computed("CASE WHEN listing IS NULL THEN fields -> 'gtin' ->> 'value' END"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("kind", KINDS), name="ck_product_proposal_kind"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_product_proposal_status"),
        sa.CheckConstraint(
            "kind <> 'product_update' OR product_id IS NOT NULL",
            name="ck_product_proposal_update_target",
        ),
    )
    op.create_index("ix_product_proposal_capture_id", "product_proposal", ["capture_id"])
    op.create_index(
        "ix_product_proposal_pending",
        "product_proposal",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_product_proposal_pending_listing",
        "product_proposal",
        ["listing_key"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND listing_key IS NOT NULL"),
    )
    op.create_index(
        "uq_product_proposal_pending_gtin",
        "product_proposal",
        ["gtin_key"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND gtin_key IS NOT NULL"),
    )

    # Photos and jobs of a proposal (PR13).
    op.add_column(
        "product_image",
        sa.Column(
            "proposal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_proposal.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_product_image_proposal_id", "product_image", ["proposal_id"])
    op.create_unique_constraint(
        "uq_product_image_proposal_upload", "product_image", ["proposal_id", "upload_sha256"]
    )
    op.drop_constraint("ck_product_image_owner", "product_image", type_="check")
    op.create_check_constraint(
        "ck_product_image_owner",
        "product_image",
        "product_id IS NOT NULL OR proposal_id IS NOT NULL",
    )
    op.add_column(
        "product_job",
        sa.Column(
            "product_capture_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_capture.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_product_job_product_capture_id", "product_job", ["product_capture_id"])

    # Append-only apart from purging dom_text (F3): privileges first, trigger second.
    op.execute(f"REVOKE UPDATE, DELETE ON product_capture FROM {APP_ROLE}")
    op.execute(f"GRANT UPDATE (payload) ON product_capture TO {APP_ROLE}")
    op.execute(PURGE_ONLY)
    op.execute(
        "CREATE TRIGGER trg_product_capture_purge_only BEFORE UPDATE OR DELETE "
        "ON product_capture FOR EACH ROW EXECUTE FUNCTION kerp_capture_purge_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_product_capture_purge_only ON product_capture")
    op.execute("DROP FUNCTION IF EXISTS kerp_capture_purge_only()")
    op.drop_index("ix_product_job_product_capture_id", table_name="product_job")
    op.drop_column("product_job", "product_capture_id")
    op.execute("DELETE FROM product_image WHERE product_id IS NULL")
    op.drop_constraint("ck_product_image_owner", "product_image", type_="check")
    op.create_check_constraint("ck_product_image_owner", "product_image", "product_id IS NOT NULL")
    op.drop_constraint("uq_product_image_proposal_upload", "product_image", type_="unique")
    op.drop_index("ix_product_image_proposal_id", table_name="product_image")
    op.drop_column("product_image", "proposal_id")
    op.drop_table("product_proposal")
    op.drop_table("product_capture")
