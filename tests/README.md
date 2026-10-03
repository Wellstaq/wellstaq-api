# Backend tests

Run from the backend project root with the project's virtual environment active:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

Unlike manual endpoint testing, this suite does **not** require Uvicorn,
`scripts.seed_all`, a running database, Redis, ngrok, or payment/email accounts.
Do not run production seeds to prepare these tests.

## Conventions and isolation

- Keep model tests under `tests/models/`, service tests under
  `tests/services/<domain>/`, and route tests under `tests/routers/`.
- Every source module gets a corresponding `test_*.py` module in the matching
  folder. Use the existing plural domain folders (`branch` -> `branches`,
  `event` -> `events`, and so on) consistently.
- Start each test module with its source path and behavior contract. Keep
  domain-only factories local; shared organization/user/authentication
  factories belong in `conftest.py`.
- Group cases by behavior with `Test...` classes: success, validation,
  not-found, conflict, authorization, tenant/scope isolation, persistence,
  and provider failures as applicable.
- Model tests exercise ORM persistence, defaults, constraints, relationships,
  cascades, and tenant integrity against the real in-memory database.
- Service tests call the real service with persisted rows and assert returned
  values plus database side effects. Mock only external providers such as
  email, Redis timing, payment gateways, push delivery, and object storage.
- Router tests make HTTP requests through `client`; they exercise real request
  validation, JWT/session authentication, role-derived permissions, service
  calls, transaction ownership, response schemas, and persisted side effects.
  Route-existence manifests and "status < 500" smoke tests are not substitutes
  for endpoint behavior tests.
- Use the shared `db_session`: in-memory SQLite with foreign keys enabled
  and an outer transaction/savepoint, including tests that call `commit()`.
  The test configuration overrides `DATABASE_URL` before importing settings.
- `make_org_with_owner(..., with_trial=False)` is for model tests or tests
  that create their own subscription. The default creates a trial for
  service tests that need an active plan. Do not create two subscriptions
  for one organization.
- Redis is a fresh deterministic `MemoryRedis` per test. Use
  `memory_redis.advance(seconds)` to test expiry without sleeping.
- External socket connections are blocked. Mock email, mobile push,
  payment-provider, and object-storage boundaries explicitly; use the
  real service logic, database queries, and JWTs whenever practical.
- Use `client` for HTTP tests; it overrides FastAPI's database dependency.
- Assert behavior and persisted results rather than implementation text.
  Older source-inspection contract tests remain as supplemental coverage.

Examples:

```bash
python -m pytest tests/services/auth -q
python -m pytest tests/services/notifications tests/services/events -q
python -m pytest tests/services/auth/test_role_access.py -q
python -m pytest tests/routers/test_branches.py -q
python -m pytest -q --junitxml=/tmp/wellstaq-tests.xml
python -m pytest -q --cov=app --cov-branch --cov-report=term-missing
```

## Current baseline and known limitations

The full suite baseline after the current refresh is **1,613 passed and 2
expected failures**. SQLAlchemy warnings are promoted to errors so ORM/database
cascade conflicts cannot pass unnoticed.

The two expected failures document SQLite limitations rather than accepted
application failures:

- the circular organization/owner bootstrap cannot express a deferred
  `NOT NULL` owner constraint in SQLite;
- SQLite does not reproduce PostgreSQL's owner-FK cascade across the deferred
  circular relationship.

PostgreSQL migration tests are still required before deployment for those two
contracts. External-provider sandbox tests remain separate from this isolated
unit/integration suite.

Router expansion is deliberately behavior-first. Completed matching HTTP
suites currently include branches, branch activity summary, activity log,
categories, storage, Paystack webhooks, and role protection. Continue adding
one complete source-matched router suite at a time; do not bulk-generate
placeholder tests.
