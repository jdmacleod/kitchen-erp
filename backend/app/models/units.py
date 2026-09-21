from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UnitRow(Base):
    __tablename__ = "unit"
    __table_args__ = (
        CheckConstraint("dimension IN ('mass', 'volume', 'count')", name="ck_unit_dimension"),
        CheckConstraint("system IN ('us', 'metric', 'any')", name="ck_unit_system"),
    )

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    dimension: Mapped[str] = mapped_column(String(8), nullable=False)
    # Unconstrained numeric: several factors carry eleven decimals.
    to_base_factor: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    system: Mapped[str] = mapped_column(String(8), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
