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

7. Bootstrap the first platform admin (see **Platform Admin Console**
   below) — none of the app's seeds create one, and the superadmin
   console has nothing to log in with until you run this.

## Platform Admin Console

Platform staff (Wellstaq's own team, not organization customers) sign in
through a completely separate identity: `AdminUser`/`AdminSession`
(`app/models/admin_user.py`), not the org-facing `User` table. Their
tokens carry a `scope="admin"` claim and are rejected by every
org-facing route, and vice versa — an org user's token is rejected by
every `/app-admin/*` route. This is intentional: a staff account should
never be able to join or act inside a customer organization, and a
compromised org token should never grant console access.

Roles are `super_admin`, `admin`, and `support`, with the permission
matrix defined in `app/core/constants.py` (`ADMIN_ROLE_PERMISSIONS`).
`super_admin` implicitly has every permission and is the only role that
can manage other admins.

**There is no self-serve way to create the first admin** — inviting a
new admin (`POST /app-admin/admins/invite`) requires already being
logged in as an admin with `admins.manage`, which is a chicken-and-egg
problem for the very first one. Bootstrap it with:

```bash
python -m scripts.create_super_admin --email owner@wellstaq.com --password "..." --first-name Wellstaq --last-name Owner
```

Omit `--password` to be prompted for it instead of passing it on the
command line. If you're locked out later (forgotten password, or need
to recover after disabling an account), rotate the same account's
password instead of creating a duplicate:

```bash
python -m scripts.create_super_admin --email owner@wellstaq.com --reset-password
```

Every admin *after* this first one should go through the real invite
flow (the Admin Team page in the admin console, or
`POST /app-admin/admins/invite` directly) — that's what makes the audit
log able to show who invited whom. The bootstrap script has no acting
admin to attribute to, so the first admin's `invited_by` is left null.

Every write a staff member makes through `/app-admin/*` — suspending an
org, granting free access, creating a platform event, inviting or
removing another admin — is recorded in `audit_entries`
(`app/models/audit_entry.py`) with who did it, when, and (where
applicable) why. It's visible in the console's Audit Log page, or
directly via `GET /app-admin/audit-log` (supports `q`, `target_type`,
and `start_date`/`end_date` filters). Entries are never edited or
deleted, and `actor_admin_id` is nulled (not cascaded) if the acting
admin is later removed, so history survives staff turnover.

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

## Auth & scoping

There are two separate identities and auth stacks in this codebase —
see **Platform Admin Console** above for the second one.

For organization users (owners, managers, employees):

- `app/auth/security.py` — password hashing (bcrypt)
- `app/auth/jwt.py` — access/refresh token creation + verification,
  including the admin-scoped variants (`create_admin_access_token`,
  `create_admin_refresh_token`) used by the platform admin console
- `app/auth/dependencies.py` — `get_current_user`, the real dependency
  for anything touching org- or branch-scoped data. It loads the user
  row fresh from the database on every request (not just trusting the
  token's claims), which is also where an organization's suspension
  (`Organization.is_active`) is enforced — a suspended org's users are
  locked out immediately, not just at their next login
- `app/auth/admin_dependencies.py` — the platform-admin equivalent:
  `get_current_admin` and `require_admin_permission(permission)`
- `app/routers/auth.py` — `/auth/register`, `/auth/login`, 2FA,
  password reset, and session/device management for org users
- `app/routers/uploads.py` — `/uploads/presign`, protected, returns a
  short-lived presigned URL so the client uploads file bytes directly to
  S3/R2, never through this server

Role/org/branch scoping and permission checks for org-facing routes are
in `app/services/organization_roles.py` and `app/services/permission_service.py`.

## Running tests

Install the test dependencies into the same virtual environment (not
covered by `requirements.txt`, since they're dev-only):

```bash
python -m pip install -r requirements-dev.txt
```

Then, from the project root:

```bash
pytest
```

Tests run against a throwaway async SQLite database per test (see
`tests/conftest.py`'s `db_session`/`client` fixtures) — you do not need
Postgres, Redis, or a running API server to run the suite.

**Test layout convention**: every service, router, and model has its
own test file in the matching `tests/` subfolder — e.g.
`app/services/platform_admin/organization_service.py` is tested by
`tests/services/platform_admin/test_organization_service.py`, and
`app/routers/app_admin.py` by `tests/routers/test_app_admin.py`. Follow
this placement for any new service, router, or model.

## Background jobs (app/jobs/)

Job functions are organized by domain (`analytics/`, `challenges/`,
`events/`, `media/`, `notifications/`, `payment/`, `wellbeing/`), each
with its own task modules rather than one flat file. `shared.py` has
the common helpers (e.g. `get_job_session()`, used by one-off scripts
like `create_super_admin.py` to get a DB session outside a request).

`worker.py` — `WorkerSettings`: registers the on-demand job functions
and the cron schedule for periodic jobs (snapshots, subscription
expiry sweeps, survey window transitions, etc). Run with
`arq app.jobs.worker.WorkerSettings`.

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
├── auth/          password hashing, JWT, auth dependencies (org + admin)
│   ├── security.py
│   ├── jwt.py
│   ├── dependencies.py       get_current_user (org users)
│   └── admin_dependencies.py get_current_admin (platform admins)
├── core/          settings, database session, object storage, constants
├── models/        SQLAlchemy models — one module per domain/table
├── schemas/       Pydantic request/response models, one module per domain
├── services/      business logic, one package per domain. Services never
│                  commit — the router owns the transaction boundary
│   └── platform_admin/  admin auth, org suspend/comp, audit, admin team,
│                         platform events — see Platform Admin Console above
├── routers/       FastAPI route definitions, one module per domain
│   └── app_admin.py  all /app-admin/* platform-console routes
├── jobs/          ARQ background + scheduled jobs, organized by domain
└── main.py        app entry point — registers every router
alembic/           database migrations
scripts/           one-off CLI scripts: seeding, create_super_admin
tests/             mirrors app/ — tests/<models|routers|services>/...
