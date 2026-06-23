"""
Shared test fixtures.

Uses a separate in-memory SQLite database so tests never touch
the real app.db file. Each test session starts with a clean slate.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.main import app
from app.core.database import get_db
from app.models.base import Base
from app.models import Organization, User

# ── In-memory SQLite for tests — never touches app.db ────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,  # set True to debug SQL in tests
)

# ── Enable FK enforcement — SQLite skips this by default ─────────────────────
# Without this, FK violations silently pass instead of raising IntegrityError.
# PostgreSQL enforces FKs automatically; this pragma brings SQLite in line.
@event.listens_for(test_engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    expire_on_commit=False,
    autoflush=False,
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    """Create all tables once per test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    yield
    async with test_engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("PRAGMA foreign_keys=ON"))

@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Fresh DB session per test, rolled back after each test."""
    async with TestSessionLocal() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        yield session
        await session.rollback()

@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """
    HTTP test client that uses the test DB session.
    Overrides the real get_db dependency with the test one.
    """
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ── Shared model factories ────────────────────────────────────────────────────
#
# User.organization_id is NOT NULL, and Organization.owner_id requires a
# User to exist first — a circular FK by design (see Organization model
# docstring). Every test that creates a User must resolve this the same
# way the real signup transaction does:
#   1. create Organization with owner_id deferred (None) -> flush to
#      obtain org.id without committing
#   2. create the owner User with that organization_id (now satisfiable)
#   3. patch org.owner_id = user.id -> flush
#
# This used to be copy-pasted per test file (test_organization.py,
# test_user.py, test_wellbeings.py each had their own slightly different
# version) which is exactly how test_wellbeings.py's setup_org() was left
# behind when User.organization_id changed from nullable to NOT NULL.
# Centralizing it here means a future constraint change only needs to be
# fixed in one place.
#
# make_user() is exposed separately (not just as part of
# make_org_with_owner) because tests frequently need additional,
# non-owner members of an already-created org.

def make_user(organization_id: str, **overrides) -> User:
    """Build a User instance (not yet persisted) belonging to the given
    organization. Use for any user other than an org's first/owner user
    — for the owner, use make_org_with_owner() instead, since the owner
    and the org must be created together to resolve the circular FK."""
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        role_name="employee",
        hashed_password="$2b$12$fakehash",
    )
    return User(organization_id=organization_id, **{**defaults, **overrides})


async def make_org_with_owner(
    db_session: AsyncSession,
    *,
    org_kwargs: dict | None = None,
    user_kwargs: dict | None = None,
) -> tuple[Organization, User]:
    """Creates an Organization + its owner User, resolving the circular
    FK the same way the real signup transaction does. Returns
    (organization, owner_user).

    org_kwargs: overrides for Organization (business_name, slug, etc.)
    user_kwargs: overrides for the owner User (email, role_name, etc.)
    """
    org_kwargs = dict(org_kwargs or {})
    user_kwargs = dict(user_kwargs or {})

    org_defaults = dict(business_name="Acme Corp", slug="acme-corp")
    org = Organization(owner_id=None, **{**org_defaults, **org_kwargs})
    db_session.add(org)
    await db_session.flush()  # org.id now exists; owner_id still None

    owner_defaults = dict(role_name="overall_manager", email="owner@example.com")
    owner = make_user(organization_id=org.id, **{**owner_defaults, **user_kwargs})
    db_session.add(owner)
    await db_session.flush()

    org.owner_id = owner.id
    await db_session.flush()

    return org, owner