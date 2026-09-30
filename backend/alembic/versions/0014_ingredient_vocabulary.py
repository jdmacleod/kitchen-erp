"""Ingredient vocabulary (1G): slugs, reconcile state, spellings, and references.

``ingredient.slug`` is the standard-list key once an ingredient is created from
or linked to the standard list. Until then it is ``local.<name>``, backfilled
here from names and suffixed ("-2", "-3") where two would collide; the dot
keeps a generated slug from ever equalling a standard key.

``reconcile_state`` records where an ingredient stands against the standard
list. Ingredients that exist now start ``unreviewed``, so the link page offers
each of them once; ingredients created later default to ``not_applicable``.
``usda_reviewed_fdc_id`` is the USDA food whose suggestions a person last
reviewed, and ``merged_into`` points a merged-away ingredient at its survivor.

``ingredient_alias`` holds other spellings only, never the canonical name.
``ingredient_ref`` holds external identifiers, one preferred per system.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-30
"""

from __future__ import annotations

import re
import unicodedata

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _slug(text: str, fallback: str) -> str:
    # A frozen copy of app.models.keys.slugify: migrations do not import app code.
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


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.add_column("ingredient", sa.Column("slug", sa.String(120), nullable=True))
    op.add_column(
        "ingredient",
        sa.Column(
            "reconcile_state",
            sa.String(16),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column("ingredient", sa.Column("usda_reviewed_fdc_id", sa.Integer(), nullable=True))
    op.add_column(
        "ingredient",
        sa.Column("merged_into", UUID(as_uuid=True), sa.ForeignKey("ingredient.id"), nullable=True),
    )

    conn = op.get_bind()
    taken: set[str] = set()
    for ingredient_id, name in conn.execute(
        sa.text("SELECT id, name FROM ingredient ORDER BY created_at, id")
    ):
        conn.execute(
            sa.text(
                "UPDATE ingredient SET slug = :slug, reconcile_state = 'unreviewed' WHERE id = :id"
            ),
            {"slug": _unique("local." + _slug(name, "ingredient"), taken), "id": ingredient_id},
        )

    op.alter_column("ingredient", "slug", nullable=False)
    op.create_unique_constraint("uq_ingredient_slug", "ingredient", ["slug"])
    op.create_check_constraint(
        "ck_ingredient_reconcile_state",
        "ingredient",
        "reconcile_state IN ('unreviewed', 'linked', 'skipped', 'not_applicable')",
    )
    op.create_index("ix_ingredient_reconcile_state", "ingredient", ["reconcile_state"])

    op.create_table(
        "ingredient_alias",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.Column(
            "ingredient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("name_norm", name="uq_ingredient_alias_name_norm"),
        sa.CheckConstraint(
            "kind IN ('synonym', 'inflection', 'legacy')", name="ck_ingredient_alias_kind"
        ),
        sa.CheckConstraint(
            "source IN ('standard', 'generated', 'rename', 'merge', 'manual')",
            name="ck_ingredient_alias_source",
        ),
    )
    op.create_index("ix_ingredient_alias_ingredient_id", "ingredient_alias", ["ingredient_id"])
    op.create_index(
        "ix_ingredient_alias_name_norm_trgm",
        "ingredient_alias",
        ["name_norm"],
        postgresql_using="gin",
        postgresql_ops={"name_norm": "gin_trgm_ops"},
    )

    op.create_table(
        "ingredient_ref",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingredient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("system", sa.String(16), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("is_preferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.CheckConstraint("system IN ('fdc')", name="ck_ingredient_ref_system"),
        sa.CheckConstraint(
            "system <> 'fdc' OR external_id ~ '^[0-9]+$'", name="ck_ingredient_ref_fdc_digits"
        ),
        sa.UniqueConstraint(
            "system", "external_id", "ingredient_id", name="uq_ingredient_ref_system_external"
        ),
    )
    op.create_index("ix_ingredient_ref_ingredient_id", "ingredient_ref", ["ingredient_id"])
    op.create_index(
        "uq_ingredient_ref_one_preferred",
        "ingredient_ref",
        ["ingredient_id", "system"],
        unique=True,
        postgresql_where=sa.text("is_preferred"),
    )


def downgrade() -> None:
    op.drop_table("ingredient_ref")
    op.drop_table("ingredient_alias")
    op.drop_index("ix_ingredient_reconcile_state", table_name="ingredient")
    op.drop_constraint("ck_ingredient_reconcile_state", "ingredient", type_="check")
    op.drop_constraint("uq_ingredient_slug", "ingredient", type_="unique")
    op.drop_column("ingredient", "merged_into")
    op.drop_column("ingredient", "usda_reviewed_fdc_id")
    op.drop_column("ingredient", "reconcile_state")
    op.drop_column("ingredient", "slug")
