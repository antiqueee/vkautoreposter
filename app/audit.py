"""Audit log writer.

Every user-visible or security-relevant event goes here. Cheap to write (one
row), cheap to query (indexed on user_id + created_at). Never write tokens or
any other secret into details_json.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import Connection, insert

from app.models import Actor, audit_log

_log = logging.getLogger("app.audit")


def record(
    conn: Connection,
    *,
    actor: str,
    action: str,
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    if actor not in {Actor.USER, Actor.ADMIN, Actor.SYSTEM, Actor.VK_CALLBACK}:
        raise ValueError(f"invalid audit actor: {actor!r}")
    conn.execute(
        insert(audit_log).values(
            user_id=user_id,
            actor=actor,
            action=action,
            details_json=json.dumps(details, ensure_ascii=False) if details else None,
            ip_address=ip_address,
        )
    )
    _log.info("audit actor=%s action=%s user_id=%s", actor, action, user_id)
