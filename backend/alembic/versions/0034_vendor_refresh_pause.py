"""Listing refreshes follow fetch_policy, and pause while a store's pages are unreachable (#264).

Spec 03 lets the lookup helper fetch a vendor's pages only when its
``fetch_policy`` is ``server_fetch``, but the refresh scheduler queued every
vendor's listings. This adds what the scheduler needs to pause a vendor whose
pages keep failing to load:

- ``refresh_failures``: unreachable refresh answers since the last good one;
- ``refresh_unreachable_since``: when the first of those arrived;
- ``refresh_paused_until``: no refresh is queued for the vendor before then;
- ``refresh_backoff_days``: the length of the current pause, doubled each time
  a pause ends in another failure.

Every vendor starts at ``capture_only`` (the default), so honouring the policy
would stop all refreshes. Vendors whose pages the helper has already read (a
refresh answer with ``found: true``) are set to ``server_fetch``, recorded in
``field_source`` as written by this migration, so a person's later change wins
under the 1F rule. Downgrade returns those, if nobody has changed them since,
to ``capture_only`` and drops the columns.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

MARK = "0034"


def upgrade() -> None:
    op.add_column(
        "vendor",
        sa.Column("refresh_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("vendor", sa.Column("refresh_unreachable_since", sa.DateTime(timezone=True)))
    op.add_column("vendor", sa.Column("refresh_paused_until", sa.DateTime(timezone=True)))
    op.add_column("vendor", sa.Column("refresh_backoff_days", sa.SmallInteger()))
    op.create_check_constraint("ck_vendor_refresh_failures", "vendor", "refresh_failures >= 0")
    op.execute(
        sa.text(
            """
            UPDATE vendor v
            SET fetch_policy = 'server_fetch',
                field_source = coalesce(v.field_source, '{}'::jsonb) || jsonb_build_object(
                    'fetch_policy', jsonb_build_object(
                        'source', 'migration', 'ref', :mark,
                        'checked_at', to_char(now() AT TIME ZONE 'UTC',
                                              'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                        'imported', 'server_fetch'))
            WHERE v.fetch_policy = 'capture_only'
              AND EXISTS (
                  SELECT 1 FROM vendor_listing l
                  JOIN lookup_request r ON r.listing_id = l.id AND r.kind = 'page'
                  JOIN lookup_answer a ON a.request_id = r.id
                  WHERE l.vendor_id = v.id AND a.body ->> 'found' = 'true'
              )
            """
        ).bindparams(mark=MARK)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE vendor
            SET fetch_policy = 'capture_only',
                field_source = field_source - 'fetch_policy'
            WHERE fetch_policy = 'server_fetch'
              AND field_source -> 'fetch_policy' ->> 'ref' = :mark
              AND field_source -> 'fetch_policy' ->> 'source' = 'migration'
            """
        ).bindparams(mark=MARK)
    )
    op.drop_constraint("ck_vendor_refresh_failures", "vendor", type_="check")
    for column in (
        "refresh_backoff_days",
        "refresh_paused_until",
        "refresh_unreachable_since",
        "refresh_failures",
    ):
        op.drop_column("vendor", column)
