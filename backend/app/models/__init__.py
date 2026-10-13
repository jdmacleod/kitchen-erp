from app.models.base import Base
from app.models.brands import (
    Brand,
    BrandAlias,
    BrandFamily,
    BrandFamilyBanner,
    BrandFamilyCarries,
    Gs1Prefix,
    Gs1PrefixFamily,
)
from app.models.catalog import (
    FdcBranded,
    FdcFood,
    FdcRelease,
    Ingredient,
    IngredientAlias,
    IngredientMeasure,
    IngredientRef,
    Product,
    ProductDistinctPair,
    ProductIdentifier,
    RefUsdaPortion,
    VendorListing,
)
from app.models.identity import ApiToken, AppUser, IdempotencyKey, Session
from app.models.lookups import ListingPriceChange, LookupAnswer, LookupRequest
from app.models.photos import ProductImage, ProductJob, ProductStageResult
from app.models.plan import (
    ShoppingList,
    ShoppingListItem,
    ShoppingListItemQty,
    TravelLeg,
    TripPlan,
    TripPlanStop,
    TripPlanStopItem,
    TripPlanVariant,
    UserSetting,
)
from app.models.proposals import ProductCapture, ProductProposal
from app.models.purchases import (
    IngestJob,
    IngestStageResult,
    LineReread,
    NamingSuggestion,
    PriceNorm,
    PriceObservation,
    PriceObservationVoid,
    Purchase,
    PurchaseLine,
    ReceiptAlias,
    ReceiptDocument,
    UploadBatch,
    UploadBatchReceipt,
)
from app.models.recipes import (
    Recipe,
    RecipeCostLine,
    RecipeCostSnapshot,
    RecipeIngredient,
    RecipeNameIgnore,
    RecipePin,
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
    "Brand",
    "BrandAlias",
    "BrandFamily",
    "BrandFamilyBanner",
    "BrandFamilyCarries",
    "FdcBranded",
    "FdcFood",
    "FdcRelease",
    "Gs1Prefix",
    "Gs1PrefixFamily",
    "IdempotencyKey",
    "IngestJob",
    "IngestStageResult",
    "Ingredient",
    "IngredientAlias",
    "IngredientMeasure",
    "IngredientRef",
    "LineReread",
    "ListingPriceChange",
    "LookupAnswer",
    "LookupRequest",
    "NamingSuggestion",
    "PriceNorm",
    "PriceObservation",
    "PriceObservationVoid",
    "Product",
    "ProductCapture",
    "ProductDistinctPair",
    "ProductIdentifier",
    "ProductImage",
    "ProductJob",
    "ProductProposal",
    "ProductStageResult",
    "Purchase",
    "PurchaseLine",
    "ReceiptAlias",
    "ReceiptDocument",
    "Recipe",
    "RecipeCostLine",
    "RecipeCostSnapshot",
    "RecipeIngredient",
    "RecipeNameIgnore",
    "RecipePin",
    "RefUsdaPortion",
    "Session",
    "ShoppingList",
    "ShoppingListItem",
    "ShoppingListItemQty",
    "TravelLeg",
    "TripPlan",
    "TripPlanStop",
    "TripPlanStopItem",
    "TripPlanVariant",
    "UnitRow",
    "UploadBatch",
    "UploadBatchReceipt",
    "UserSetting",
    "VendorListing",
]
