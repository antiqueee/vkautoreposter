"""One-shot VK Mini App smoke-test route.

Goal: confirm whether a token issued via VK Bridge inside a VKMA profile
can call wall.repost, or hits the same 1051 "Method is not available for
this profile type" as our Web app token.

Temporary. Delete once the flow decision is made.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

VKMA_APP_ID = 54562844
TEST_OBJECT = "wall-119521533_6928"


# Minimal inline VK Bridge client — no external CDN, no dependency on network
# conditions inside the VK iframe. VK Bridge protocol is just postMessage with
# `{handler, params, type:"vk-connect"}` and matching `*Result` / `*Failed`
# messages from the parent window. This implements exactly what the smoke
# test needs: Init + GetAuthToken + CallAPIMethod.
_BRIDGE_JS = """
(() => {
  const pending = new Map();
  let counter = 0;
  window.addEventListener('message', (e) => {
    const d = e.data;
    if (!d || typeof d !== 'object') return;
    const rid = d.data && d.data.request_id;
    if (rid == null) return;
    const cb = pending.get(String(rid));
    if (!cb) return;
    pending.delete(String(rid));
    if (typeof d.type === 'string' && d.type.endsWith('Result')) cb.resolve(d.data);
    else cb.reject(d);
  });
  window.vkSend = (handler, params = {}) => new Promise((resolve, reject) => {
    const request_id = 'r' + (++counter);
    pending.set(request_id, { resolve, reject });
    window.parent.postMessage(
      { handler, params: Object.assign({}, params, { request_id }), type: 'vk-connect' },
      '*'
    );
    setTimeout(() => {
      if (pending.has(request_id)) {
        pending.delete(request_id);
        reject({ type: 'timeout', handler });
      }
    }, 15000);
  });
})();
"""


def _page(body_inner: str, run_on_load: str = "") -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VKMA smoke</title>
<style>
  body {{ font: 14px/1.4 -apple-system, system-ui, sans-serif; padding: 16px; margin: 0; }}
  button {{ padding: 12px 20px; font-size: 15px; margin: 8px 0; }}
  pre {{ background: #f4f4f6; padding: 12px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; }}
  .ok {{ color: #1a7f37; }} .err {{ color: #cf222e; }}
</style>
<script>{_BRIDGE_JS}</script>
</head>
<body>
{body_inner}
<script>
  // Always send VKWebAppInit first — VK keeps the "mini apps" spinner on
  // screen until the iframe reports Init. Fire and forget; errors here mean
  // we're not actually inside a VK iframe, which is fine for out-of-VK debug.
  window.vkSend('VKWebAppInit').catch(() => {{}});
  {run_on_load}
</script>
</body>
</html>
"""


@router.get("/vkma/ping")
async def vkma_ping() -> HTMLResponse:
    """Sanity check: page reports Init, spinner lifts, renders 'VKMA ping OK'.

    If this works inside vk.com/app54562844 but /vkma/test does not,
    the issue is wall.repost-specific, not infrastructure.
    """
    return HTMLResponse(_page('<h2>VKMA ping OK</h2><p>Bridge Init отправлен.</p>'))


@router.get("/vkma/test")
async def vkma_smoke_test() -> HTMLResponse:
    run = f"""
  const out = document.getElementById('out');
  const log = (label, data, cls) => {{
    const line = `${{label}}: ${{typeof data === 'string' ? data : JSON.stringify(data, null, 2)}}`;
    const span = document.createElement('span');
    if (cls) span.className = cls;
    span.textContent = line + '\\n';
    out.appendChild(span);
  }};
  document.getElementById('run').onclick = async () => {{
    out.textContent = '';
    try {{
      const tok = await window.vkSend('VKWebAppGetAuthToken', {{ app_id: {VKMA_APP_ID}, scope: 'wall' }});
      log('get_auth_token', tok, 'ok');
      const resp = await window.vkSend('VKWebAppCallAPIMethod', {{
        method: 'wall.repost',
        params: {{ object: '{TEST_OBJECT}', access_token: tok.access_token, v: '5.199' }},
      }});
      log('wall.repost', resp, 'ok');
    }} catch (e) {{
      log('ERROR', e, 'err');
    }}
  }};
"""
    body = f"""
<h2>VKMA wall.repost smoke-test</h2>
<p>app_id={VKMA_APP_ID}, object={TEST_OBJECT}</p>
<button id="run">Попробовать repost</button>
<pre id="out">жду клика…</pre>
"""
    return HTMLResponse(_page(body, run))
