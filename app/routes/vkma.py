"""VK Mini App routes.

The mini app lives at vk.com/app54562844 and embeds the page served here.
Because VK closed ``wall.repost`` for Web and VKMA profile tokens
(error 15/1134 "non-standalone applications"), the participant has to
authorise against a legacy standalone OAuth flow (Kate Mobile client_id)
and paste the resulting URL back — that's the only way to get a token
with ``wall.repost`` permission in 2026.

Routes:
  GET  /vkma                   — single-page UI (inline HTML + JS)
  POST /api/vkma/token         — body {access_token}, validates via VK
                                 users.get, upserts user, returns bearer
  GET  /api/vkma/status        — Authorization: Bearer <…>; returns state
  POST /api/vkma/pause         — set status=paused
  POST /api/vkma/resume        — set status=active
  POST /api/vkma/revoke        — set status=revoked; cancel pending tasks
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import insert, select, update

from app.audit import record as audit_record
from app.auth.bearer import get_bearer_user_id, sign_bearer
from app.crypto import encrypt_token
from app.db import connection
from app.models import Actor, TaskStatus, UserStatus, repost_tasks, users
from app.vk.api import VkApiError, VkNetworkError, users_get

_log = logging.getLogger("app.routes.vkma")

router = APIRouter()

# Kate Mobile — a long-standing standalone VK app whose tokens are not blocked
# on wall.repost. We don't own it, but it's OAuth-public and widely used by
# third-party VK tools. This is the only practical path to a non-blocked
# user token until VK re-opens wall.repost for new app types.
KATE_CLIENT_ID = 2685278
KATE_OAUTH_URL = (
    "https://oauth.vk.com/authorize"
    f"?client_id={KATE_CLIENT_ID}"
    "&scope=wall,offline"
    "&response_type=token"
    "&redirect_uri=https://oauth.vk.com/blank.html"
    "&v=5.199&display=page"
)


def _require_user(request: Request) -> int:
    uid = get_bearer_user_id(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="unauthenticated")
    return uid


def _load_user(internal_id: int) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            select(
                users.c.id,
                users.c.vk_user_id,
                users.c.display_name,
                users.c.status,
            ).where(users.c.id == internal_id)
        ).first()
    return dict(row._mapping) if row else None


@router.get("/vkma")
async def vkma_page() -> HTMLResponse:
    return HTMLResponse(_PAGE_HTML)


@router.post("/api/vkma/token")
async def vkma_token(request: Request) -> JSONResponse:
    body = await request.json()
    access_token = (body or {}).get("access_token")
    if not isinstance(access_token, str) or len(access_token) < 20:
        raise HTTPException(status_code=400, detail="invalid_token_format")

    try:
        profiles = await users_get(access_token)
    except VkApiError as exc:
        _log.warning("users.get rejected pasted token: code=%s", exc.error_code)
        raise HTTPException(status_code=400, detail=f"vk_error_{exc.error_code}") from exc
    except VkNetworkError as exc:
        _log.warning("users.get network error: %s", exc)
        raise HTTPException(status_code=502, detail="vk_unreachable") from exc

    if not profiles:
        raise HTTPException(status_code=400, detail="vk_empty_profile")
    profile = profiles[0]
    vk_user_id = int(profile["id"])
    display_name = (
        f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
        or profile.get("screen_name")
        or f"id{vk_user_id}"
    )

    encrypted = encrypt_token(access_token)
    now = datetime.utcnow()
    ip = request.client.host if request.client else None

    with connection() as conn:
        existing = conn.execute(
            select(users.c.id, users.c.status).where(users.c.vk_user_id == vk_user_id)
        ).first()
        if existing is None:
            result = conn.execute(
                insert(users).values(
                    vk_user_id=vk_user_id,
                    display_name=display_name,
                    status=UserStatus.ACTIVE,
                    encrypted_token=encrypted,
                    token_verified_at=now,
                    consented_at=now,
                )
            )
            internal_id = int(result.inserted_primary_key[0])
            audit_record(
                conn,
                actor=Actor.USER,
                action="vkma_onboard",
                user_id=internal_id,
                details={"vk_user_id": vk_user_id, "display_name": display_name},
                ip_address=ip,
            )
        else:
            internal_id = int(existing.id)
            conn.execute(
                update(users)
                .where(users.c.id == internal_id)
                .values(
                    display_name=display_name,
                    encrypted_token=encrypted,
                    token_verified_at=now,
                    status=UserStatus.ACTIVE,
                    updated_at=now,
                )
            )
            audit_record(
                conn,
                actor=Actor.USER,
                action="vkma_reconnect",
                user_id=internal_id,
                details={"vk_user_id": vk_user_id, "previous_status": existing.status},
                ip_address=ip,
            )

    return JSONResponse(
        {
            "ok": True,
            "auth_token": sign_bearer(internal_id, vk_user_id),
            "display_name": display_name,
            "status": UserStatus.ACTIVE,
        }
    )


@router.get("/api/vkma/status")
async def vkma_status(request: Request) -> JSONResponse:
    uid = get_bearer_user_id(request)
    if uid is None:
        return JSONResponse({"connected": False})
    user = _load_user(uid)
    if user is None or user["status"] == UserStatus.REVOKED:
        return JSONResponse({"connected": False})
    return JSONResponse(
        {
            "connected": True,
            "status": user["status"],
            "display_name": user["display_name"],
            "vk_user_id": user["vk_user_id"],
        }
    )


@router.post("/api/vkma/pause")
async def vkma_pause(request: Request) -> JSONResponse:
    uid = _require_user(request)
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            update(users).where(users.c.id == uid).values(status=UserStatus.PAUSED, updated_at=now)
        )
        audit_record(conn, actor=Actor.USER, action="vkma_pause", user_id=uid)
    return JSONResponse({"ok": True, "status": UserStatus.PAUSED})


@router.post("/api/vkma/resume")
async def vkma_resume(request: Request) -> JSONResponse:
    uid = _require_user(request)
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            update(users).where(users.c.id == uid).values(status=UserStatus.ACTIVE, updated_at=now)
        )
        audit_record(conn, actor=Actor.USER, action="vkma_resume", user_id=uid)
    return JSONResponse({"ok": True, "status": UserStatus.ACTIVE})


@router.post("/api/vkma/revoke")
async def vkma_revoke(request: Request) -> JSONResponse:
    uid = _require_user(request)
    now = datetime.utcnow()
    with connection() as conn:
        conn.execute(
            update(users)
            .where(users.c.id == uid)
            .values(
                status=UserStatus.REVOKED,
                encrypted_token=encrypt_token("revoked"),
                updated_at=now,
            )
        )
        conn.execute(
            update(repost_tasks)
            .where(repost_tasks.c.user_id == uid)
            .where(repost_tasks.c.status == TaskStatus.PENDING)
            .values(status=TaskStatus.CANCELLED, finished_at=now, error_code="user_revoked")
        )
        audit_record(conn, actor=Actor.USER, action="vkma_revoke", user_id=uid)
    return JSONResponse({"ok": True})


_PAGE_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подписка на репосты</title>
<style>
  :root { --border:#e4e7eb; --muted:#6b7280; --bg:#f7f8fa; --ok:#1a7f37; --err:#cf222e; }
  * { box-sizing: border-box; }
  body { font: 15px/1.45 -apple-system, system-ui, "Segoe UI", sans-serif; margin: 0; padding: 16px; color: #111; background: #fff; }
  h2 { margin: 0 0 12px; font-size: 18px; }
  p { margin: 8px 0; color: var(--muted); }
  h3 { margin: 0 0 8px; font-size: 15px; }
  .card { border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin: 12px 0; background: #fff; }
  button { font: inherit; font-weight: 600; padding: 12px 16px; border-radius: 10px; border: 0; cursor: pointer; width: 100%; margin-top: 8px; }
  button.primary { background: #0077ff; color: #fff; }
  button.secondary { background: var(--bg); color: #111; border: 1px solid var(--border); }
  button.danger { background: transparent; color: var(--err); border: 1px solid var(--border); }
  button:disabled { opacity: 0.5; cursor: default; }
  input[type=text] { width: 100%; font: inherit; padding: 10px; border-radius: 8px; border: 1px solid var(--border); }
  ol { padding-left: 20px; }
  ol li { margin: 6px 0; color: #111; }
  code { background: #f3f4f6; padding: 2px 6px; border-radius: 6px; }
  .muted { color: var(--muted); font-size: 13px; }
  .error { color: var(--err); margin-top: 8px; font-size: 13px; }
  .ok { color: var(--ok); margin-top: 8px; font-size: 13px; }
  .status-pill { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
  .status-active { background: #e7f5ec; color: var(--ok); }
  .status-paused { background: #fff4e5; color: #a5670b; }
  .steps { margin: 0; padding-left: 18px; }
  .steps li { margin: 8px 0; }
  .stack + .stack { margin-top: 12px; }
</style>
</head>
<body>
  <div id="root">Загрузка…</div>

<script>
(() => {
  const KATE_OAUTH_URL = "__KATE_OAUTH_URL__";
  const LS_KEY = "vkma_auth_token";

  // ---- VK Bridge (minimal, inline, no CDN) ----
  // We only need VKWebAppInit so VK lifts the "mini apps" spinner.
  window.parent.postMessage(
    { handler: "VKWebAppInit", params: { request_id: "init" }, type: "vk-connect" },
    "*"
  );

  const root = document.getElementById("root");
  const getToken = () => localStorage.getItem(LS_KEY);
  const setToken = (t) => { if (t) localStorage.setItem(LS_KEY, t); else localStorage.removeItem(LS_KEY); };

  async function api(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    const tok = getToken();
    if (tok) headers["Authorization"] = "Bearer " + tok;
    const resp = await fetch(path, Object.assign({ headers }, opts));
    const data = await resp.json().catch(() => ({}));
    return { status: resp.status, data };
  }

  function parseTokenFromUrl(raw) {
    // Expected: https://oauth.vk.com/blank.html#access_token=...&expires_in=0&user_id=...
    if (!raw) return null;
    const hashAt = raw.indexOf("#");
    const frag = hashAt >= 0 ? raw.slice(hashAt + 1) : raw;
    const params = new URLSearchParams(frag);
    const tok = params.get("access_token");
    return tok && tok.length > 20 ? tok : null;
  }

  function parseVkUserIdFromUrl(raw) {
    if (!raw) return null;
    const hashAt = raw.indexOf("#");
    const frag = hashAt >= 0 ? raw.slice(hashAt + 1) : raw;
    const params = new URLSearchParams(frag);
    return params.get("user_id");
  }

  // ---- Views ----

  function renderConnect(errMsg, pastedValue = "") {
    root.innerHTML = `
      <h2>Подписка на репосты</h2>
      <p>Подключение делается один раз. После этого новые посты будут репоститься автоматически, а остановить подписку можно здесь же.</p>

      <div class="card">
        <div class="stack">
          <h3>Шаг 1. Получить доступ</h3>
          <ol class="steps">
            <li>Нажми <b>«Открыть окно VK»</b>.</li>
            <li>В новом окне нажми <b>«Разрешить»</b>.</li>
            <li>Когда откроется <code>oauth.vk.com/blank.html#…</code>, скопируй адрес из строки браузера целиком.</li>
          </ol>
          <button class="primary" id="btn-open">Открыть окно VK</button>
        </div>

        <div class="stack">
          <h3>Шаг 2. Вставить адрес</h3>
          <p class="muted">Мы сами вытащим токен из адреса. Вставлять что-то вручную внутри ссылки не нужно.</p>
          <p class="muted">Важно: адрес вставляется здесь, внутри этого мини-приложения. Он не публикуется и не отправляется на сторонние сайты. Из него берётся только токен для подключения твоей подписки.</p>
          <input id="paste" type="text" placeholder="https://oauth.vk.com/blank.html#access_token=…" value="${pastedValue.replace(/"/g, "&quot;")}">
          <button class="secondary" id="btn-clip">Вставить из буфера</button>
          <button class="primary" id="btn-submit">Я скопировал адрес, подключить</button>
          <div class="muted" id="hint"></div>
        </div>
        ${errMsg ? `<div class="error">${errMsg}</div>` : ""}
      </div>
    `;

    const pasteInput = document.getElementById("paste");
    const hint = document.getElementById("hint");

    const refreshHint = () => {
      const raw = pasteInput.value.trim();
      const tok = parseTokenFromUrl(raw);
      const vkUserId = parseVkUserIdFromUrl(raw);
      if (!raw) {
        hint.textContent = "После копирования просто вставь адрес целиком в поле выше.";
      } else if (tok) {
        hint.textContent = vkUserId
          ? `Адрес выглядит правильно. Найден token и user_id=${vkUserId}.`
          : "Адрес выглядит правильно. Токен найден.";
      } else {
        hint.textContent = "Пока не вижу access_token. Нужен полный адрес с #access_token=...";
      }
    };
    refreshHint();
    pasteInput.addEventListener("input", refreshHint);

    document.getElementById("btn-open").onclick = () => {
      window.open(KATE_OAUTH_URL, "_blank", "noopener");
    };
    document.getElementById("btn-clip").onclick = async () => {
      try {
        const text = await navigator.clipboard.readText();
        pasteInput.value = text;
        refreshHint();
      } catch (e) {
        renderConnect("Не удалось прочитать буфер. Вставь адрес вручную в поле выше.", pasteInput.value);
      }
    };
    document.getElementById("btn-submit").onclick = async () => {
      const raw = pasteInput.value.trim();
      const tok = parseTokenFromUrl(raw);
      if (!tok) {
        renderConnect("В адресе не найден <code>access_token</code>. Скопируй полный адрес из строки браузера и вставь сюда без изменений.", raw);
        return;
      }
      const btn = document.getElementById("btn-submit");
      btn.disabled = true; btn.textContent = "Подключаем…";
      const r = await api("/api/vkma/token", { method: "POST", body: JSON.stringify({ access_token: tok }) });
      if (r.status === 200 && r.data.ok) {
        setToken(r.data.auth_token);
        bootstrap();
      } else {
        const reason = r.data && r.data.detail ? r.data.detail : ("HTTP " + r.status);
        renderConnect("Не получилось подключить: " + reason, raw);
      }
    };
  }

  function renderConnected(info) {
    const isActive = info.status === "active";
    root.innerHTML = `
      <h2>Подписка подключена</h2>
      <p><b>${info.display_name}</b> · <a href="https://vk.com/id${info.vk_user_id}" target="_blank" rel="noopener">id${info.vk_user_id}</a></p>

      <div class="card">
        <div>
          <span class="status-pill ${isActive ? "status-active" : "status-paused"}">
            ${isActive ? "Активна" : "На паузе"}
          </span>
        </div>
        <p class="muted" style="margin-top:12px">
          ${isActive
            ? "Новые посты будут автоматически репоститься на твою стену с небольшой случайной задержкой."
            : "Подписка стоит на паузе. Новые посты пропускаются, но аккаунт остаётся подключён."}
        </p>
        <button class="${isActive ? "secondary" : "primary"}" id="btn-toggle">
          ${isActive ? "Поставить на паузу" : "Возобновить"}
        </button>
        <button class="danger" id="btn-revoke">Отключить и удалить токен</button>
      </div>
    `;

    document.getElementById("btn-toggle").onclick = async () => {
      const btn = document.getElementById("btn-toggle");
      btn.disabled = true;
      const path = isActive ? "/api/vkma/pause" : "/api/vkma/resume";
      const r = await api(path, { method: "POST" });
      if (r.status === 200) bootstrap();
      else { btn.disabled = false; alert("Не получилось обновить статус"); }
    };
    document.getElementById("btn-revoke").onclick = async () => {
      if (!confirm("Отключить подписку? Токен будет удалён из системы, все запланированные репосты отменены.")) return;
      const r = await api("/api/vkma/revoke", { method: "POST" });
      if (r.status === 200) { setToken(null); bootstrap(); }
      else alert("Не получилось отключить");
    };
  }

  async function bootstrap() {
    root.textContent = "Загрузка…";
    const r = await api("/api/vkma/status");
    if (r.status !== 200) {
      root.innerHTML = `<p class="error">Ошибка связи с сервером (HTTP ${r.status}). Обнови страницу.</p>`;
      return;
    }
    if (r.data.connected) renderConnected(r.data);
    else { setToken(null); renderConnect(null); }
  }

  bootstrap();
})();
</script>
</body>
</html>
""".replace("__KATE_OAUTH_URL__", KATE_OAUTH_URL)
