from fastapi import APIRouter

from app.api import api_tokens, auth, health, users

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(api_tokens.router)
