"""FastAPI application factory."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api import router
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger, request_id_var

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    log.info("api starting")
    yield
    await dispose_engine()
    log.info("api stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kitchen ERP",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)
    app.include_router(router)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    return app


app = create_app()
