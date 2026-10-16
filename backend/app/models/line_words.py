"""Learned expansions of the words vendors print on receipt lines (#388)."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped, UUIDPrimaryKey

LINE_WORD_SOURCES = ("learned", "person")


class LineWord(UUIDPrimaryKey, Timestamped, Base):
    """One printed word and the word it stands for, at one vendor or at any.

    ``vendor_id`` is null on the any-vendor row, the fallback for a vendor that
    hasn't printed the word yet. ``confirmed_count`` counts the namings that agree;
    at zero (undone by a person) the expansion is no longer offered. The key
    ``(vendor_id, word, expansion)`` is a unique index with ``NULLS NOT DISTINCT``
    (migration 0058). A suggestion only: the printed text is never rewritten.
    """

    __tablename__ = "line_word"
    __table_args__ = (
        CheckConstraint("source IN ('learned', 'person')", name="ck_line_word_source"),
        CheckConstraint("confirmed_count >= 0", name="ck_line_word_count"),
    )

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor.id", ondelete="CASCADE")
    )
    word: Mapped[str] = mapped_column(Text, nullable=False)
    expansion: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="learned")
