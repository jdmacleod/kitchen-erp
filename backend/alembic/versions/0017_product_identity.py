"""Product identity, kinds and listings (03, 1H).

Adds ``product_identifier`` (every code a product is known by) and
``vendor_listing`` (a vendor's page for a product), a product's kind,
attributes and field sources, and the vendor facts products need. Existing
``product.barcode`` values move into identifiers, each keeping its original
string as ``legacy_value``, and the column is dropped; the downgrade restores it
byte for byte.

The classifier below is a frozen copy: it must not import application code, so
that this migration means the same thing forever. ``kerp migrate
--check-barcodes`` loads this file and calls ``plan`` for its dry run.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

SCHEMES = ("gtin", "plu", "vendor_sku", "rw_item", "other")
SOURCES = ("barcode_scan", "listing", "manufacturer", "manual", "receipt", "migrated_barcode")
KINDS = ("branded", "private_label", "random_weight", "loose", "unbranded_vendor")


# --- frozen classifier -------------------------------------------------------


def _check(body: str) -> int:
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def _ok(code: str) -> bool:
    return code.isdigit() and len(code) >= 2 and _check(code[:-1]) == int(code[-1])


def _upce(code: str) -> str | None:
    if len(code) != 8 or not code.isdigit() or code[0] not in "01":
        return None
    ns, d, check = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = f"{ns}{d[0]}{d[1]}{last}0000{d[2]}{d[3]}{d[4]}"
    elif last == "3":
        body = f"{ns}{d[0]}{d[1]}{d[2]}00000{d[3]}{d[4]}"
    elif last == "4":
        body = f"{ns}{d[0]}{d[1]}{d[2]}{d[3]}00000{d[4]}"
    else:
        body = f"{ns}{d[0]}{d[1]}{d[2]}{d[3]}{d[4]}0000{last}"
    return body + check


def classify(raw: str, has_exclusive_vendor: bool) -> tuple[str, str, str]:
    """(outcome, scheme, value) for one legacy barcode."""
    code = raw.strip()
    if code.isdigit() and len(code) == 8:
        upca = _upce(code)
        as_ean8, as_upce = _ok(code), upca is not None and _ok(upca)
        if as_ean8 and as_upce and code.zfill(14) != upca.zfill(14):
            return "ambiguous_8_digit", "other", raw
        if as_ean8:
            return "gtin", "gtin", code.zfill(14)
        if as_upce:
            return "gtin", "gtin", upca.zfill(14)
        return "other", "other", raw
    if code.isdigit() and len(code) in (12, 13, 14) and _ok(code):
        return "gtin", "gtin", code.zfill(14)
    if code.isdigit() and len(code) in (4, 5):
        if has_exclusive_vendor:
            return "plu", "plu", code
        return "plu_without_vendor", "other", raw
    return "other", "other", raw


def plan(rows: Iterable[tuple[Any, str, Any]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Identifier rows for (product_id, barcode, exclusive_vendor_id) rows, and counts.

    A GTIN two products normalize to (the same code typed in two forms) goes to
    the first only; the second keeps its value as ``other`` and is counted.
    """
    out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seen: set[tuple[str, str, Any]] = set()
    for product_id, raw, vendor_id in rows:
        outcome, scheme, value = classify(raw, vendor_id is not None)
        scoped_vendor = vendor_id if scheme == "plu" else None
        key = (scheme, value, scoped_vendor)
        if key in seen:
            outcome, scheme, value, scoped_vendor = "duplicate_gtin", "other", raw, None
            key = (scheme, value, None)
        seen.add(key)
        counts[outcome] += 1
        out.append(
            {
                "product_id": product_id,
                "scheme": scheme,
                "value": value,
                "vendor_id": scoped_vendor,
                "legacy_value": raw,
            }
        )
    return out, counts


# --- schema -------------------------------------------------------------------


