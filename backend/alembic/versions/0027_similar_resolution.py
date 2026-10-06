"""Receipt lines resolved by accepting a similar name (04, 2D): ``resolution = similar``.

When no model answers, the ladder offers the product shortlist's best hits as
similar names. A person accepting one records ``similar``, as accepting a fuzzy
alias records ``fuzzy``.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-06
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

OLD = ("barcode", "identifier", "alias", "fuzzy", "llm", "manual", "unmatched", "ignored")
NEW = (*OLD[:5], "similar", *OLD[5:])


def _in(values: tuple[str, ...]) -> str:
    return f"resolution IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint("ck_purchase_line_resolution", "purchase_line", type_="check")
    op.create_check_constraint("ck_purchase_line_resolution", "purchase_line", _in(NEW))


def downgrade() -> None:
    # A person chose the product either way; before this rung it read as chosen.
    op.execute("UPDATE purchase_line SET resolution = 'manual' WHERE resolution = 'similar'")
    op.drop_constraint("ck_purchase_line_resolution", "purchase_line", type_="check")
    op.create_check_constraint("ck_purchase_line_resolution", "purchase_line", _in(OLD))
