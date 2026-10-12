"""Drive times between places (17, Phase 4B, package 5).

``travel_leg`` is the free-flow time and distance from one place to another,
one row per ordered pair, with no multiplier stored: time of day is applied
when a plan is made. ``source`` says whether the routing engine answered
(``osrm``) or the leg is haversine times a road factor (``estimate``); an
estimate is replaced when the engine next answers, never the reverse.

The table is derived and rebuildable from places plus the engine, so beside
ordinary DML the runtime role gets TRUNCATE and the table joins ``TRUNCATABLE``
in ``app/core/grants.py``. Both foreign keys cascade: a leg without one of its
places is nothing.

Downgrade drops the table; the grants go with it.

Revision ID: 0052
Revises: 0050
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0052"
down_revision = "0050"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"


def upgrade() -> None:
    op.create_table(
        "travel_leg",
        sa.Column(
            "from_place_id",
            UUID(as_uuid=True),
            sa.ForeignKey("place.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "to_place_id",
            UUID(as_uuid=True),
            sa.ForeignKey("place.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("meters", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("profile", sa.String(16), nullable=False, server_default="car"),
        sa.Column("engine_version", sa.String(80), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("from_place_id <> to_place_id", name="ck_travel_leg_distinct"),
        sa.CheckConstraint("seconds >= 0", name="ck_travel_leg_seconds"),
        sa.CheckConstraint("meters >= 0", name="ck_travel_leg_meters"),
        sa.CheckConstraint("source IN ('osrm', 'estimate')", name="ck_travel_leg_source"),
    )
    # The primary key serves lookups by origin; deletes by destination need their own.
    op.create_index("ix_travel_leg_to_place_id", "travel_leg", ["to_place_id"])
    # Derived, rebuildable: ordinary DML plus TRUNCATE for `kerp travel-legs recompute --all`.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON travel_leg TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("travel_leg")
