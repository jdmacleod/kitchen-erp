"""Trip plans (17, Phase 4B, package 6): the planner's inputs, its three variants,
their stops and what to buy at each.

``trip_plan`` is a fact a person stated: the list, the kitchen, where the trip
starts and ends, when, the value of an hour, how many stops at most, a mileage
rate and the default stop overhead, and later the choice. ``trip_plan_variant``,
``trip_plan_stop`` and ``trip_plan_stop_item`` are derived until chosen: all
three variants are stored so the choice is auditable, and the runtime role may
TRUNCATE the three derived tables (they join ``TRUNCATABLE`` in
``app/core/grants.py``). Once ``chosen_variant_id`` is set they are the frozen
prediction 4C compares against; replanning creates a new plan (P1).

``chosen_variant_id`` closes a cycle between ``trip_plan`` and
``trip_plan_variant``, so it is added after both tables exist, and dropped first
on the way down. ``purchase.trip_plan_id`` is the nullable link 02 anticipated
and 4C fills. A variant goes with its plan, a stop with its variant, a stop
item with its stop and with the list item it buys; a purchase keeps its row
when a plan is deleted (the link is set null).

Downgrade drops the purchase column and the four tables.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"
TABLES = ("trip_plan", "trip_plan_variant", "trip_plan_stop", "trip_plan_stop_item")
DERIVED = ("trip_plan_variant", "trip_plan_stop", "trip_plan_stop_item")


def _id() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), primary_key=True)


def _fk(name: str, target: str, *, nullable: bool, ondelete: str | None = None) -> sa.Column:
    return sa.Column(
        name, UUID(as_uuid=True), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


def upgrade() -> None:
    op.create_table(
        "trip_plan",
        _id(),
        _fk("list_id", "shopping_list.id", nullable=False),
        _fk("home_base_id", "home_base.id", nullable=False),
        _fk("origin_place_id", "place.id", nullable=False),
        _fk("destination_place_id", "place.id", nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_of_time_per_hour", sa.Numeric(8, 2), nullable=False),
        sa.Column("max_stops", sa.SmallInteger(), nullable=False),
        sa.Column("mileage_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("stop_overhead_min", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
        _fk("created_by", "app_user.id", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("chosen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("max_stops BETWEEN 1 AND 4", name="ck_trip_plan_max_stops"),
        sa.CheckConstraint("value_of_time_per_hour >= 0", name="ck_trip_plan_value_of_time"),
        sa.CheckConstraint(
            "mileage_rate IS NULL OR mileage_rate >= 0", name="ck_trip_plan_mileage"
        ),
        sa.CheckConstraint("stop_overhead_min >= 0", name="ck_trip_plan_stop_overhead"),
        sa.CheckConstraint(
            "status IN ('planned', 'underway', 'done', 'abandoned')", name="ck_trip_plan_status"
        ),
    )
    op.create_index("ix_trip_plan_home_base_id", "trip_plan", ["home_base_id"])
    op.create_index("ix_trip_plan_list_id", "trip_plan", ["list_id"])

    op.create_table(
        "trip_plan_variant",
        _id(),
        _fk("plan_id", "trip_plan.id", nullable=False, ondelete="CASCADE"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("item_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("driving_seconds", sa.Integer(), nullable=False),
        sa.Column("stop_seconds", sa.Integer(), nullable=False),
        sa.Column("mileage_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("total_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.Column("annotations", JSONB, nullable=False, server_default="[]"),
        sa.UniqueConstraint("plan_id", "kind", name="uq_trip_plan_variant_kind"),
        sa.CheckConstraint(
            "kind IN ('fastest', 'cheapest', 'balanced')", name="ck_trip_plan_variant_kind"
        ),
        sa.CheckConstraint("driving_seconds >= 0", name="ck_trip_plan_variant_driving"),
        sa.CheckConstraint("stop_seconds >= 0", name="ck_trip_plan_variant_stop"),
    )

    # The choice closes a cycle; the constraint comes once both tables exist.
    op.add_column(
        "trip_plan",
        sa.Column(
            "chosen_variant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("trip_plan_variant.id", name="fk_trip_plan_chosen_variant"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_trip_plan_chosen_at",
        "trip_plan",
        "(chosen_variant_id IS NULL) = (chosen_at IS NULL)",
    )

    op.create_table(
        "trip_plan_stop",
        _id(),
        _fk("variant_id", "trip_plan_variant.id", nullable=False, ondelete="CASCADE"),
        sa.Column("seq", sa.Integer(), nullable=False),
        _fk("vendor_location_id", "vendor_location.id", nullable=False),
        sa.Column("leg_seconds", sa.Integer(), nullable=False),
        sa.Column("leg_meters", sa.Integer(), nullable=False),
        sa.Column("leg_source", sa.String(16), nullable=False),
        sa.Column("multiplier", sa.Numeric(4, 2), nullable=False),
        sa.Column("arrive_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overhead_min", sa.SmallInteger(), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 4), nullable=False),
        sa.Column("hours_known", sa.Boolean(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        _fk("purchase_id", "purchase.id", nullable=True, ondelete="SET NULL"),
        sa.UniqueConstraint("variant_id", "seq", name="uq_trip_plan_stop_seq"),
        sa.CheckConstraint("seq >= 1", name="ck_trip_plan_stop_seq"),
        sa.CheckConstraint(
            "leg_source IN ('osrm', 'estimate')", name="ck_trip_plan_stop_leg_source"
        ),
        sa.CheckConstraint("leg_seconds >= 0 AND leg_meters >= 0", name="ck_trip_plan_stop_leg"),
        sa.CheckConstraint("multiplier > 0", name="ck_trip_plan_stop_multiplier"),
        sa.CheckConstraint("overhead_min >= 0", name="ck_trip_plan_stop_overhead"),
    )
    op.create_index(
        "ix_trip_plan_stop_vendor_location_id", "trip_plan_stop", ["vendor_location_id"]
    )

    op.create_table(
        "trip_plan_stop_item",
        _id(),
        _fk("stop_id", "trip_plan_stop.id", nullable=False, ondelete="CASCADE"),
        _fk("list_item_id", "shopping_list_item.id", nullable=False, ondelete="CASCADE"),
        _fk("product_id", "product.id", nullable=False),
        sa.Column("packs", sa.Numeric(), nullable=True),
        sa.Column("pack_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("line_cost", sa.Numeric(12, 4), nullable=False),
        _fk("observation_id", "price_observation.id", nullable=False),
        sa.Column("price_stale", sa.Boolean(), nullable=False),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("actual_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("actual_qty", sa.Numeric(), nullable=True),
        sa.Column("actual_unit", sa.String(16), sa.ForeignKey("unit.code"), nullable=True),
        sa.Column("got_here", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("stop_id", "list_item_id", name="uq_trip_plan_stop_item"),
        sa.CheckConstraint("packs IS NULL OR packs >= 0", name="ck_trip_plan_stop_item_packs"),
        sa.CheckConstraint(
            "(packs IS NULL) = (pack_price IS NULL)", name="ck_trip_plan_stop_item_pack_pair"
        ),
        sa.CheckConstraint("line_cost >= 0", name="ck_trip_plan_stop_item_line_cost"),
    )
    op.create_index("ix_trip_plan_stop_item_list_item_id", "trip_plan_stop_item", ["list_item_id"])

    op.add_column(
        "purchase",
        sa.Column(
            "trip_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("trip_plan.id", name="fk_purchase_trip_plan", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_purchase_trip_plan_id",
        "purchase",
        ["trip_plan_id"],
        postgresql_where=sa.text("trip_plan_id IS NOT NULL"),
    )

    # Facts a person stated get ordinary DML; the derived tables may be emptied
    # wholesale before a rebuild while no plan has chosen them.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TABLES)} TO {APP_ROLE}")
    op.execute(f"GRANT TRUNCATE ON {', '.join(DERIVED)} TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_purchase_trip_plan_id", table_name="purchase")
    op.drop_column("purchase", "trip_plan_id")
    op.drop_constraint("fk_trip_plan_chosen_variant", "trip_plan", type_="foreignkey")
    for table in reversed(TABLES):
        op.drop_table(table)
