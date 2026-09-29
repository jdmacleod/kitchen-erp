"""Stable keys for the vendor file format, brand facts, and sharing (1F).

``vendor.slug`` and ``vendor_location.key`` ("<vendor slug>/<location slug>")
are the keys a ``kitchen-erp-vendors/1`` file carries. They are backfilled from
names here, suffixed ("-2", "-3") where two would collide, and never change on a
rename. ``brand`` and ``wikidata`` hold the facts OpenStreetMap and outside
sources publish about a chain. ``vendor_location.publishable`` marks a location
that may appear in a public export.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29
"""

from __future__ import annotations

import re
import unicodedata

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _slug(text: str, fallback: str) -> str:
    # A frozen copy of app.services.keys.slugify: migrations do not import app code.
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:80].strip("-")
    return slug or fallback


def _unique(base: str, taken: set[str]) -> str:
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    taken.add(candidate)
    return candidate


def upgrade() -> None:
    op.add_column("vendor", sa.Column("slug", sa.String(120), nullable=True))
    op.add_column("vendor", sa.Column("brand", sa.String(200), nullable=True))
    op.add_column("vendor", sa.Column("wikidata", sa.String(20), nullable=True))
    op.add_column("vendor_location", sa.Column("key", sa.String(250), nullable=True))
    op.add_column(
        "vendor_location",
        sa.Column("publishable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    conn = op.get_bind()
    slugs: dict[str, str] = {}
    taken: set[str] = set()
    for vendor_id, name in conn.execute(
        sa.text("SELECT id, name FROM vendor ORDER BY created_at, id")
    ):
        slugs[str(vendor_id)] = slug = _unique(_slug(name, "vendor"), taken)
        conn.execute(
            sa.text("UPDATE vendor SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": vendor_id},
        )
    keys: set[str] = set()
    for location_id, vendor_id, name in conn.execute(
        sa.text("SELECT id, vendor_id, name FROM vendor_location ORDER BY created_at, id")
    ):
        key = _unique(f"{slugs[str(vendor_id)]}/{_slug(name, 'location')}", keys)
        conn.execute(
            sa.text("UPDATE vendor_location SET key = :key WHERE id = :id"),
            {"key": key, "id": location_id},
        )

    op.alter_column("vendor", "slug", nullable=False)
    op.alter_column("vendor_location", "key", nullable=False)
    op.create_unique_constraint("uq_vendor_slug", "vendor", ["slug"])
    op.create_unique_constraint("uq_location_key", "vendor_location", ["key"])
    op.create_check_constraint(
        "ck_vendor_wikidata", "vendor", "wikidata IS NULL OR wikidata ~ '^Q[0-9]+$'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_vendor_wikidata", "vendor", type_="check")
    op.drop_constraint("uq_location_key", "vendor_location", type_="unique")
    op.drop_constraint("uq_vendor_slug", "vendor", type_="unique")
    op.drop_column("vendor_location", "publishable")
    op.drop_column("vendor_location", "key")
    op.drop_column("vendor", "wikidata")
    op.drop_column("vendor", "brand")
    op.drop_column("vendor", "slug")
