"""Weekly flyers in the trip planner (18, 2X, X1).

``trip_plan.with_offers`` records that the plan was asked to use this week's
offers. A stop item an offer priced names it in ``flyer_offer_id``; its
``observation_id`` is still the price-book price the offer stood in for. A stop's
``expected_saving`` is what its offers save against the trailing median regular
prices there, null when none of its items rests on an offer.

Downgrade drops the three columns: the plans keep their costs as planned.

Revision ID: 0067
Revises: 0066
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trip_plan",
        sa.Column("with_offers", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("trip_plan_stop", sa.Column("expected_saving", sa.Numeric(12, 4)))
    op.create_check_constraint(
        "ck_trip_plan_stop_expected_saving",
        "trip_plan_stop",
        "expected_saving IS NULL OR expected_saving >= 0",
    )
    op.add_column(
        "trip_plan_stop_item",
        sa.Column(
            "flyer_offer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flyer_offer.id", name="fk_trip_plan_stop_item_flyer_offer"),
        ),
    )


def downgrade() -> None:
    op.drop_column("trip_plan_stop_item", "flyer_offer_id")
    op.drop_constraint("ck_trip_plan_stop_expected_saving", "trip_plan_stop", type_="check")
    op.drop_column("trip_plan_stop", "expected_saving")
    op.drop_column("trip_plan", "with_offers")
