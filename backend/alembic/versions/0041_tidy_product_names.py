"""Tidy existing product names and brands (2P).

From here on a product's name and brand are tidied as they are saved
(``app.catalog.product_names``): no trademark signs, plain quotes, dashes and
spaces, runs of spaces collapsed. This applies the same rules once to the
products saved before. The rules are copied here, so the migration keeps doing
what it did if the application's rules change later.

The original name and brand of every product it changes are kept in
``product_name_before_0041``, so downgrade restores exactly those (where the
product still has the tidied value) and drops the table.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-08
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

BEFORE = "product_name_before_0041"

_MARKS = [0x2122, 0x00AE, 0x00A9, 0x2120]
_INVISIBLE = [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF]
_SINGLE_QUOTES = [0x2018, 0x2019, 0x201A, 0x201B, 0x2032, 0x02BC]
_DOUBLE_QUOTES = [0x201C, 0x201D, 0x201E, 0x201F, 0x2033]
_DASHES = [0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212]
_SPACES_ODD = [0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000]
_WHITESPACE = [ord(c) for c in "\t\n\r\v\f"]
_TABLE: dict[int, str | None] = {
    **dict.fromkeys(_MARKS + _INVISIBLE),
    **dict.fromkeys(_SINGLE_QUOTES, "'"),
    **dict.fromkeys(_DOUBLE_QUOTES, '"'),
    **dict.fromkeys(_DASHES, "-"),
    **dict.fromkeys(_SPACES_ODD + _WHITESPACE, " "),
}
_SPACES = re.compile(r" {2,}")
_BEFORE_PUNCT = re.compile(r" +([,;:])")


def _tidy(text: str | None) -> str | None:
    if text is None:
        return None
    out = _SPACES.sub(" ", text.translate(_TABLE))
    return _BEFORE_PUNCT.sub(r"\1", out).strip()


def upgrade() -> None:
    op.create_table(
        BEFORE,
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text()),
        sa.Column("tidied_name", sa.Text(), nullable=False),
        sa.Column("tidied_brand", sa.Text()),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name, brand FROM product")).all()
    for row in rows:
        name = _tidy(row.name) or row.name
        brand = _tidy(row.brand) or None
        if (name, brand) == (row.name, row.brand):
            continue
        conn.execute(
            sa.text(
                f"INSERT INTO {BEFORE} (product_id, name, brand, tidied_name, tidied_brand) "
                "VALUES (:id, :name, :brand, :tidied_name, :tidied_brand)"
            ),
            {
                "id": row.id,
                "name": row.name,
                "brand": row.brand,
                "tidied_name": name,
                "tidied_brand": brand,
            },
        )
        conn.execute(
            sa.text("UPDATE product SET name = :name, brand = :brand WHERE id = :id"),
            {"id": row.id, "name": name, "brand": brand},
        )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE product p SET name = b.name, brand = b.brand
        FROM {BEFORE} b
        WHERE p.id = b.product_id
          AND p.name = b.tidied_name
          AND p.brand IS NOT DISTINCT FROM b.tidied_brand
        """
    )
    op.drop_table(BEFORE)
