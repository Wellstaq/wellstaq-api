# Wellstaq API

FastAPI backend for the Wellstaq corporate wellness platform.

The `scripts/` folder seeds platform defaults, including roles, permissions,
categories, wellbeing lookups, and subscription plans.

## Stack

- FastAPI
- SQLAlchemy 2.0 (async, asyncpg driver — the fastest async Postgres driver in Python)
- PostgreSQL
- Alembic (migrations)
- Pydantic v2 (request/response validation — FastAPI is built around this)
- Redis (caching for leaderboards/trending, and as the ARQ job queue)
- ARQ (asyncio-native background jobs and cron-style scheduling — chosen
  over Celery because it's async-first and reuses the same Redis already
  needed for caching, rather than bolting on separate broker infrastructure)
- aioboto3 (async S3/R2 client — plain boto3's sync calls would block the
  whole event loop, not just one request, if called from an async route)
- Pillow (image resizing, run inside a background job, never inline on
  the upload request)
- JWT auth (python-jose + passlib), since FastAPI has no built-in auth

## Setup

Run all commands below from the project root (the directory containing
`app/`, `scripts/`, and `requirements.txt`). The development startup order is:
install dependencies → configure environment and database → start the API →
run the seeds → use the application endpoints.

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv venv
   source venv/bin/activate   # on Windows: venv\Scripts\activate
   python -m pip install -r requirements.txt
   ```

2. Create `.env` using `.env.example` as a guideline, unless you already
   have a configured `.env`. Do not overwrite an existing configuration.
   Set the environment variables for your environment, including
   `DATABASE_URL`, `REDIS_URL`, a private `JWT_SECRET_KEY`, and the storage,
   email, and payment credentials needed for the features you will test.
   The Paystack seed requires `PAYSTACK_SECRET_KEY`; add it to `.env`
   even if it is not listed in the example. Never commit `.env` or real keys.

   Before starting the API, finish setting up the database, apply the
   project's database migrations, and ensure the configured database and
   Redis services are available. Seeding supplies default records; it is
   not a replacement for database/schema setup.

3. Start the API for local development/testing and leave it running:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

   You can change `--port` to an available port of your choice. To listen
   only on your own machine, use `--host 127.0.0.1` instead of `0.0.0.0`.
   `0.0.0.0` listens on all network interfaces; open
   `http://localhost:8000` or `http://127.0.0.1:8000` in your browser.
   Adjust URLs and tunnel ports if you select a different port.
   `--reload` is for development, not production.

4. Once the API is running, open a second terminal, activate the same
   virtual environment, and run this command from the project root:

   ```bash
   python -m scripts.seed_all
   ```

   This runs the permissions/roles, categories, wellbeing, subscription
   plans, and Paystack plan seeds in dependency order. Wait for
   `All seed scripts completed successfully` before using the application
   endpoints. If any seed fails, fix the reported error and rerun the
   command. Keep the API running in the first terminal.

5. Run the background worker in another terminal with the same environment
   activated (requires Redis):

   ```bash
   arq app.jobs.worker.WorkerSettings
   ```

6. After successful seeding, access the API:
   - `http://localhost:8000/` — hello-world root route
   - `http://localhost:8000/health` — public health check
   - `http://localhost:8000/docs` — interactive Swagger UI

### Local Paystack testing and webhooks

The Paystack plan seed makes outbound requests to Paystack. It can run
locally with internet access and a valid `PAYSTACK_SECRET_KEY`; it does
not itself require a public address. Receiving payment webhooks is the
part that requires a public URL: Paystack cannot reach your machine's
`localhost` or `127.0.0.1` address.

For local payment testing, keep Uvicorn running and expose its port with
ngrok in a separate terminal:

```bash
ngrok http 8000
```

Configure the webhook URL in your Paystack dashboard for the matching
test/live environment as:

```text
https://<your-ngrok-domain>/webhooks/paystack
```

Keep both Uvicorn and ngrok running while testing payments so the backend
can receive Paystack's payment events. Update the configured webhook URL
whenever your ngrok URL changes. Use test credentials for local tests;
do not disable webhook signature verification. The tunnel exposes the
API port publicly, not just the webhook path, so use it only as needed
and do not expose sensitive development endpoints unnecessarily.

