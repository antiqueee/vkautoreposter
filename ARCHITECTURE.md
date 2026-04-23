# Architecture — Repost Sync

## Scope

Consent-based automation of reposts from a single VK public to the walls of
~70 participants who already do this by hand. OAuth-based consent, per-user
pause/revoke, full audit trail. Not a bot network, not astroturfing — a
rota tool for a political-communications team's volunteer network.

## Stack

- Python 3.12
- FastAPI + Starlette SessionMiddleware
- SQLAlchemy 2.x **Core** (not ORM) + Alembic
- SQLite with `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`
- `cryptography` (Fernet) for token-at-rest encryption
- `httpx` for VK HTTP calls (no VK SDK — they lag API versions)
- Jinja2 + HTMX (HTMX arrives in phase 3 with admin UI)
- APScheduler (phase 2) for the in-process minute tick

Rationale for deliberately boring choices: workload is tiny (a few posts/day
× 70 participants), so the ceiling of SQLite + single-process FastAPI +
in-process scheduler is several orders of magnitude above what we need.
Redis/Celery/Postgres would be complexity for a hypothetical future.

## Components

```
                         VK
   ┌──────────────────────────────────────────────┐
   │  oauth.vk.com            api.vk.com          │
   │        ▲                     ▲               │
   │        │ user consent        │ wall.get      │
   │        │                     │ users.get     │
   │        │                     │ wall.repost   │
   └────────┼─────────────────────┼───────────────┘
            │                     │
   ┌────────▼─────────────────────▼───────────────┐
   │            FastAPI (single process)          │
   │                                              │
   │  /auth/vk/*     /me         /admin/*         │
   │  participant    cabinet     coordinator UI   │
   │  OAuth          (phase 3)                    │
   │                                              │
   │  APScheduler in-process (phase 2)            │
   │    poll_source  — every 60s  (wall.get)      │
   │    run_due      — every 60s  (claim + post)  │
   │                                              │
   │  CLI: python scripts/smoke_test.py           │
   │       python -m app.worker run_due_tasks     │
   └──────────────────────┬───────────────────────┘
                          │
                     ┌────▼─────┐
                     │  SQLite  │
                     │  (WAL)   │
                     └──────────┘
```

Important: **no Callback API / inbound webhook.** We poll `wall.get` on the
source public with a service token once per minute. This is one request,
not seventy, and removes the need for a public URL that VK can reach —
which matters for local dev and simplifies prod deployment.

## Schema (phase 1 subset is live; rest arrives in later phases)

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vk_user_id INTEGER NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK(status IN ('active','paused','revoked','invalid_token')),
    encrypted_token BLOB NOT NULL,
    token_verified_at DATETIME,
    consented_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_users_status ON users(status);

-- Phase 2:
CREATE TABLE source_config (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    vk_group_id INTEGER NOT NULL,        -- positive
    service_token_encrypted BLOB NOT NULL,
    last_seen_vk_post_id INTEGER NOT NULL DEFAULT 0,
    last_polled_at DATETIME,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vk_owner_id INTEGER NOT NULL,   -- negative for groups
    vk_post_id INTEGER NOT NULL,
    detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME NOT NULL,
    url TEXT NOT NULL,
    text_preview TEXT,
    marked_as_ads INTEGER NOT NULL DEFAULT 0,
    UNIQUE(vk_owner_id, vk_post_id)
);

CREATE TABLE repost_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    post_id INTEGER NOT NULL REFERENCES posts(id),
    scheduled_at DATETIME NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending','running','done','failed',
        'skipped_paused','cancelled','missed'
    )),
    started_at DATETIME,
    finished_at DATETIME,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_details TEXT,
    UNIQUE(user_id, post_id)
);
CREATE INDEX idx_repost_tasks_due  ON repost_tasks(status, scheduled_at);
CREATE INDEX idx_repost_tasks_user ON repost_tasks(user_id);

-- Live now (phase 1):
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    actor TEXT NOT NULL CHECK(actor IN ('user','admin','system','vk_callback')),
    action TEXT NOT NULL,
    details_json TEXT,
    ip_address TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_user_time ON audit_log(user_id, created_at);
