"""Bunches: a unit of their own (issue 371).

Produce is often sold by the bunch. ``bunch`` is a unit in a dimension of its own,
so a bunch never equals a piece: it reaches a weight only through the ingredient's
"bunch" measure, set by a person ("Weigh one bunch" on Needs a bridge). Until then
a price normalizes per bunch with the new status ``no_bunch_weight``, and compares
only with other per-bunch prices.

The unit row is inserted here so a deployment has it before ``kerp seed units``
runs again (the seed upserts the same row). No grants change: the runtime role
already reads ``unit`` and writes ``price_norm``.

Downgrade deletes the derived per-bunch price rows (``kerp recompute-norms``
rebuilds them, non-negotiable 5) and the unit. It fails on the foreign keys while
any product, price, line or recipe still uses ``bunch``: change those first.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

_DIMENSIONS = "'mass', 'volume', 'count'"
_STATUSES = "'ok', 'no_density', 'unknown_measure', 'no_pack', 'no_qty'"


def upgrade() -> None:
    op.drop_constraint("ck_unit_dimension", "unit", type_="check")
    op.create_check_constraint(
        "ck_unit_dimension", "unit", f"dimension IN ({_DIMENSIONS}, 'bunch')"
    )
    op.execute(
        "INSERT INTO unit (code, dimension, to_base_factor, system, aliases) "
        "VALUES ('bunch', 'bunch', 1, 'any', ARRAY['bunches', 'bnch']) "
        "ON CONFLICT (code) DO NOTHING"
    )
    op.drop_constraint("ck_price_norm_status", "price_norm", type_="check")
    op.create_check_constraint(
        "ck_price_norm_status", "price_norm", f"status IN ({_STATUSES}, 'no_bunch_weight')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM price_norm WHERE status = 'no_bunch_weight' OR norm_unit = 'bunch'")
    op.drop_constraint("ck_price_norm_status", "price_norm", type_="check")
    op.create_check_constraint("ck_price_norm_status", "price_norm", f"status IN ({_STATUSES})")
    op.execute("DELETE FROM unit WHERE code = 'bunch'")
    op.drop_constraint("ck_unit_dimension", "unit", type_="check")
    op.create_check_constraint("ck_unit_dimension", "unit", f"dimension IN ({_DIMENSIONS})")
