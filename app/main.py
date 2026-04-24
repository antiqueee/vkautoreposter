"""FastAPI entrypoint. Wire middleware + routers, nothing else lives here."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.auth.routes import router as auth_router
from app.config import get_settings
from app.routes.admin import router as admin_router
from app.routes.public import router as public_router
from app.routes.vkma import router as vkma_router


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    app = FastAPI(title="Repost Sync", version="0.1.0")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret_key,
        session_cookie="repost_sync_session",
        https_only=settings.app_env != "dev",
        same_site="lax",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )
    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(vkma_router)
    return app


app = create_app()
