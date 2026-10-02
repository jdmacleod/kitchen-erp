"""Product photo files (03, 1I): ``GET /api/v1/media/<sha>/<variant>-<version>[-<mask>]``.

An address names its pipeline version and, for a cutout, its mask, so a change
produces a new address and a response can be cached for good. Media needs a
session or a token like every other route. A missing derivative whose original
exists is made again; a missing original is a 404 and the page shows its
placeholder.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.services import media

router = APIRouter(tags=["media"])
log = get_logger(__name__)

CACHE_CONTROL = "private, max-age=31536000, immutable"


@router.get(
    "/media/{sha}/{name}",
    response_class=FileResponse,
    responses={200: {"content": {"image/webp": {}}}, 404: {}},
)
async def get_media(sha: str, name: str, _: CurrentUser) -> FileResponse:
    parsed = media.parse_name(sha, name)
    if parsed is None or parsed.version != media.pipeline_version():
        raise ApiError(404, "not_found", "No such media.")
    path = await anyio.to_thread.run_sync(media.ensure, sha, parsed.variant, parsed.mask_sha)
    if path is None:
        # The address is the request's own input, so it is not logged (CodeQL log injection).
        log.warning("media source missing", extra={"variant": parsed.variant})
        raise ApiError(404, "not_found", "No such media.")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": CACHE_CONTROL})
