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
- Name test modules `test_*.py` so pytest collects them. The survey tests
  are now `tests/models/test_wellbeing_surveys.py` (previously uncollected).
- Follow the existing async pytest conventions, local factories, and
  `Test...` groups for happy paths, validation, and authorization cases.
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
python -m pytest -q --junitxml=/tmp/wellstaq-tests.xml
```

## Refresh scope and known failures

This refresh updates stale role names, scoped role/permission fixtures,
subscription fixtures, club fields and membership results, event results
and invitation behavior, OTP storage, and challenge progress/notification
tests. Added behavior coverage includes role replacement and session
revocation, OTP expiry/reuse/attempt limits, notification preferences and
recipient isolation, password reset/reuse, and initial Overview membership.

The following remain deliberately failing, not skipped or marked expected:

- Three model tests require a custom role to have an organization. The
  current database model accepts tenant-less custom roles. These identify
  a missing persistence invariant; they do not by themselves prove an
  authenticated endpoint permits cross-organization access.
- `test_dashboard_session_requires_overview_permission[custom_viewer-False]`
  detects the deferred registration bug: a custom role without `overview`
  still receives tokens because registration checks the role name.

Existing expected failures remain unchanged. Application code, migrations,
and development data were not altered to silence these results. Fixing the
remaining application behavior is separate work.

SQLite tests do not replace PostgreSQL migration/constraint verification or
provider sandbox integration tests. This is targeted regression coverage,
not a claim that every route and external integration is covered.
