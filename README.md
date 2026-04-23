# Repost Sync

Consent-based automation for reposting a single VK public's posts across a
network of participants. Internal tool — not a public web service.

**Status:** phase 3 — OAuth onboarding, participant cabinet, source polling,
task enqueueing, CLI worker, admin dashboard, pause/resume/revoke, and manual
trigger by post URL.

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

Fill in `FERNET_KEY`, `SESSION_SECRET_KEY`, `VK_APP_ID`. `VK_SOURCE_GROUP_ID`
is only needed when you start testing source polling.

For the admin UI, also set:

```env
ADMIN_PASSWORD=long-random-password
```

Admin login is fixed as `admin`; password comes from env.

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
land on `/me`.

### 7. Smoke test

```bash
python scripts/smoke_test.py
```

Pulls the first active user from the DB, decrypts the token, calls
`users.get` on VK, prints the profile. If it succeeds, the end-to-end
OAuth + crypto path works.

### 8. Source polling and worker

Set the source public group ID in `.env`:

```env
VK_SOURCE_GROUP_ID=123456789
VK_SERVICE_TOKEN=
```

`VK_SOURCE_GROUP_ID` is positive; the app polls `owner_id=-VK_SOURCE_GROUP_ID`.
For public groups `wall.get` can work without `VK_SERVICE_TOKEN`; if VK returns
an access error, create an app service token in VK app settings and put it here.

Commands:

```bash
python -m app.worker poll_source
python -m app.worker run_due_tasks
python -m app.worker tick
```

`tick` does both: poll source, then execute due repost tasks.

### 9. Admin UI

Open <http://localhost:8765/admin>. Browser Basic Auth credentials:

```text
login: admin
password: ADMIN_PASSWORD from .env
```

The MVP admin UI shows participants, recent posts, failed tasks from the last
24 hours, source cursor status, and a manual trigger form for URLs like:

```text
https://vk.com/wall-123456789_42
```

---

## Docker

Local container run:

```bash
docker compose up --build
```

Run one worker tick:

```bash
docker compose run --rm app python -m app.worker tick
```

Recommended MVP cron setup: keep the web container as a single process and let
host cron call the worker. This avoids a process supervisor inside the container
and keeps failures visible in regular cron/docker logs.

Example crontab on the host:

```cron
* * * * * cd /opt/repost-sync && docker compose run --rm app python -m app.worker tick >> /var/log/repost-sync-worker.log 2>&1
```

Do not run two schedulers at once.

## Production HTTPS

For production OAuth, VK requires a real HTTPS redirect URL. Put Caddy in front
of the app and set:

```env
APP_BASE_URL=https://vkautoreposter.example.com
```

VK app settings:

```text
Базовый домен: vkautoreposter.example.com
Доверенный Redirect URL: https://vkautoreposter.example.com/auth/vk/complete
```

`Caddyfile.example` contains a minimal reverse proxy template.

### Server deploy behind an existing Caddy

If the server already has a Caddy reverse proxy for another project, use
`docker-compose.server.yml`. It joins the existing Docker network
`shtab-tasks_default` and does **not** bind host ports:

```bash
docker compose -f docker-compose.server.yml up -d --build
```

Then add a host block to the existing Caddy:

```caddy
app.subscribe-to-reposter.ru {
    reverse_proxy repost-sync:8765
}
```

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

Helper script:

```bash
BACKUP_DIR=/var/backups/repost-sync ./scripts/backup.sh
```

It backs up only the SQLite DB. `FERNET_KEY` must be copied separately.

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
    api.py             httpx client, users_get, wall_get, wall_repost, errors
  auth/
    admin.py           HTTP Basic admin auth
    sessions.py        session cookie helpers
    routes.py          /auth/vk/start, /auth/vk/complete, /auth/vk/logout
  routes/
    public.py          /, /me
    admin.py           /admin dashboard, manual trigger, participant controls
  worker/
    poller.py          wall.get polling + task enqueueing
    executor.py        due-task claim + wall.repost execution
    scheduling.py      log-normal scheduled_at sampler
    __main__.py        python -m app.worker ...
  templates/           Jinja2 templates
  main.py              FastAPI wiring
migrations/            Alembic
scripts/
  smoke_test.py        phase 1 E2E check
  backup.sh            SQLite backup helper
```

## Roadmap

- **Phase 1 (done):** arch doc, OAuth onboarding, encrypted token storage,
  minimal cabinet, smoke test.
- **Phase 2 (done):** poll source public via `wall.get`, detect new posts,
  enqueue per-user tasks with log-normal scheduling, CLI worker,
  VK error-matrix handling.
- **Phase 3 (done):** admin UI (participant list, manual trigger by URL, post
  history, failed-in-24h panel), pause/resume/revoke in cabinet, per-user
  audit view.
- **Phase 4 (partial):** Docker/Caddy templates and backup helper are present.
  Real production deploy waits for domain, SSH access, and VK app settings.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full design.
