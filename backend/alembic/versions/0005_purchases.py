"""Phase 2: receipts, ingest, purchases, aliases, the price book, and its views.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "kerp_app"
APPEND_ONLY = ("price_observation", "price_observation_void", "ingest_stage_result")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


UUID = postgresql.UUID(as_uuid=True)

VIEWS = {
    # Non-voided observations joined to their normalization.
    "price_current": """
        CREATE VIEW price_current AS
        SELECT o.id AS observation_id, o.product_id, o.vendor_location_id, o.purchase_line_id,
               o.observed_at, o.price, o.qty, o.unit, o.is_promo, o.source, o.entered_by,
               n.canonical_qty, n.norm_unit, n.norm_unit_price, n.status AS norm_status,
               n.bridge_kind, n.bridge_source, n.bridge_confirmed
        FROM price_observation o
        LEFT JOIN price_norm n ON n.observation_id = o.id
        WHERE NOT EXISTS (SELECT 1 FROM price_observation_void v WHERE v.observation_id = o.id)
    """,
    # Every current observation, fanned out to each active location it applies
    # to: its own location, or every active location of a chain-scoped vendor.
    "offer_applicable": """
        CREATE VIEW offer_applicable AS
        SELECT pc.*, vl.id AS applies_to_location_id, v.id AS vendor_id, v.price_scope
        FROM price_current pc
        JOIN vendor_location src ON src.id = pc.vendor_location_id
        JOIN vendor v ON v.id = src.vendor_id
        JOIN vendor_location vl
          ON vl.vendor_id = v.id AND vl.active AND (v.price_scope = 'chain' OR vl.id = src.id)
    """,
    "offer_latest": """
        CREATE VIEW offer_latest AS
        SELECT DISTINCT ON (product_id, applies_to_location_id) *
        FROM offer_applicable
        ORDER BY product_id, applies_to_location_id, observed_at DESC, observation_id DESC
    """,
    # The same, ignoring promotional observations, so that a caller who excludes
    # promotions falls back to the most recent regular price rather than nothing.
    "offer_latest_regular": """
        CREATE VIEW offer_latest_regular AS
        SELECT DISTINCT ON (product_id, applies_to_location_id) *
        FROM offer_applicable
        WHERE NOT is_promo
        ORDER BY product_id, applies_to_location_id, observed_at DESC, observation_id DESC
    """,
    "ingredient_offer": """
        CREATE VIEW ingredient_offer AS
        SELECT p.ingredient_id, ol.applies_to_location_id AS vendor_location_id, ol.vendor_id,
               ol.product_id, p.quality_rating, ol.observation_id, ol.observed_at, ol.price,
               ol.qty, ol.unit, ol.is_promo, ol.canonical_qty, ol.norm_unit, ol.norm_unit_price,
               ol.norm_status, ol.bridge_kind, ol.bridge_confirmed, i.perishability
        FROM offer_latest ol
        JOIN product p ON p.id = ol.product_id AND p.active
        JOIN ingredient i ON i.id = p.ingredient_id
    """,
}

ONE_LIVE_OBSERVATION_PER_LINE = """
CREATE OR REPLACE FUNCTION kerp_one_live_observation_per_line() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.purchase_line_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM price_observation o
        WHERE o.purchase_line_id = NEW.purchase_line_id
          AND NOT EXISTS (SELECT 1 FROM price_observation_void v WHERE v.observation_id = o.id)
    ) THEN
        RAISE EXCEPTION 'purchase line % already has a live price observation; void it first',
            NEW.purchase_line_id USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "receipt_document",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True)),
        sa.Column("client_ocr_text", sa.Text()),
        sa.Column("uploaded_by", UUID, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("ALTER TABLE receipt_document ADD COLUMN capture_geo geography(Point, 4326)")

    op.create_table(
        "purchase",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("vendor_location_id", UUID, sa.ForeignKey("vendor_location.id")),
        sa.Column("receipt_document_id", UUID, sa.ForeignKey("receipt_document.id")),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 4)),
        sa.Column("tax", sa.Numeric(12, 4)),
        sa.Column("total", sa.Numeric(12, 4), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("entered_by", UUID, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("flags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("ledger_txn_ref", sa.Text()),
        sa.Column("import_ref", sa.Text()),
        *_ts(),
        sa.CheckConstraint(
            _in("status", ("draft", "reviewed", "committed")), name="ck_purchase_status"
        ),
        sa.CheckConstraint(
            _in("source", ("receipt", "manual", "import")), name="ck_purchase_source"
        ),
        sa.CheckConstraint(
            "status = 'draft' OR vendor_location_id IS NOT NULL", name="ck_purchase_location"
        ),
    )
    op.create_index("ix_purchase_vendor_location_id", "purchase", ["vendor_location_id"])
    op.create_index("ix_purchase_purchased_at", "purchase", ["purchased_at"])
    op.create_index(
        "uq_purchase_import_ref",
        "purchase",
        ["import_ref"],
        unique=True,
        postgresql_where=sa.text("import_ref IS NOT NULL"),
    )

    op.create_table(
        "ingest_job",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "receipt_document_id",
            UUID,
            sa.ForeignKey("receipt_document.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("stage", sa.String(16), nullable=False, server_default="captured"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(100)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("purchase_id", UUID, sa.ForeignKey("purchase.id", name="fk_ingest_job_purchase")),
        *_ts(),
        sa.CheckConstraint(
            _in("stage", ("captured", "ocr", "header", "lines", "resolve", "review", "committed")),
            name="ck_ingest_job_stage",
        ),
        sa.CheckConstraint(
            _in("status", ("pending", "running", "needs_review", "done", "failed")),
            name="ck_ingest_job_status",
        ),
    )
    op.create_index("ix_ingest_job_status_stage", "ingest_job", ["status", "stage"])

    op.create_table(
        "ingest_stage_result",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("job_id", UUID, sa.ForeignKey("ingest_job.id"), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("adapter", sa.String(100), nullable=False),
        sa.Column("adapter_version", sa.String(50), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_ingest_stage_result_job_id", "ingest_stage_result", ["job_id"])

    op.create_table(
        "purchase_line",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "purchase_id", UUID, sa.ForeignKey("purchase.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text()),
        sa.Column("line_kind", sa.String(16), nullable=False, server_default="item"),
        sa.Column("product_id", UUID, sa.ForeignKey("product.id")),
        sa.Column("parent_line_id", UUID, sa.ForeignKey("purchase_line.id", ondelete="SET NULL")),
        sa.Column("qty", sa.Numeric()),
        sa.Column("unit", sa.String(16), sa.ForeignKey("unit.code")),
        sa.Column("unit_price", sa.Numeric(12, 4)),
        sa.Column("line_total", sa.Numeric(12, 4), nullable=False),
        sa.Column("resolution", sa.String(16), nullable=False, server_default="unmatched"),
        sa.Column("resolved_by", UUID, sa.ForeignKey("app_user.id")),
        sa.Column("resolution_confidence", sa.Numeric(5, 4)),
        sa.Column("flags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        *_ts(),
        sa.CheckConstraint(
            _in("line_kind", ("item", "discount", "tax", "deposit", "fee")),
            name="ck_purchase_line_kind",
        ),
        sa.CheckConstraint(
            _in(
                "resolution", ("barcode", "alias", "fuzzy", "llm", "manual", "unmatched", "ignored")
            ),
            name="ck_purchase_line_resolution",
        ),
        sa.CheckConstraint(
            "line_kind = 'item' OR product_id IS NULL", name="ck_purchase_line_product"
        ),
        sa.UniqueConstraint("purchase_id", "seq", name="uq_purchase_line_seq"),
    )
    op.create_index("ix_purchase_line_product_id", "purchase_line", ["product_id"])

    op.create_table(
        "receipt_alias",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("vendor_id", UUID, sa.ForeignKey("vendor.id"), nullable=False),
        sa.Column("raw_text_norm", sa.Text(), nullable=False),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("product_id", UUID, sa.ForeignKey("product.id")),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        *_ts(),
        sa.CheckConstraint(
            "disposition IN ('product', 'ignore')", name="ck_receipt_alias_disposition"
        ),
        sa.CheckConstraint(
            "(disposition = 'product') = (product_id IS NOT NULL)", name="ck_receipt_alias_product"
        ),
        sa.UniqueConstraint("vendor_id", "raw_text_norm", name="uq_receipt_alias_vendor_text"),
    )
    op.execute(
        "CREATE INDEX ix_receipt_alias_text_trgm ON receipt_alias "
        "USING gin (raw_text_norm gin_trgm_ops)"
    )

    op.create_table(
        "price_observation",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("product_id", UUID, sa.ForeignKey("product.id"), nullable=False),
        sa.Column("vendor_location_id", UUID, sa.ForeignKey("vendor_location.id"), nullable=False),
        sa.Column("purchase_line_id", UUID, sa.ForeignKey("purchase_line.id")),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(12, 4), nullable=False),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(16), sa.ForeignKey("unit.code"), nullable=False),
        sa.Column("is_promo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("entered_by", UUID, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            _in("source", ("receipt", "manual", "shelf", "import")),
            name="ck_price_observation_source",
        ),
        sa.CheckConstraint("price >= 0", name="ck_price_observation_price"),
        sa.CheckConstraint("qty > 0", name="ck_price_observation_qty"),
    )
    op.create_index("ix_price_observation_product_id", "price_observation", ["product_id"])
    op.create_index(
        "ix_price_observation_vendor_location_id", "price_observation", ["vendor_location_id"]
    )
    op.create_index(
        "ix_price_observation_purchase_line_id", "price_observation", ["purchase_line_id"]
    )
    op.create_index(
        "ix_price_observation_product_time", "price_observation", ["product_id", "observed_at"]
    )

    op.create_table(
        "price_observation_void",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "observation_id",
            UUID,
            sa.ForeignKey("price_observation.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("voided_by", UUID, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "voided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "price_norm",
        sa.Column(
            "observation_id",
            UUID,
            sa.ForeignKey("price_observation.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("canonical_qty", sa.Numeric()),
        sa.Column("norm_unit", sa.String(16), sa.ForeignKey("unit.code")),
        sa.Column("norm_unit_price", sa.Numeric(14, 6)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("bridge_kind", sa.String(20), nullable=False, server_default="none"),
        sa.Column("bridge_source", sa.String(16)),
        sa.Column("bridge_confirmed", sa.Boolean()),
        sa.Column("convert_version", sa.String(16), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            _in("status", ("ok", "no_density", "unknown_measure", "no_pack", "no_qty")),
            name="ck_price_norm_status",
        ),
        sa.CheckConstraint(
            _in("bridge_kind", ("none", "density", "density_override", "measure", "pack")),
            name="ck_price_norm_bridge_kind",
        ),
    )

    # Append-only enforcement: privileges first, trigger second.
    for table in APPEND_ONLY:
        op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM {APP_ROLE}")
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION kerp_reject_modification()"
        )
    op.execute(ONE_LIVE_OBSERVATION_PER_LINE)
    op.execute(
        "CREATE TRIGGER trg_price_observation_one_live BEFORE INSERT ON price_observation "
        "FOR EACH ROW EXECUTE FUNCTION kerp_one_live_observation_per_line()"
    )

    # Derived table: the runtime role may empty it wholesale before a rebuild.
    op.execute(f"GRANT TRUNCATE ON price_norm TO {APP_ROLE}")

    for ddl in VIEWS.values():
        op.execute(ddl)
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")


def downgrade() -> None:
    for name in reversed(list(VIEWS)):
        op.execute(f"DROP VIEW IF EXISTS {name}")
    op.execute("DROP TRIGGER IF EXISTS trg_price_observation_one_live ON price_observation")
    op.execute("DROP FUNCTION IF EXISTS kerp_one_live_observation_per_line()")
    for table in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.drop_table("price_norm")
    op.drop_table("price_observation_void")
    op.drop_table("price_observation")
    op.drop_table("receipt_alias")
    op.drop_table("purchase_line")
    op.drop_table("ingest_stage_result")
    op.drop_constraint("fk_ingest_job_purchase", "ingest_job", type_="foreignkey")
    op.drop_table("ingest_job")
    op.drop_table("purchase")
    op.drop_table("receipt_document")
