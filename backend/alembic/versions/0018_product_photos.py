"""Product photos and the product work queue (03, 1I).

Adds ``product_image`` (a photo of a product; its files live under MEDIA_PATH),
``product_job`` (product work for the worker) and ``product_stage_result``,
which is append-only like ``ingest_stage_result``: the runtime role may only
SELECT and INSERT, and a trigger rejects UPDATE and DELETE. ``app/core/grants.py``
lists it so a restore re-applies the same privileges.

``product.primary_image_id`` is the main photo. Its foreign key is deferrable,
because a photo and the product's choice of it can change in one transaction.

Proposals (2L) add ``product_image.proposal_id`` and ``product_job.product_capture_id``;
until then every photo belongs to a product.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"

SOURCES = ("user_photo", "manufacturer", "open_food_facts", "vendor_listing")
ROLES = ("product", "label_front", "label_nutrition", "label_ingredients", "shelf_tag")
STATUSES = ("processing", "candidate", "active", "hidden", "failed")
JOB_KINDS = ("extract", "resolve", "image_process", "identify")
JOB_STATUSES = ("pending", "running", "done", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "product_image",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendor.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("upload_sha256", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("role", sa.String(24), nullable=False, server_default="product"),
        sa.Column("status", sa.String(16), nullable=False, server_default="processing"),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("phash", sa.BigInteger(), nullable=True),
        sa.Column("mask_sha256", sa.String(64), nullable=True),
        sa.Column("cutout_source", sa.String(8), nullable=True),
        sa.Column("is_stock_suspect", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in("source_kind", SOURCES), name="ck_product_image_source"),
        sa.CheckConstraint(_in("role", ROLES), name="ck_product_image_role"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_product_image_status"),
        sa.CheckConstraint(
            "cutout_source IS NULL OR cutout_source IN ('device', 'tool')",
            name="ck_product_image_cutout_source",
        ),
        sa.CheckConstraint(
            "(mask_sha256 IS NULL) = (cutout_source IS NULL)", name="ck_product_image_mask_pair"
        ),
        sa.CheckConstraint("product_id IS NOT NULL", name="ck_product_image_owner"),
        sa.UniqueConstraint("product_id", "upload_sha256", name="uq_product_image_upload"),
    )
    op.create_index("ix_product_image_product_id", "product_image", ["product_id"])
    op.create_index("ix_product_image_sha256", "product_image", ["sha256"])
    op.create_index(
        "uq_product_image_one_pinned",
        "product_image",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("pinned"),
    )

    op.add_column("product", sa.Column("primary_image_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_product_primary_image",
        "product",
        "product_image",
        ["primary_image_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "product_job",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "product_image_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_image.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(200), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in("kind", JOB_KINDS), name="ck_product_job_kind"),
        sa.CheckConstraint(_in("status", JOB_STATUSES), name="ck_product_job_status"),
    )
    op.create_index("ix_product_job_product_image_id", "product_job", ["product_image_id"])
    op.create_index(
        "ix_product_job_claimable",
        "product_job",
        ["created_at"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "product_stage_result",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("product_job.id"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("adapter", sa.String(100), nullable=False),
        sa.Column("adapter_version", sa.String(50), nullable=False),
        sa.Column("output", JSONB(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_product_stage_result_job_id", "product_stage_result", ["job_id"])
    # Append-only, as in 0005: privileges first, trigger second.
    op.execute(f"REVOKE UPDATE, DELETE ON product_stage_result FROM {APP_ROLE}")
    op.execute(
        "CREATE TRIGGER trg_product_stage_result_append_only BEFORE UPDATE OR DELETE "
        "ON product_stage_result FOR EACH ROW EXECUTE FUNCTION kerp_reject_modification()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_product_stage_result_append_only ON product_stage_result"
    )
    op.drop_table("product_stage_result")
    op.drop_table("product_job")
    op.drop_constraint("fk_product_primary_image", "product", type_="foreignkey")
    op.drop_column("product", "primary_image_id")
    op.drop_table("product_image")
