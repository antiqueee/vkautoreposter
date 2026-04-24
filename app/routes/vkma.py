"""One-shot VK Mini App smoke-test route.

Goal: confirm whether a token issued via VK Bridge (VKWebAppGetAuthToken)
inside a VKMA profile can call wall.repost, or hits the same 1051
"Method is not available for this profile type" as our Web app token.

Temporary. Delete this module once we either (a) switch the real flow to
VKMA, or (b) confirm VKMA is also blocked and pivot away.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

VKMA_APP_ID = 54562844
TEST_OBJECT = "wall-119521533_6928"

_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VKMA repost smoke</title>
<script src="https://cdn.jsdelivr.net/npm/@vkontakte/vk-bridge@latest/dist/browser.min.js"></script>
<style>
  body { font: 14px/1.4 -apple-system, system-ui, sans-serif; padding: 16px; }
  button { padding: 12px 20px; font-size: 15px; margin: 8px 0; }
  pre { background: #f4f4f6; padding: 12px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; }
  .ok { color: #1a7f37; } .err { color: #cf222e; }
</style>
</head>
<body>
<h2>VKMA wall.repost smoke-test</h2>
<p>app_id=__APP_ID__, object=__OBJECT__</p>
<button id="run">Попробовать repost</button>
<pre id="out">жду клика…</pre>
<script>
  const out = document.getElementById('out');
  const log = (label, data, cls) => {
    const line = `${label}: ${typeof data === 'string' ? data : JSON.stringify(data, null, 2)}`;
    out.innerHTML += `\\n<span class="${cls || ''}">${line.replace(/</g,'&lt;')}</span>`;
  };
  vkBridge.send('VKWebAppInit').then(r => log('init', r, 'ok')).catch(e => log('init_err', e, 'err'));
  document.getElementById('run').onclick = async () => {
    out.textContent = '';
    try {
      const tok = await vkBridge.send('VKWebAppGetAuthToken', { app_id: __APP_ID__, scope: 'wall' });
      log('get_auth_token', tok, 'ok');
      const resp = await vkBridge.send('VKWebAppCallAPIMethod', {
        method: 'wall.repost',
        params: { object: '__OBJECT__', access_token: tok.access_token, v: '5.199' },
      });
      log('wall.repost', resp, 'ok');
    } catch (e) {
      log('ERROR', e, 'err');
    }
  };
</script>
</body>
</html>
"""


@router.get("/vkma/test")
async def vkma_smoke_test() -> HTMLResponse:
    body = _HTML.replace("__APP_ID__", str(VKMA_APP_ID)).replace("__OBJECT__", TEST_OBJECT)
    return HTMLResponse(content=body)


@router.get("/vkma/ping")
async def vkma_ping() -> HTMLResponse:
    """Zero-JS page to verify VK can actually load our URL inside its iframe.

    If this renders inside vk.com/app54562844 but /vkma/test hangs, the Bridge
    script fetch is the culprit (CDN blocked / slow inside VK iframe).
    """
    return HTMLResponse(
        content=(
            '<!doctype html><meta charset="utf-8">'
            '<title>ping</title>'
            '<body style="font:20px system-ui;padding:24px">'
            'VKMA ping OK'
            '</body>'
        )
    )