def _in(column: str, values: Iterable[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "product_identifier",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("scheme", sa.String(16), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("vendor.id"), nullable=True),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("legacy_value", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("scheme", SCHEMES), name="ck_product_identifier_scheme"),
        sa.CheckConstraint(_in("source", SOURCES), name="ck_product_identifier_source"),
        sa.CheckConstraint(
            "(scheme IN ('plu', 'vendor_sku', 'rw_item')) = (vendor_id IS NOT NULL)",
            name="ck_product_identifier_vendor",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_product_identifier_value ON product_identifier "
        "(scheme, value, vendor_id) NULLS NOT DISTINCT"
    )

    op.create_table(
        "vendor_listing",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("vendor.id"), nullable=False),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("vendor_sku", sa.String(64), nullable=True),
        sa.Column("store_ref", sa.String(64), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("last_captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("status", ("active", "gone", "ignored")), name="ck_listing_status"),
        sa.UniqueConstraint("vendor_id", "canonical_url", name="uq_listing_vendor_url"),
    )

    op.add_column("product", sa.Column("kind", sa.String(24), nullable=True))
    op.add_column(
        "product",
        sa.Column("attributes", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "product",
        sa.Column("field_source", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("vendor", sa.Column("platform", sa.String(64), nullable=True))
    op.add_column(
        "vendor",
        sa.Column("fetch_policy", sa.String(16), nullable=False, server_default="capture_only"),
    )
    op.add_column("vendor", sa.Column("rw_layout", JSONB(), nullable=True))
    op.add_column("vendor", sa.Column("code_position", JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_vendor_fetch_policy",
        "vendor",
        _in("fetch_policy", ("server_fetch", "capture_only", "none")),
    )
    op.add_column("vendor_location", sa.Column("platform_store_ref", sa.String(64), nullable=True))
    op.create_index(
        "uq_location_platform_store",
        "vendor_location",
        ["vendor_id", "platform_store_ref"],
        unique=True,
        postgresql_where=sa.text("platform_store_ref IS NOT NULL"),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, barcode, exclusive_vendor_id FROM product "
            "WHERE barcode IS NOT NULL ORDER BY created_at, id"
        )
    ).all()
    identifiers, _ = plan((r[0], r[1], r[2]) for r in rows)
    for row in identifiers:
        bind.execute(
            sa.text(
                "INSERT INTO product_identifier "
                "(id, product_id, scheme, value, vendor_id, source, legacy_value) "
                "VALUES (gen_random_uuid(), :product_id, :scheme, :value, :vendor_id, "
                "'migrated_barcode', :legacy_value)"
            ),
            row,
        )

    # Kinds from evidence; the kind stays editable.
    op.execute(
        "UPDATE product SET kind = CASE "
        "WHEN exclusive_vendor_id IS NOT NULL THEN 'unbranded_vendor' "
        "WHEN coalesce(btrim(brand), '') <> '' OR barcode IS NOT NULL THEN 'branded' "
        "ELSE 'loose' END"
    )
    op.alter_column("product", "kind", nullable=False)
    op.create_check_constraint("ck_product_kind", "product", _in("kind", KINDS))

    op.drop_index("uq_product_barcode", table_name="product")
    op.drop_column("product", "barcode")


def downgrade() -> None:
    op.add_column("product", sa.Column("barcode", sa.String(32), nullable=True))
    # Migrated codes come back exactly as they were; codes added since, as the
    # API showed them (a GTIN at its shortest form).
    op.execute(
        "UPDATE product p SET barcode = i.legacy_value FROM product_identifier i "
        "WHERE i.product_id = p.id AND i.source = 'migrated_barcode'"
    )
    op.execute(
        """
        UPDATE product p SET barcode = CASE
            WHEN i.scheme = 'gtin' AND i.value LIKE '000000%' THEN substr(i.value, 7)
            WHEN i.scheme = 'gtin' AND i.value LIKE '00%' THEN substr(i.value, 3)
            WHEN i.scheme = 'gtin' AND i.value LIKE '0%' THEN substr(i.value, 2)
            ELSE i.value END
        FROM (
            SELECT DISTINCT ON (product_id) product_id, scheme, value
            FROM product_identifier
            WHERE scheme IN ('gtin', 'other')
            ORDER BY product_id, created_at, id
        ) i
        WHERE i.product_id = p.id AND p.barcode IS NULL
        """
    )
    op.create_index(
        "uq_product_barcode",
        "product",
        ["barcode"],
        unique=True,
        postgresql_where=sa.text("barcode IS NOT NULL"),
    )
    op.drop_constraint("ck_product_kind", "product", type_="check")
    op.drop_index("uq_location_platform_store", table_name="vendor_location")
    op.drop_column("vendor_location", "platform_store_ref")
    op.drop_constraint("ck_vendor_fetch_policy", "vendor", type_="check")
    for column in ("code_position", "rw_layout", "fetch_policy", "platform"):
        op.drop_column("vendor", column)
    for column in ("field_source", "attributes", "kind"):
        op.drop_column("product", column)
    op.drop_table("vendor_listing")
    op.drop_table("product_identifier")
