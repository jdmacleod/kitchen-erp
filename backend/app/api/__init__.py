from fastapi import APIRouter

from app.api import (
    api_tokens,
    auth,
    catalog,
    geo,
    health,
    inbox,
    ingredient_link,
    media,
    product_captures,
    product_photos,
    product_proposals,
    purchases,
    receipts,
    search,
    units,
    users,
    vendor_suggestions,
)

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(api_tokens.router)
router.include_router(units.router)
router.include_router(ingredient_link.router)
router.include_router(catalog.router)
router.include_router(geo.router)
router.include_router(purchases.router)
router.include_router(receipts.router)
router.include_router(inbox.router)
router.include_router(search.router)
router.include_router(vendor_suggestions.router)
router.include_router(product_photos.router)
router.include_router(product_proposals.router)
router.include_router(product_captures.router)
router.include_router(media.router)
