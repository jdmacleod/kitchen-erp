"""Close clip price lookups queued for vendors whose pages may not be fetched (#264).

A clip that ended with a listing but no price queued a page request for the
helper (#234) whatever the store's ``fetch_policy``. Only ``server_fetch``
vendors may be fetched by the helper (03), so open requests for any other
vendor, or for one whose fetches are paused, are closed here; new ones are no
longer queued.

The ids it closes are kept in ``lookup_clip_closed_0036``, so downgrade reopens
exactly those (if still closed) and drops the table. Listing refreshes (0035)
and every other kind are left alone.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

CLOSED = "lookup_clip_closed_0036"


def upgrade() -> None:
    op.create_table(
        CLOSED,
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lookup_request.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.execute(
        f"""
        INSERT INTO {CLOSED} (request_id)
        SELECT r.id
        FROM lookup_request r
        JOIN product_proposal p ON p.id = r.proposal_id
        JOIN vendor v ON v.id::text = p.listing ->> 'vendor_id'
        WHERE r.status = 'open' AND r.kind = 'page'
          AND (v.fetch_policy <> 'server_fetch'
               OR (v.refresh_paused_until IS NOT NULL AND v.refresh_paused_until > now()))
        """
    )
    op.execute(
        f"""
        UPDATE lookup_request SET status = 'closed'
        WHERE id IN (SELECT request_id FROM {CLOSED})
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE lookup_request SET status = 'open'
        WHERE status = 'closed' AND id IN (SELECT request_id FROM {CLOSED})
        """
    )
    op.drop_table(CLOSED)
