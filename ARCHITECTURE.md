# Architecture — Repost Sync

## Scope

Consent-based automation of reposts from a single VK public to the walls of
~70 participants who already do this by hand. Per-user consent, pause/revoke,
full audit trail. Not a bot network, not astroturfing — a rota tool for a
political-communications team's volunteer network.

## Stack

- Python 3.12
- FastAPI
- SQLAlchemy 2.x **Core** (not ORM) + Alembic
- SQLite with `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`
- `cryptography` (Fernet) for token-at-rest encryption
- `httpx` for VK HTTP calls (no VK SDK — they lag API versions)
- inline HTML/JS for the VK Mini App + Jinja2 for the landing page
- Host cron or manual CLI for the minute tick

Rationale for deliberately boring choices: workload is tiny (a few posts/day
× 70 participants), so the ceiling of SQLite + single-process FastAPI +
host cron is several orders of magnitude above what we need.
Redis/Celery/Postgres would be complexity for a hypothetical future.

## Components

```
                         VK
   ┌──────────────────────────────────────────────┐
   │  dev.vk.ru / VK Mini App    api.vk.com       │
   │  oauth.vk.com (standalone)                    │
   │        ▲                     ▲               │
   │        │ user consent        │ wall.get      │
   │        │                     │ users.get     │
   │        │                     │ wall.repost   │
   └────────┼─────────────────────┼───────────────┘
            │                     │
   ┌────────▼─────────────────────▼───────────────┐
   │            FastAPI (single process)          │
   │                                              │
   │  /vkma         /api/vkma/*  /admin/*         │
   │  participant   bearer API   coordinator UI   │
   │  mini app                                    │
   │                                              │
   │  Host cron / manual CLI                      │
   │    tick — poll_source + run_due_tasks        │
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
not seventy, and removes the need for source-side push integration.

## Schema (phase 2 live)

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

CREATE TABLE source_config (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    vk_group_id INTEGER NOT NULL,        -- positive
    enabled INTEGER NOT NULL DEFAULT 1,
    last_seen_vk_post_id INTEGER,
    last_polled_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
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

## Participant onboarding flow (VK Mini App + standalone token paste)

```
Participant  VK Mini App      Server             VK
    │            │               │               │
    │ open app   │               │               │
    ├───────────>│ GET /vkma                     │
    │            ├──────────────>│ serve inline UI
    │            │<──────────────┤               │
    │ click      │               │               │
    │ "Open VK"  │ new tab oauth.vk.com/authorize
    │            ├──────────────────────────────>│
    │            │ approve standalone OAuth      │
    │            │<──────────────────────────────┤
    │ copy full  │ oauth.vk.com/blank.html#access_token=...
    │ URL        │               │               │
    │ paste URL  │ POST /api/vkma/token         │
    │ back       ├──────────────>│ users.get     │
    │            │               ├──────────────>│
    │            │               │<──────────────┤
    │            │               │ encrypt token │
    │            │               │ UPSERT users  │
    │            │               │ mint bearer   │
    │            │<──────────────┤               │
    │            │ localStorage[bearer]          │
    │            │ GET /api/vkma/status          │
    │            ├──────────────>│               │
    │            │<──────────────┤ connected     │
```

Why this shape:

- VK blocks `wall.repost` for both Web and VK Mini App profile tokens.
- VK explicitly returns `error_code=15`, `error_subcode=1134`,
  `Permission ... denied for non-standalone applications`.
- The only working path we found was a standalone-profile token, obtained via a
  legacy OAuth client (`Kate Mobile`).

Trade-off:

- the participant must copy one URL from `oauth.vk.com/blank.html` back into the
  mini app during onboarding;
- after that the system is fully automatic again: poller and worker do the rest.

## Source polling (phase 2)

Every 60 seconds:

1. `wall.get?owner_id=-vk_group_id&count=10`. Public groups can work without
   a token; if VK requires auth, use `VK_SERVICE_TOKEN` from env.
2. Keep posts whose `id > source_config.last_seen_vk_post_id`.
3. First poll only initializes the cursor and intentionally does not backfill
   historical posts.
4. For each new post, inside one transaction:
   - INSERT into `posts` (UNIQUE protects against retry dups).
   - For each user with `status='active'`:
     - `delay = clip(exp(gauss(μ=ln(3600), σ=0.7)), 60, 18000)`
     - INSERT into `repost_tasks` with `scheduled_at = max(published_at, now()) + delay`.
   - UPDATE `source_config.last_seen_vk_post_id`.
5. Commit.

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
claim the same row. Policy: run exactly one scheduler. The MVP recommendation
is host cron calling `python -m app.worker tick` once per minute.

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
| 15   | Access denied            | task → `failed` (`access_denied`)                              |
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
  the mini app onboarding link.
- No server-side VK token revocation. "Revoke" means local
  token wipe/overwrite, `status='revoked'`, and pending task cancellation.

## Delivery phases

- **Phase 1 (done):** auth onboarding, encrypted tokens at rest,
  minimal cabinet, smoke test.
- **Phase 2 (done):** source polling, log-normal scheduling, worker, error matrix.
- **Phase 3 (done):** admin UI, pause/resume/revoke, per-user repost history.
- **Phase 4 (done):** production deployment, minute cron tick, working VK Mini
  App onboarding, real end-to-end repost confirmed.
