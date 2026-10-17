from fastapi import APIRouter

from app.api import (
    api_tokens,
    auth,
    brands,
    bridges,
    catalog,
    catalog_cleanup,
    geo,
    health,
    inbox,
    ingredient_link,
    lookups,
    media,
    naming_words,
    plan,
    product_captures,
    product_photos,
    product_proposals,
    purchases,
    receipts,
    recipes,
    rereads,
    search,
    to_identify,
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
router.include_router(catalog_cleanup.router)
router.include_router(ingredient_link.router)
router.include_router(catalog.router)
router.include_router(bridges.router)
router.include_router(geo.router)
router.include_router(brands.router)
router.include_router(naming_words.router)  # before purchases: a fixed path
router.include_router(purchases.router)
router.include_router(to_identify.router)
router.include_router(rereads.router)
router.include_router(receipts.router)
router.include_router(recipes.router)
router.include_router(plan.router)
router.include_router(inbox.router)
router.include_router(search.router)
router.include_router(vendor_suggestions.router)
router.include_router(product_photos.router)
router.include_router(product_proposals.router)
router.include_router(product_captures.router)
router.include_router(media.router)
router.include_router(lookups.router)
