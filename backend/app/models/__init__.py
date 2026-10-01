from app.models.base import Base
from app.models.catalog import (
    FdcFood,
    FdcRelease,
    Ingredient,
    IngredientAlias,
    IngredientMeasure,
    IngredientRef,
    Product,
    ProductIdentifier,
    RefUsdaPortion,
    VendorListing,
)
from app.models.identity import ApiToken, AppUser, IdempotencyKey, Session
from app.models.purchases import (
    IngestJob,
    IngestStageResult,
    NamingSuggestion,
    PriceNorm,
    PriceObservation,
    PriceObservationVoid,
    Purchase,
    PurchaseLine,
    ReceiptAlias,
    ReceiptDocument,
)
from app.models.units import UnitRow

# Geography models (Phase 1D) register with Base on import.
from app.models import geo as _geo  # noqa: F401  isort: skip

# Assigns vendor slugs and location keys on every flush that creates one (1F).
from app.models import keys as _keys  # noqa: F401  isort: skip

# Vendor suggestions (1F) register with Base on import.
from app.models import suggestions as _suggestions  # noqa: F401  isort: skip

__all__ = [
    "ApiToken",
    "AppUser",
    "Base",
    "FdcFood",
    "FdcRelease",
    "IdempotencyKey",
    "Ingredient",
    "IngredientAlias",
    "IngredientRef",
    "IngestJob",
    "IngestStageResult",
    "NamingSuggestion",
    "IngredientMeasure",
    "PriceNorm",
    "PriceObservation",
    "PriceObservationVoid",
    "Purchase",
    "PurchaseLine",
    "ReceiptAlias",
    "ReceiptDocument",
    "Product",
    "ProductIdentifier",
    "VendorListing",
    "RefUsdaPortion",
    "Session",
    "UnitRow",
]
