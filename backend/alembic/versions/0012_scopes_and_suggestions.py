"""Scoped API tokens and vendor suggestions from an outside tool (1F).

``api_token.scopes`` limits what a token may do. ``*`` is every right a person
has, and every token that existed before this migration gets it, so nothing
that worked stops working. ``vendors:read`` reads the public vendor export
only; ``vendors:suggest`` posts suggestions only.

``vendor_suggestion`` holds what a tool proposed. It is append-only apart from
its decision: the runtime role may update ``status``, ``decided_by`` and
``decided_at`` and nothing else, and a trigger refuses any other change and
every delete, the owner's included, as for the price tables.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"
FIELDS = (
    "website",
    "brand",
    "wikidata",
    "phone",
    "address",
    "opening_hours",
    "osm",
    "name",
    "price_scope",
)

DECISION_ONLY = """
CREATE OR REPLACE FUNCTION kerp_suggestion_decision_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'vendor_suggestion is append-only: rows are never deleted'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (to_jsonb(NEW) - 'status' - 'decided_by' - 'decided_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'decided_by' - 'decided_at') THEN
        RAISE EXCEPTION 'vendor_suggestion: only status, decided_by and decided_at may change'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.add_column(
        "api_token",
        sa.Column(
            "scopes", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{*}'::text[]")
        ),
    )
    op.create_table(
        "vendor_suggestion",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("target", sa.String(16), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("vendor.id"), nullable=True),
        sa.Column(
            "vendor_location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendor_location.id"),
            nullable=True,
        ),
        sa.Column("field", sa.String(32), nullable=False),
        sa.Column("old_value", JSONB, nullable=True),
        sa.Column("proposed_value", JSONB, nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("evidence", sa.String(1000), nullable=True),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column("tool_version", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "created_by_token_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_token.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "decided_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id", ondelete="SET NULL")
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("target IN ('vendor', 'location')", name="ck_suggestion_target"),
        sa.CheckConstraint(
            "(target = 'vendor') = (vendor_id IS NOT NULL AND vendor_location_id IS NULL) "
            "AND (target = 'location') = (vendor_location_id IS NOT NULL AND vendor_id IS NULL)",
            name="ck_suggestion_one_target",
        ),
        sa.CheckConstraint(
            "field IN (" + ", ".join(f"'{f}'" for f in FIELDS) + ")", name="ck_suggestion_field"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'stale')", name="ck_suggestion_status"
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)", name="ck_suggestion_decided"
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_suggestion_confidence",
        ),
    )
    # Pending suggestions are what the inbox counts and review lists.
    op.create_index(
        "ix_suggestion_pending",
        "vendor_suggestion",
        ["vendor_id", "vendor_location_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    # Suggestions are append-only apart from the decision: privileges first, trigger second.
    op.execute(f"REVOKE UPDATE, DELETE ON vendor_suggestion FROM {APP_ROLE}")
    op.execute(f"GRANT UPDATE (status, decided_by, decided_at) ON vendor_suggestion TO {APP_ROLE}")
    op.execute(DECISION_ONLY)
    op.execute(
        "CREATE TRIGGER trg_vendor_suggestion_decision_only BEFORE UPDATE OR DELETE "
        "ON vendor_suggestion FOR EACH ROW EXECUTE FUNCTION kerp_suggestion_decision_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_vendor_suggestion_decision_only ON vendor_suggestion")
    op.execute("DROP FUNCTION IF EXISTS kerp_suggestion_decision_only()")
    op.drop_index("ix_suggestion_pending", table_name="vendor_suggestion")
    op.drop_table("vendor_suggestion")
    op.drop_column("api_token", "scopes")
