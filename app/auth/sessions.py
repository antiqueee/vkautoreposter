"""Session cookie helpers.

Starlette's SessionMiddleware signs a JSON blob with itsdangerous. We store
only the participant's ``user_id`` — never the VK token.
"""

from __future__ import annotations

from fastapi import Request

SESSION_KEY_USER_ID = "uid"
SESSION_KEY_OAUTH_STATE = "vk_oauth_state"
SESSION_KEY_OAUTH_CODE_VERIFIER = "vk_oauth_code_verifier"


def set_current_user(request: Request, user_id: int) -> None:
    request.session[SESSION_KEY_USER_ID] = user_id


def get_current_user_id(request: Request) -> int | None:
    uid = request.session.get(SESSION_KEY_USER_ID)
    return int(uid) if uid is not None else None


def clear_session(request: Request) -> None:
    request.session.clear()


def set_oauth_state(request: Request, state: str) -> None:
    request.session[SESSION_KEY_OAUTH_STATE] = state


def pop_oauth_state(request: Request) -> str | None:
    return request.session.pop(SESSION_KEY_OAUTH_STATE, None)


def set_oauth_code_verifier(request: Request, code_verifier: str) -> None:
    request.session[SESSION_KEY_OAUTH_CODE_VERIFIER] = code_verifier


def pop_oauth_code_verifier(request: Request) -> str | None:
    return request.session.pop(SESSION_KEY_OAUTH_CODE_VERIFIER, None)
