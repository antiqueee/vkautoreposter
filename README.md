# Repost Sync

Consent-based automation for reposting a single VK public's posts across a
network of participants. Internal tool — not a public web service.

**Status:** phase 1 — OAuth onboarding + participant cabinet. Phase 2
(source polling + repost worker) and phase 3 (admin UI, pause, revoke) are
not yet implemented.

---

## Quickstart (local dev)

### 1. Register a VK Standalone application

Create a new **Standalone** app at <https://vk.com/editapp?act=create>.

- **Type:** Standalone-приложение
- After creation, open **Настройки** and fill in:
  - **Базовый домен:** `localhost`
  - **Доверенный redirect URI:** `http://localhost:8765/auth/vk/complete`
- Save. Copy the **ID приложения** — this is your `VK_APP_ID`.

The OAuth flow requests `scope=wall,offline`. `offline` makes the issued
user token non-expiring (revocable only by VK or by the user).

### 2. Generate local secrets

```bash
# Fernet key (44-char base64). Back up separately from the DB!
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Session cookie secret
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3. Configure `.env`

```bash
cp .env.example .env
```

Fill in `FERNET_KEY`, `SESSION_SECRET_KEY`, `VK_APP_ID`. Leave phase 2/3
fields blank for now.

### 4. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 5. Apply migrations

```bash
alembic upgrade head
```

This creates `./data/repost.db` (path configurable via `DB_PATH`).

### 6. Run the server

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

Open <http://localhost:8765>, click **Подключить аккаунт ВК**, approve on VK,
land on `/me`. That is the golden path for phase 1.

### 7. Smoke test

```bash
python scripts/smoke_test.py
```

Pulls the first active user from the DB, decrypts the token, calls
`users.get` on VK, prints the profile. If it succeeds, the end-to-end
OAuth + crypto path works.

---

## Security notes

- VK access tokens are stored **encrypted at rest** (Fernet / AES-128-CBC +
  HMAC-SHA256). The key is in `FERNET_KEY` — **back it up separately from
  the DB file**. If you lose the key, every participant will need to
  re-authorise.
- Tokens never appear in logs. The log format deliberately avoids rendering
  request bodies.
- Session cookies are signed with `SESSION_SECRET_KEY`. Rotating this key
  logs everyone out — harmless but annoying.
- In dev the `https_only` cookie flag is off. In `APP_ENV=prod` it's on, so
  you need TLS (Caddy + Let's Encrypt) in front of the app.

## Backups

Backing up only the DB without the Fernet key is useless — you'd end up
with a pile of unopenable ciphertexts. Back up the key separately, ideally
on a different medium.

Minimal strategy (cron example):

```bash
# Daily: copy the DB to local backup dir (fine while WAL is in use — SQLite
# guarantees the .db file is consistent at any moment under WAL).
0 3 * * * cp /path/to/repost-sync/data/repost.db /var/backups/repost-$(date +\%F).db
```

Fernet key: store once in a password manager + a second offline copy.
Never commit it.

---

## Project layout

```
app/
  config.py            Pydantic Settings
  db.py                SQLAlchemy engine, WAL pragma, connection helper
  crypto.py            Fernet wrap/unwrap
  models.py            Table definitions (Core)
  audit.py             audit_log writer
  vk/
    oauth.py           OAuth URL builder
    api.py             httpx client, users_get, error classes
  auth/
    sessions.py        session cookie helpers
    routes.py          /auth/vk/start, /auth/vk/complete, /auth/vk/logout
  routes/
    public.py          /, /me
  templates/           Jinja2 templates
  main.py              FastAPI wiring
migrations/            Alembic
scripts/
  smoke_test.py        phase 1 E2E check
```

## Roadmap

- **Phase 1 (done):** arch doc, OAuth onboarding, encrypted token storage,
  minimal cabinet, smoke test.
- **Phase 2 (next):** poll source public via `wall.get`, detect new posts,
  enqueue per-user tasks with log-normal scheduling, APScheduler worker,
  VK error-matrix handling.
- **Phase 3:** admin UI (participant list, manual trigger by URL, post
  history, failed-in-24h panel), pause/resume/revoke in cabinet, per-user
  audit view.
- **Phase 4:** Docker deploy, Caddy + Let's Encrypt, backup script,
  production readme.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full design.
