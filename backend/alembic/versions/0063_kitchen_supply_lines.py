"""Kitchen supply lines (04, 2T; issue 262).

A receipt line can be marked a kitchen supply instead of ignored. It keeps its
amount, takes one of a fixed list of supply categories, and emits no price. The
choice is learned like an ignore: the receipt alias takes ``disposition =
'supply'`` with the same category, so the vendor's same wording resolves without
a person next time.

``purchase_line`` gains ``supply_category`` and the ``supply`` resolution;
``receipt_alias`` gains ``supply_category`` and the ``supply`` disposition. A
category is set exactly when the line or alias is a supply. No privileges
change: both tables are ordinary, and new columns take the table's grants.

Downgrade turns supply lines back to ``unmatched`` (they rejoin the to-identify
queue) and supply aliases into ignore aliases, so the vendor's wording is still
not asked about, then drops the columns and restores the constraints.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

_KEYS = "('wraps', 'storage', 'cooking', 'filters', 'fuel')"
_RESOLUTIONS = (
    "'barcode', 'identifier', 'alias', 'fuzzy', 'llm', 'similar', 'manual', 'unmatched', 'ignored'"
)


def upgrade() -> None:
    op.add_column("purchase_line", sa.Column("supply_category", sa.String(16)))
    op.drop_constraint("ck_purchase_line_resolution", "purchase_line", type_="check")
    op.create_check_constraint(
        "ck_purchase_line_resolution",
        "purchase_line",
        f"resolution IN ({_RESOLUTIONS}, 'supply')",
    )
    op.create_check_constraint(
        "ck_purchase_line_supply_category", "purchase_line", f"supply_category IN {_KEYS}"
    )
    op.create_check_constraint(
        "ck_purchase_line_supply",
        "purchase_line",
        "(resolution = 'supply') = (supply_category IS NOT NULL) "
        "AND (resolution <> 'supply' OR product_id IS NULL)",
    )

    op.add_column("receipt_alias", sa.Column("supply_category", sa.String(16)))
    op.drop_constraint("ck_receipt_alias_disposition", "receipt_alias", type_="check")
    op.create_check_constraint(
        "ck_receipt_alias_disposition",
        "receipt_alias",
        "disposition IN ('product', 'ignore', 'supply')",
    )
    op.create_check_constraint(
        "ck_receipt_alias_supply",
        "receipt_alias",
        "(disposition = 'supply') = (supply_category IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_receipt_alias_supply_category", "receipt_alias", f"supply_category IN {_KEYS}"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE receipt_alias SET disposition = 'ignore', supply_category = NULL "
        "WHERE disposition = 'supply'"
    )
    op.drop_constraint("ck_receipt_alias_supply_category", "receipt_alias", type_="check")
    op.drop_constraint("ck_receipt_alias_supply", "receipt_alias", type_="check")
    op.drop_constraint("ck_receipt_alias_disposition", "receipt_alias", type_="check")
    op.create_check_constraint(
        "ck_receipt_alias_disposition", "receipt_alias", "disposition IN ('product', 'ignore')"
    )
    op.drop_column("receipt_alias", "supply_category")

    op.execute(
        "UPDATE purchase_line "
        "SET resolution = 'unmatched', supply_category = NULL, resolved_by = NULL "
        "WHERE resolution = 'supply'"
    )
    op.drop_constraint("ck_purchase_line_supply", "purchase_line", type_="check")
    op.drop_constraint("ck_purchase_line_supply_category", "purchase_line", type_="check")
    op.drop_constraint("ck_purchase_line_resolution", "purchase_line", type_="check")
    op.create_check_constraint(
        "ck_purchase_line_resolution", "purchase_line", f"resolution IN ({_RESOLUTIONS})"
    )
    op.drop_column("purchase_line", "supply_category")
