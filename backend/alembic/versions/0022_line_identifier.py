"""Receipt lines resolved by an identifier (04, 2K): ``resolution = identifier``.

The barcode rung became the identifier rung. New matches by a GTIN, a weighed-
item label or a code where the vendor prints them record ``identifier``; lines
resolved before keep ``barcode``.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-02
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

OLD = ("barcode", "alias", "fuzzy", "llm", "manual", "unmatched", "ignored")
NEW = ("barcode", "identifier", "alias", "fuzzy", "llm", "manual", "unmatched", "ignored")


def _in(values: tuple[str, ...]) -> str:
    return f"resolution IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint("ck_purchase_line_resolution", "purchase_line", type_="check")
    op.create_check_constraint("ck_purchase_line_resolution", "purchase_line", _in(NEW))


def downgrade() -> None:
    # A line matched by a code reads as the barcode rung it grew out of.
    op.execute("UPDATE purchase_line SET resolution = 'barcode' WHERE resolution = 'identifier'")
    op.drop_constraint("ck_purchase_line_resolution", "purchase_line", type_="check")
    op.create_check_constraint("ck_purchase_line_resolution", "purchase_line", _in(OLD))
