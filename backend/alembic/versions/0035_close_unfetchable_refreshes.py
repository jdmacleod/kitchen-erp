"""Close listing refreshes queued for vendors whose pages may not be fetched (#264).

0034 stopped queuing listing refreshes for vendors whose ``fetch_policy`` isn't
``server_fetch``, or whose refreshes are paused. Refreshes queued before then
stayed open, so the helper kept fetching those stores. This closes them.

The ids it closes are kept in ``lookup_refresh_closed_0035``, so downgrade
reopens exactly those (if still closed) and drops the table. Page requests for
proposals, and every other kind, are left alone.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

CLOSED = "lookup_refresh_closed_0035"


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
        JOIN vendor_listing l ON l.id = r.listing_id
        JOIN vendor v ON v.id = l.vendor_id
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
