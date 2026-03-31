from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.api.v1.approvals import start_poller
from app.config import get_settings
from app.db import create_tables
from mangum import Mangum



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001

    settings = get_settings()
    await create_tables()
    logger.info("DevOps Agent Platform starting up")
    token_preview = (settings.github_pat[:12] + "...") if settings.github_pat else "NOT SET"
    logger.info("GITHUB_PERSONAL_ACCESS_TOKEN loaded: %s", token_preview)
    poller_task = asyncio.create_task(start_poller()) if os.getenv("ENABLE_POLLER", "true").lower() == "true" else None
    yield
    if poller_task:
        poller_task.cancel()
    logger.info("DevOps Agent Platform shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title="DevOps Agent Platform API",
        version="0.1.0",
        description=(
            "Enterprise DevOps Agent — GitHub webhook-driven CI/CD automation, "
            "AI pipeline analysis, Azure infrastructure provisioning."
        ),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    allowed_origins = list(settings.cors_origins or [])
    if settings.frontend_url and settings.frontend_url not in allowed_origins:
        allowed_origins.append(settings.frontend_url)

    if os.getenv("DEV_ALLOW_ALL_CORS", "").lower() == "true":
        logger.warning("DEV_ALLOW_ALL_CORS is enabled — allowing all origins for development")
        cors_origins = ["*"]
    else:
        cors_origins = allowed_origins

    logger.info("CORS allowed origins: %s", cors_origins)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router, prefix="/api")
    return application


app = create_app()
handler = Mangum(app)
