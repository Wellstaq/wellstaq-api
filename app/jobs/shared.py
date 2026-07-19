"""
app/jobs/shared.py

Job-system-level utilities — infrastructure plumbing shared across every
job, regardless of domain. NOT for business-logic helpers (those belong
in helpers/ once genuinely shared, or next to their owning task module
if domain-specific) — this file is deliberately kept to things like
session acquisition, not scoring formulas or query builders.

Currently empty — every existing task acquires its own session inline
via `async for session in get_db(): ... break`, matching the pattern
established in scripts/seed_permissions.py. If that pattern gets
duplicated enough times to be worth centralizing, it lands here.
"""