## Auth & scoping status

The auth *plumbing* is fully wired, including the role/org/branch
scoping layer:

- `app/auth/security.py` — password hashing (bcrypt)
- `app/auth/jwt.py` — access/refresh token creation + verification.
  `create_access_token` accepts `extra_claims` — login should pass
  `role`, `organization_id`, and `branch_id` here once it's implemented
- `app/auth/dependencies.py` — `get_current_user_id`, a bare-bones
  dependency for routes that only need "is this person logged in"
- `app/auth/scoping.py` — the real one to use for anything touching
  org- or branch-scoped data (most routes, given 40+ tables touched by
  this isolation):
  - `get_current_user` → returns a `CurrentUser` (user_id, role,
    organization_id, branch_id) resolved from the token
  - `require_role(minimum_role)` → dependency factory enforcing
    **hierarchical** role checks (`overall_manager > branch_manager >
    employee` — an overall_manager passes a branch_manager-only check)
  - `require_same_organization` / `require_same_branch_or_org_level` →
    call these inside a route/service after loading a target record, to
    block cross-org or cross-branch access even with a guessed valid UUID
- `app/routers/auth.py` — `/auth/register` and `/auth/login` (currently
  `501 Not Implemented` until the User model and database exist)
- `app/routers/general.py` — `/me` (any authenticated user) and
  `/admin-only-example` (requires at least `branch_manager`) demonstrate
  the pattern to copy for every future protected route
- `app/routers/uploads.py` — `/uploads/presign`, protected, returns a
  short-lived presigned URL so the client uploads file bytes directly to
  S3/R2, never through this server

**TODO flagged inline in `scoping.py`:** `get_current_user` currently
trusts the token's claims directly. Once `app/models/users.py` exists,
swap it to load the user row from the database by id instead, so a role
change or branch transfer takes effect immediately rather than only
after the user's current token expires.

Nothing in `app/models/`, `app/schemas/`, or `app/services/` is
implemented yet — those folders are intentionally empty stubs, ready to
be filled in domain by domain (organizations → users → everything else).

## Background jobs (app/jobs/)

- `tasks.py` — task function stubs: four scheduled snapshot jobs
  (`compute_leaderboard_snapshots`, `compute_kpi_snapshots`,
  `compute_hashtag_trend_snapshots`, `compute_department_rank_snapshots`)
  plus three on-demand jobs (`resize_thumbnail`, `send_invite_email`,
  `fanout_notification`)
- `worker.py` — `WorkerSettings`: registers the on-demand functions and
  the cron schedule for the periodic snapshot jobs. Run with
  `arq app.jobs.worker.WorkerSettings`

## Object storage (app/core/storage.py)

`generate_presigned_upload_url(object_key, content_type)` is the core
function — it returns a short-lived URL the client PUTs file bytes to
directly, plus the public URL to store on the relevant row (e.g.
`posts.media_url`). Works against AWS S3 or Cloudflare R2 unmodified;
only the `.env` values differ, since both speak the same S3 API. R2 is
worth it once the feed is serving images constantly, since it has no
egress fees.

Thumbnail generation should never happen inline in the upload request —
enqueue `resize_thumbnail` as an ARQ job right after the client confirms
the upload completed.

## Project layout

```
app/
├── auth/          JWT + password hashing + role/org/branch scoping
│   ├── security.py       password hashing
│   ├── jwt.py             token create/decode
│   ├── dependencies.py    bare user-id-only auth dependency
│   └── scoping.py         CurrentUser, get_current_user, require_role
├── core/          settings, database session, object storage client
│   ├── config.py
│   ├── database.py
│   └── storage.py         aioboto3 presigned URLs (S3/R2)
├── models/        SQLAlchemy models (empty stubs for now)
├── schemas/       Pydantic request/response models (empty stubs for now)
├── services/      business logic (empty stubs for now)
├── routers/       FastAPI route definitions
│   ├── auth.py
│   ├── general.py
│   └── uploads.py         presigned upload URL endpoint
├── jobs/          ARQ background + scheduled jobs
│   ├── tasks.py
│   └── worker.py
└── main.py        app entry point
alembic/           database migrations
