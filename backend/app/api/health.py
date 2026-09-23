from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import DbSession, OptionalUser
from app.core.config import get_settings, is_dev_build
from app.schemas.health import HealthOut
from app.services import health as health_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut, response_model_exclude_none=True)
async def health(db: DbSession, user: OptionalUser, response: Response) -> HealthOut:
    result = await health_service.health(db)
    if result.status == "failed":
        response.status_code = 503
    if user is None:
        return HealthOut(status=result.status)
    settings = get_settings()
    return result.model_copy(
        update={
            "version": settings.build_version,
            "commit": settings.build_commit,
            "is_dev": is_dev_build(settings.build_version, settings.build_commit),
        }
    )
