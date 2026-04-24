# Repost Sync

Consent-based automation for reposting a single VK public's posts across a
network of participants. Internal tool — not a public web service.

**Current flow:** VK Mini App onboarding + Kate Mobile standalone OAuth token,
source polling, task enqueueing, CLI worker, admin dashboard, pause/resume,
revoke, and manual trigger by post URL.

---

## Quickstart (local dev)

### 1. Register a VK Mini App

Create a new **Mini App** in `dev.vk.ru` and set its placement URL to your real
HTTPS host, for example:

```text
https://app.subscribe-to-reposter.ru/vkma
```

The app itself does **not** use the VKMA profile token for reposting. VK blocks
`wall.repost` for Web and Mini App profile types. The mini app is only the
participant UI shell. The actual repost-capable user token comes from a legacy
standalone OAuth flow (`Kate Mobile` client id) and is pasted back into the
mini app once during onboarding.

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

Fill in `FERNET_KEY` and `SESSION_SECRET_KEY`. `VK_SOURCE_GROUP_ID` is only
needed when you start testing source polling.

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

Open:

- landing page: <http://localhost:8765/>
- mini app page directly for local UI work: <http://localhost:8765/vkma>

In production, participants should enter through the VK Mini App:

```text
https://vk.com/app54562844
```

Onboarding flow:

1. Click `Открыть окно VK`.
2. Approve access in the VK OAuth window.
3. Copy the full `oauth.vk.com/blank.html#access_token=...` URL.
4. Paste it back into the mini app.
5. The backend validates the token with `users.get`, encrypts it, stores it,
   and the account becomes active for automatic reposts.

### 7. Smoke test

```bash
python scripts/smoke_test.py
```

Pulls the first active user from the DB, decrypts the token, calls
`users.get` on VK, prints the profile. If it succeeds, the end-to-end
mini app onboarding + token storage path works.

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

The mini app placement URL must be a real HTTPS page. Put Caddy in front of the
app and set:

```env
APP_BASE_URL=https://vkautoreposter.example.com
```

VK Mini App settings:

```text
URL / iframe URL / mobile URL: https://vkautoreposter.example.com/vkma
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
- The mini app uses a signed bearer token in `localStorage` for its own API
  calls because third-party cookies inside the VK iframe are unreliable.
- Tokens never appear in logs. The log format deliberately avoids rendering
  request bodies.
- The participant still sees a VK warning on `oauth.vk.com/blank.html`; this is
  expected. The mini app asks them to paste the URL back into the app, where
  only the `access_token` fragment is extracted and sent to the server.
- Session cookies are still signed with `SESSION_SECRET_KEY`. Admin flows and
  any leftover legacy routes keep working, but the active participant flow no
  longer depends on session cookies.
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
    api.py             httpx client, users_get, wall_get, wall_repost, errors
  auth/
    admin.py           HTTP Basic admin auth
    bearer.py          signed bearer token for the mini app
    sessions.py        legacy session helpers
  routes/
    public.py          landing page
    vkma.py            /vkma UI + /api/vkma/*
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

- **Phase 1 (done):** arch doc, auth onboarding, encrypted token storage,
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
