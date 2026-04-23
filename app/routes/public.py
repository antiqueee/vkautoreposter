"""Public-facing routes: landing and participant cabinet."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth.sessions import get_current_user_id
from app.db import connection
from app.models import users

router = APIRouter()

_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates"),
)


@router.get("/")
async def landing(request: Request) -> Response:
    if get_current_user_id(request) is not None:
        return RedirectResponse(url="/me", status_code=302)
    return _TEMPLATES.TemplateResponse(request, "landing.html", {})


@router.get("/me")
async def me(request: Request) -> Response:
    uid = get_current_user_id(request)
    if uid is None:
        return RedirectResponse(url="/", status_code=302)
    with connection() as conn:
        row = conn.execute(
            select(
                users.c.id,
                users.c.vk_user_id,
                users.c.display_name,
                users.c.status,
                users.c.consented_at,
                users.c.token_verified_at,
            ).where(users.c.id == uid)
        ).first()
    if row is None:
        request.session.clear()
        return RedirectResponse(url="/", status_code=302)
    return _TEMPLATES.TemplateResponse(request, "me.html", {"user": row._mapping})
