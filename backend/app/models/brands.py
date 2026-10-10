"""Store brands and the families of stores that sell them (spec 16, 2R).

Imported from the kitchen-erp-brands dataset (``kerp import brands``). A family
is a group of stores that sells the same house brands: a retailer with banners,
or a wholesaler or cooperative whose labels many stores carry. These rows are
reference data, never facts about a purchase; a family or brand missing from a
later file is reported and kept, because products point at brands.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamped, UUIDPrimaryKey

FAMILY_KINDS = ("retailer", "wholesaler", "cooperative")
TIERS = ("value", "standard", "premium", "organic", "natural", "prepared", "specialty")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class BrandFamily(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "brand_family"
    __table_args__ = (
        UniqueConstraint("key", name="uq_brand_family_key"),
        CheckConstraint(_in("kind", FAMILY_KINDS), name="ck_brand_family_kind"),
        CheckConstraint(
            "wikidata IS NULL OR wikidata ~ '^Q[0-9]+$'", name="ck_brand_family_wikidata"
        ),
    )

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    wikidata: Mapped[str | None] = mapped_column(String(20))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    owner_wikidata: Mapped[str | None] = mapped_column(String(20))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # {field: {source, ref, checked_at, imported}}, as on vendor (1F).
    field_source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    banners: Mapped[list[BrandFamilyBanner]] = relationship(
        back_populates="family", cascade="all, delete-orphan", lazy="selectin"
    )
    brands: Mapped[list[Brand]] = relationship(back_populates="family", lazy="selectin")


class BrandFamilyBanner(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "brand_family_banner"
    __table_args__ = (
        UniqueConstraint("key", name="uq_brand_family_banner_key"),
        CheckConstraint(
            "wikidata IS NULL OR wikidata ~ '^Q[0-9]+$'", name="ck_brand_family_banner_wikidata"
        ),
    )

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brand_family.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    wikidata: Mapped[str | None] = mapped_column(String(20), index=True)
    domains: Mapped[list[str]] = mapped_column(ARRAY(String(253)), nullable=False, default=list)

    family: Mapped[BrandFamily] = relationship(back_populates="banners")


class Brand(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "brand"
    __table_args__ = (
        UniqueConstraint("key", name="uq_brand_key"),
        CheckConstraint(_in("tier", TIERS), name="ck_brand_tier"),
        CheckConstraint(
            "replaced_by_id IS NULL OR replaced_by_id <> id", name="ck_brand_replaced_self"
        ),
    )

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brand_family.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)
    # Partial dates as the dataset writes them: 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'.
    since: Mapped[str | None] = mapped_column(String(10))
    until: Mapped[str | None] = mapped_column(String(10))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brand.id", ondelete="SET NULL")
    )
    field_source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    family: Mapped[BrandFamily] = relationship(back_populates="brands", lazy="joined")
    aliases: Mapped[list[BrandAlias]] = relationship(
        back_populates="brand", cascade="all, delete-orphan", lazy="selectin"
    )

    # Joined, one level: a retired brand's response names its replacement.
    replaced_by: Mapped[Brand | None] = relationship(
        remote_side="Brand.id", lazy="joined", join_depth=1, viewonly=True
    )

    @property
    def current(self) -> bool:
        return self.until is None

    @property
    def replaced_by_name(self) -> str | None:
        return self.replaced_by.name if self.replaced_by else None


class BrandAlias(UUIDPrimaryKey, Base):
    """Every spelling that names a brand, its own name included, by name key."""

    __tablename__ = "brand_alias"
    __table_args__ = (UniqueConstraint("alias_key", name="uq_brand_alias_key"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brand.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_key: Mapped[str] = mapped_column(String(200), nullable=False)

    brand: Mapped[Brand] = relationship(back_populates="aliases")


class BrandFamilyCarries(Base):
    """A wholesale or cooperative label a retailer family sells: never out of family there."""

    __tablename__ = "brand_family_carries"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brand_family.id", ondelete="CASCADE"), primary_key=True
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brand.id", ondelete="CASCADE"), primary_key=True
    )
