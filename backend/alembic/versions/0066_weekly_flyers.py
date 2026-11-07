"""Weekly flyers (18, 2U): sources, runs, offers, their links and learned names.

``ad_source`` names the circular a vendor location publishes and what the
products helper needs to read it. Each helper report is one ``flyer_run``; the
offers in it this household plausibly cares about are ``flyer_offer`` rows. Both
are append-only facts. ``flyer_offer_link`` is the decision of which product an
offer is, and ``flyer_alias`` the names learned from those decisions, kept apart
from ``receipt_alias``. ``ingredient.watch`` marks an ingredient whose offers
always matter, and ``lookup_request`` gains the ``flyer`` kind, owned by its
source and carrying the ad week asked for as its value.

An offer is never a price observation: no price view reads these tables.

Downgrade drops the tables and columns. Flyer requests become closed page
requests, as 0025 and 0031 do for theirs; having no owner the old check accepts,
they keep that check from being validated (``NOT VALID``).

Revision ID: 0066
Revises: 0065
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"
MECHANISMS = "'hosted_circular', 'storefront', 'itemized', 'image', 'region'"
BASES = "'each', 'per_lb', 'per_oz', 'multi', 'bogo', 'percent_off', 'dollar_off'"
LINK_COLUMNS = (
    "status, product_id, resolution, suggestions, size_inherited, corrected_price, "
    "corrected_size_qty, corrected_size_unit, decided_by, decided_at"
)

OLD_OWNER = (
    "(kind = 'cutout' AND product_image_id IS NOT NULL AND proposal_id IS NULL "
    "AND product_id IS NULL AND value IS NULL) OR "
    "(kind <> 'cutout' AND product_image_id IS NULL AND value IS NOT NULL "
    "AND (proposal_id IS NULL) <> (product_id IS NULL))"
)
NEW_OWNER = (
    "(kind = 'cutout' AND product_image_id IS NOT NULL AND proposal_id IS NULL "
    "AND product_id IS NULL AND value IS NULL AND ad_source_id IS NULL) OR "
    "(kind = 'flyer' AND ad_source_id IS NOT NULL AND value IS NOT NULL "
    "AND product_image_id IS NULL AND proposal_id IS NULL AND product_id IS NULL) OR "
    "(kind NOT IN ('cutout', 'flyer') AND product_image_id IS NULL AND ad_source_id IS NULL "
    "AND value IS NOT NULL AND (proposal_id IS NULL) <> (product_id IS NULL))"
)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "ad_source",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vendor_location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendor_location.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mechanism", sa.String(16), nullable=False),
        sa.Column("adapter", sa.String(64), nullable=False),
        sa.Column("store_ref", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("publish_weekday", sa.SmallInteger, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("require_confirmation", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("tiles", JSONB, nullable=True),
        sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("vendor_location_id", name="uq_ad_source_location"),
        sa.CheckConstraint(f"mechanism IN ({MECHANISMS})", name="ck_ad_source_mechanism"),
        sa.CheckConstraint("publish_weekday BETWEEN 1 AND 7", name="ck_ad_source_weekday"),
        sa.CheckConstraint("jsonb_typeof(store_ref) = 'object'", name="ck_ad_source_store_ref"),
        sa.CheckConstraint("failures >= 0", name="ck_ad_source_failures"),
    )

    op.add_column(
        "lookup_request",
        sa.Column(
            "ad_source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ad_source.id", ondelete="CASCADE", name="fk_lookup_request_ad_source"),
            nullable=True,
        ),
    )
    op.drop_constraint("ck_lookup_request_kind", "lookup_request", type_="check")
    op.create_check_constraint(
        "ck_lookup_request_kind",
        "lookup_request",
        "kind IN ('gtin', 'page', 'cutout', 'image', 'name', 'flyer')",
    )
    op.drop_constraint("ck_lookup_request_owner", "lookup_request", type_="check")
    op.create_check_constraint("ck_lookup_request_owner", "lookup_request", NEW_OWNER)
    # One open request per source at a time.
    op.create_index(
        "uq_lookup_request_open_ad_source",
        "lookup_request",
        ["ad_source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND ad_source_id IS NOT NULL"),
    )

    op.create_table(
        "flyer_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ad_source_id", UUID(as_uuid=True), sa.ForeignKey("ad_source.id"), nullable=False
        ),
        sa.Column(
            "lookup_request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("lookup_request.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("adapter_version", sa.String(40), nullable=False),
        sa.Column("ad_week_from", sa.Date, nullable=True),
        sa.Column("ad_week_to", sa.Date, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offered_count", sa.Integer, nullable=False),
        sa.Column("relevant_count", sa.Integer, nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('ok', 'empty', 'failed', 'not_allowed')", name="ck_flyer_run_status"
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'empty') OR reason IS NOT NULL", name="ck_flyer_run_reason"
        ),
        sa.CheckConstraint(
            "status <> 'ok' OR (ad_week_from IS NOT NULL AND ad_week_to IS NOT NULL "
            "AND ad_week_from <= ad_week_to)",
            name="ck_flyer_run_week",
        ),
        sa.CheckConstraint(
            "offered_count >= 0 AND relevant_count >= 0 AND relevant_count <= offered_count",
            name="ck_flyer_run_counts",
        ),
    )
    op.create_index("ix_flyer_run_source_week", "flyer_run", ["ad_source_id", "ad_week_from"])

    op.create_table(
        "flyer_offer",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "flyer_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flyer_run.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name_as_printed", sa.Text, nullable=False),
        sa.Column("name_norm", sa.Text, nullable=False),
        sa.Column("name_original", sa.Text, nullable=True),
        sa.Column("brand", sa.Text, nullable=True),
        sa.Column("size_qty", sa.Numeric, nullable=True),
        sa.Column("size_unit", sa.String(16), sa.ForeignKey("unit.code"), nullable=True),
        sa.Column("price_basis", sa.String(12), nullable=False),
        sa.Column("price", sa.Numeric(12, 4), nullable=True),
        sa.Column("basis_qty", sa.Numeric, nullable=True),
        sa.Column("discount", sa.Numeric(12, 4), nullable=True),
        sa.Column("requires_loyalty", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("limit_qty", sa.Numeric, nullable=True),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_to", sa.Date, nullable=False),
        sa.Column("department", sa.Text, nullable=True),
        sa.Column("item_ref", sa.Text, nullable=True),
        sa.Column("source_page", sa.Integer, nullable=True),
        sa.Column("source_bbox", ARRAY(sa.Numeric), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("relevance", sa.String(12), nullable=False),
        _created_at(),
        sa.CheckConstraint(f"price_basis IN ({BASES})", name="ck_flyer_offer_basis"),
        sa.CheckConstraint(
            "(price_basis IN ('percent_off', 'dollar_off') AND discount > 0 AND price IS NULL) "
            "OR (price_basis NOT IN ('percent_off', 'dollar_off') AND price >= 0 "
            "AND discount IS NULL)",
            name="ck_flyer_offer_amount",
        ),
        sa.CheckConstraint(
            "(price_basis = 'multi') = (basis_qty IS NOT NULL) "
            "AND (basis_qty IS NULL OR basis_qty > 0)",
            name="ck_flyer_offer_basis_qty",
        ),
        sa.CheckConstraint(
            "(size_qty IS NULL) = (size_unit IS NULL) AND (size_qty IS NULL OR size_qty > 0)",
            name="ck_flyer_offer_size",
        ),
        sa.CheckConstraint("valid_from <= valid_to", name="ck_flyer_offer_dates"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_flyer_offer_confidence"),
        sa.CheckConstraint(
            "source_bbox IS NULL OR cardinality(source_bbox) = 4", name="ck_flyer_offer_bbox"
        ),
        sa.CheckConstraint(
            "relevance IN ('alias', 'purchased', 'recipe', 'list', 'watched')",
            name="ck_flyer_offer_relevance",
        ),
    )
    op.create_index("ix_flyer_offer_valid", "flyer_offer", ["valid_from", "valid_to"])

    op.create_table(
        "flyer_offer_link",
        sa.Column(
            "flyer_offer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flyer_offer.id"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("product_id", UUID(as_uuid=True), sa.ForeignKey("product.id"), nullable=True),
        sa.Column("resolution", sa.String(16), nullable=True),
        sa.Column("suggestions", JSONB, nullable=True),
        sa.Column("size_inherited", sa.Boolean, nullable=False, server_default=sa.false()),
        # A person's correction of the printed price or size (2W); the offer stays as printed.
        sa.Column("corrected_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("corrected_size_qty", sa.Numeric, nullable=True),
        sa.Column("corrected_size_unit", sa.String(16), sa.ForeignKey("unit.code"), nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('auto', 'suggested', 'confirmed', 'rejected')",
            name="ck_flyer_offer_link_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('auto', 'confirmed') OR product_id IS NOT NULL",
            name="ck_flyer_offer_link_product",
        ),
        sa.CheckConstraint(
            "corrected_price IS NULL OR corrected_price >= 0", name="ck_flyer_offer_link_price"
        ),
        sa.CheckConstraint(
            "(corrected_size_qty IS NULL) = (corrected_size_unit IS NULL) "
            "AND (corrected_size_qty IS NULL OR corrected_size_qty > 0)",
            name="ck_flyer_offer_link_size",
        ),
    )
    op.create_index("ix_flyer_offer_link_product", "flyer_offer_link", ["product_id"])
    op.create_index(
        "ix_flyer_offer_link_suggested",
        "flyer_offer_link",
        ["status"],
        postgresql_where=sa.text("status = 'suggested'"),
    )

    # The page region an image or region offer was read from, posted by the helper (2W).
    op.create_table(
        "flyer_offer_crop",
        sa.Column(
            "flyer_offer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flyer_offer.id"),
            primary_key=True,
        ),
        sa.Column("media_path", sa.Text, nullable=False),
        _created_at(),
    )

    op.create_table(
        "flyer_alias",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendor.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name_norm", sa.Text, nullable=False),
        sa.Column("product_id", UUID(as_uuid=True), sa.ForeignKey("product.id"), nullable=True),
        sa.Column("ignore", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("confirmed_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _created_at(),
        sa.UniqueConstraint("vendor_id", "name_norm", name="uq_flyer_alias_vendor_name"),
        sa.CheckConstraint("ignore <> (product_id IS NOT NULL)", name="ck_flyer_alias_target"),
    )

    op.add_column(
        "ingredient", sa.Column("watch", sa.Boolean, nullable=False, server_default=sa.false())
    )

    # Facts are append-only (non-negotiable 4's rule, applied to offers too).
    op.execute(f"REVOKE UPDATE, DELETE ON flyer_run, flyer_offer FROM {APP_ROLE}")
    for table in ("flyer_run", "flyer_offer"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION kerp_reject_modification()"
        )
    op.execute(f"REVOKE UPDATE, DELETE ON flyer_offer_link FROM {APP_ROLE}")
    op.execute(f"GRANT UPDATE ({LINK_COLUMNS}) ON flyer_offer_link TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_column("ingredient", "watch")
    op.drop_table("flyer_alias")
    op.drop_table("flyer_offer_crop")
    op.drop_table("flyer_offer_link")
    op.execute("DROP TRIGGER IF EXISTS trg_flyer_offer_append_only ON flyer_offer")
    op.execute("DROP TRIGGER IF EXISTS trg_flyer_run_append_only ON flyer_run")
    op.drop_table("flyer_offer")
    op.drop_table("flyer_run")
    op.drop_index("uq_lookup_request_open_ad_source", table_name="lookup_request")
    op.drop_constraint("ck_lookup_request_owner", "lookup_request", type_="check")
    op.drop_constraint("ck_lookup_request_kind", "lookup_request", type_="check")
    op.execute("UPDATE lookup_request SET kind = 'page', status = 'closed' WHERE kind = 'flyer'")
    op.drop_column("lookup_request", "ad_source_id")
    op.execute(
        "ALTER TABLE lookup_request ADD CONSTRAINT ck_lookup_request_kind "
        "CHECK (kind IN ('gtin', 'page', 'cutout', 'image', 'name'))"
    )
    # A former flyer request has no owner the old check accepts, and its answers keep
    # it (lookup_answer is append-only), so the check holds for new rows only.
    op.execute(
        f"ALTER TABLE lookup_request ADD CONSTRAINT ck_lookup_request_owner "
        f"CHECK ({OLD_OWNER}) NOT VALID"
    )
    op.drop_table("ad_source")
