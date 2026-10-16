"""Tidy the catalog's slow guesses, kept between looks (issue 372)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CleanupGuess(Base):
    """What a USDA search guessed for an ingredient name, as of one catalog version.

    ``kind`` is ``category`` (the key of the best fitting USDA food's group) or
    ``usda_link`` (the first USDA food that fits, to relink to). Either value may be
    null: nothing was found. ``version`` names the loaded USDA release and the
    guessing rules; a row of another version is guessed again. Derived and
    disposable: emptying the table only makes the next look slower.
    """

    __tablename__ = "cleanup_guess"
    __table_args__ = (
        CheckConstraint("kind IN ('category', 'usda_link')", name="ck_cleanup_guess_kind"),
    )

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str | None] = mapped_column(String(20))
    fdc_id: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
