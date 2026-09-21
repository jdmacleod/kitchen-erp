"""Pure unit conversion library. No database, no network, no filesystem.

Callers load the unit table and the ingredient's bridges and pass them in.
"""

from app.units.convert import (
    BridgeKind,
    CanonicalQty,
    ConversionContext,
    ConversionFailure,
    FailureCode,
    Measure,
    Pack,
    ProductContext,
    Provenance,
    convert,
    convert_between,
)
from app.units.parse import UnitParseFailure, normalize_unit_text, parse_unit
from app.units.table import BASE_UNIT, SEED_UNITS, Dimension, Unit, units_by_code

__all__ = [
    "BASE_UNIT",
    "SEED_UNITS",
    "BridgeKind",
    "CanonicalQty",
    "ConversionContext",
    "ConversionFailure",
    "Dimension",
    "FailureCode",
    "Measure",
    "Pack",
    "ProductContext",
    "Provenance",
    "Unit",
    "UnitParseFailure",
    "convert",
    "convert_between",
    "normalize_unit_text",
    "parse_unit",
    "units_by_code",
]
