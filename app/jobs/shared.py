"""
app/jobs/shared.py

Job-system-level utilities — infrastructure plumbing shared across
every job, regardless of domain.

get_job_session() replaces the `async for session in get_db(): ... break`
pattern used throughout every job file. That pattern works, but it
borrows a FastAPI *request-scoped dependency generator* for a context
with no request lifecycle — the `break` is a workaround to force
single-use. This gives jobs a dedicated, clearly-named way to get one
session, used as a normal async context manager.

Reuses AsyncSessionLocal directly from app.core.database — the same
factory get_db() itself wraps — so job sessions are configured
identically to request sessions (same engine, same expire_on_commit
setting), just without the generator/dependency machinery FastAPI needs.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal


@asynccontextmanager
async def get_job_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session