```

No `admins` table. One coordinator → HTTP Basic + `ADMIN_PASSWORD` from env.
Good for the single-coordinator model; swap for a real table in ~30 min if
a second coordinator appears.

## OAuth flow (Implicit + offline)

```
Participant  Browser         Server            VK
    │           │               │               │
    │ click     │               │               │
    ├──────────>│ GET /auth/vk/start            │
    │           ├──────────────>│ generate state│
    │           │               │ session[state]│
    │           │ 302 oauth.vk.com/authorize?   │
    │           │   client_id&scope=wall,offline│
    │           │   response_type=token&state=X │
    │           │<──────────────┤               │
    │           │──────────────────────────────>│
    │           │<──────────────────────────────┤ consent screen
    │ approve   │                               │
    ├──────────>│                               │
    │           │ 302 /auth/vk/complete         │
    │           │   #access_token=...&user_id=  │
    │           │   ...&state=X                 │
    │           │<──────────────────────────────┤
    │           │ GET /auth/vk/complete         │
    │           ├──────────────>│               │
    │           │ 200 HTML+JS   │               │
    │           │<──────────────┤               │
    │           │ JS reads fragment             │
    │           │ JS POST /auth/vk/complete     │
    │           │   form: access_token,user_id, │
    │           │          state                │
    │           ├──────────────>│               │
    │           │               │ verify state  │
    │           │               │ users.get     │
    │           │               ├──────────────>│
    │           │               │<──────────────┤
    │           │               │ encrypt token │
    │           │               │ UPSERT users  │
    │           │               │ set session   │
    │           │ 303 /me       │               │
    │           │<──────────────┤               │
```

Fragment carries the token, so it never appears in server logs or reverse-
proxy access logs. The JS reader then calls `history.replaceState` to wipe
it from the browser history too.

## Source polling (phase 2)

Every 60 seconds:

1. `wall.get?owner_id=-vk_group_id&count=10` with the **service token**
   (works on public groups without a user token).
2. Skip pinned post (`is_pinned=1`).
3. Skip posts where `marked_as_ads=1`.
4. Keep posts whose `id > source_config.last_seen_vk_post_id`.
5. For each new post, inside one transaction:
   - INSERT into `posts` (UNIQUE protects against retry dups).
   - For each user with `status='active'`:
     - `delay = clip(exp(gauss(μ=ln(3600), σ=0.7)), 60, 18000)`
     - INSERT into `repost_tasks` with `scheduled_at = max(published_at, now()) + delay`.
   - UPDATE `source_config.last_seen_vk_post_id`.
6. Commit.

## Task execution (phase 2)

Every 60 seconds the worker claims up to 50 due tasks:

```sql
BEGIN IMMEDIATE;
UPDATE repost_tasks
   SET status='running', started_at=CURRENT_TIMESTAMP
 WHERE id IN (
     SELECT id FROM repost_tasks
      WHERE status='pending' AND scheduled_at <= CURRENT_TIMESTAMP
      ORDER BY scheduled_at
      LIMIT 50
 )
RETURNING id, user_id, post_id, retry_count;
COMMIT;
```

SQLite WAL serialises write transactions, so two concurrent workers cannot
claim the same row. Policy: run exactly one scheduler (either in-process
APScheduler or host cron, not both).

For each claimed task: re-read user → if not `active`, finalise as
`skipped_paused` / `cancelled`; if `now - scheduled_at > MISSED_THRESHOLD`,
finalise as `missed`; else decrypt token and call `wall.repost`.

## VK error matrix

| Code | Meaning                  | Action                                                         |
|-----:|--------------------------|----------------------------------------------------------------|
| 5    | Auth failed              | user → `invalid_token`, cancel pending, audit, alert           |
| 6    | Too many req/sec         | retry with backoff, don't consume retry_count                  |
| 9    | Flood control            | task → `failed` (`flood`), alert                               |
| 14   | Captcha                  | task → `failed` (`captcha`), user → `invalid_token`, alert     |
| 15   | Access denied            | task → `failed` (`access_denied`) — user may have blocked pub |
| 17   | Validation required      | task → `failed`, user → `invalid_token`                        |
| 18   | User banned              | task → `failed`, user → `revoked`                              |
| 29   | Method rate limit        | retry with backoff                                             |
| 100  | Invalid param            | task → `failed` — our bug, full details logged                 |
| 214  | Wall access denied       | task → `failed`                                                |
| network/5xx | timeout / conn reset | retry up to 3 times, exponential backoff (60s, 300s, 1200s) |

## Scope-out (explicit non-goals)

- No vault / KMS. Fernet key in env.
- No multi-source (one install = one source public).
- No Prometheus. Admin UI renders counts from `audit_log`.
- No LLM-generated comments. Raw `wall.repost`, no message.
- No auto token-refresh prompt. Invalidated participants re-authorise via
  the onboarding link.

## Delivery phases

- **Phase 1 (done):** OAuth onboarding, encrypted tokens at rest,
  minimal cabinet, smoke test.
- **Phase 2:** source polling, log-normal scheduling, worker, error matrix.
- **Phase 3:** admin UI, pause/resume/revoke, per-user repost history.
- **Phase 4:** Caddy + Let's Encrypt, backup automation, production deploy.
