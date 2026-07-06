"""
Shared test fixtures.

Uses a separate in-memory SQLite database so tests never touch
the real app.db file. Each test session starts with a clean slate.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.main import app
from app.core.database import get_db
from app.models.base import Base
from app.models import Branch, Organization, Role, User

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
# User.organization_id is NOT NULL, User.branch_id is NOT NULL, and
# Organization.owner_id requires a User to exist first — a circular FK
# by design (see Organization model docstring). Every test that creates
# a User must resolve this the same way the real signup transaction does:
#   1. Create Organization with owner_id deferred (None) → flush to
#      obtain org.id without committing.
#   2. Create the Headquarters Branch with that organization_id → flush
#      to obtain hq_branch.id. Every org has exactly one HQ branch;
#      "org-wide" positions like super_admin are simply members of HQ.
#   3. Create the owner User with organization_id + branch_id=hq.id → flush.
#   4. Patch org.owner_id = user.id → flush.
#
# make_user() is exposed separately for tests that need additional,
# non-owner members of an already-created org. Callers must supply
# branch_id explicitly — there is no sensible default branch for a
# generic member (any branch in the org is valid).

# ── Role resolution ────────────────────────────────────────────────────────
#
# User.role_id is a FK to roles.id. System roles are platform-seeded,
# read-only, and shared across all orgs (is_system=True,
# organization_id=NULL, branch_id=NULL).
#
# The test DB has no seed data loaded, so get_or_create_system_role()
# fetches a system Role by name if it already exists in this session,
# or creates it on first use. Tests don't need to know whether the role
# row already existed.
#
# This only ever creates/reuses SYSTEM roles. Tests that need a
# branch-scoped custom Role should construct it explicitly.

async def get_or_create_system_role(db_session: AsyncSession, name: str) -> Role:
    """Fetch a system Role by name, creating it if this test session
    hasn't needed it yet. System roles are org-agnostic
    (organization_id=NULL, branch_id=NULL, is_system=True)."""
    result = await db_session.execute(
        select(Role).where(
            Role.name == name,
            Role.organization_id.is_(None),
            Role.branch_id.is_(None),
        )
    )
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(
            name=name,
            organization_id=None,
            branch_id=None,
            is_system=True,
            # is_unique and unique_scope use model defaults (False / None).
            # Tests that need specific uniqueness semantics should set them
            # explicitly on the role after creation.
        )
        db_session.add(role)
        await db_session.flush()
    return role


def make_branch(organization_id: str, name: str = "Headquarters", **overrides) -> Branch:
    """Build a Branch instance (not yet persisted) belonging to the given
    organization. Defaults to name="Headquarters" — the auto-created branch
    every org gets at signup. Pass a different name for additional branches."""
    return Branch(organization_id=organization_id, name=name, **overrides)


def make_user(organization_id: str, **overrides) -> User:
    """Build a User instance (not yet persisted) belonging to the given
    organization.

    branch_id is required (User.branch_id is NOT NULL). Callers must
    always supply it via overrides — there is no sensible default.
    Omitting it will produce a User with branch_id=None that fails at
    flush time with IntegrityError, which is the correct outcome.

    For the owner user of a new org, use make_org_with_owner() instead,
    since owner + org + HQ branch must be created together to resolve
    the circular FK.

    For non-owner members, obtain the branch_id from the context:
        org, owner = await make_org_with_owner(db_session, ...)
        member = make_user(org.id, email="...", branch_id=owner.branch_id)
    """
    defaults = dict(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        hashed_password="$2b$12$fakehash",
    )
    return User(organization_id=organization_id, **{**defaults, **overrides})

async def make_user_with_role(
    db_session: AsyncSession,
    organization_id: str,
    *,
    role_name: str = "employee",
    **overrides,
) -> User:
    if "role_id" not in overrides:
        role = await get_or_create_system_role(db_session, role_name)
        overrides["role_id"] = role.id
    if "branch_id" not in overrides:
        raise ValueError(
            "make_user_with_role() requires branch_id. "
            "Use owner.branch_id from make_org_with_owner()."
        )
    return make_user(organization_id=organization_id, **overrides)

    
async def make_org_with_owner(
    db_session: AsyncSession,
    *,
    org_kwargs: dict | None = None,
    user_kwargs: dict | None = None,
) -> tuple[Organization, User]:
    """Creates an Organization + Headquarters Branch + owner User,
    resolving the circular FK the same way the real signup transaction
    does. Returns (organization, owner_user).

    The owner's branch_id is always set to the HQ branch. Tests that
    need to create additional members of the same org can retrieve the
    HQ branch id via owner.branch_id:

        org, owner = await make_org_with_owner(db_session)
        member = make_user(org.id, email="...", branch_id=owner.branch_id)

    org_kwargs:  overrides for Organization (business_name, slug, etc.)
    user_kwargs: overrides for the owner User (email, role_name, etc.)
        role_name is resolved to a system Role's id via
        make_user_with_role(). Pass role_id directly if the owner needs
        a non-system Role.
    """
    org_kwargs = dict(org_kwargs or {})
    user_kwargs = dict(user_kwargs or {})

    # ── Step 1: org (owner_id deferred) ──────────────────────────────────────
    org_defaults = dict(business_name="Acme Corp", slug="acme-corp")
    org = Organization(owner_id=None, **{**org_defaults, **org_kwargs})
    db_session.add(org)
    await db_session.flush()  # org.id available; owner_id still None

    # ── Step 2: HQ branch ─────────────────────────────────────────────────────
    # Every org gets exactly one Headquarters branch at creation. There is no
    # "org-wide / no branch" state for users — org-level roles like super_admin
    # simply live in HQ.
    hq_branch = make_branch(organization_id=org.id, name="Headquarters")
    db_session.add(hq_branch)
    await db_session.flush()  # hq_branch.id available

    # ── Step 3: owner user ─────────────────────────────────────────────────────
    owner_defaults = dict(role_name="overall_manager", email="owner@example.com")
    merged = {**owner_defaults, **user_kwargs}
    role_name = merged.pop("role_name", "overall_manager")

    # branch_id defaults to HQ; tests may override by passing branch_id in
    # user_kwargs (e.g. to assign the owner to a different pre-existing branch).
    kwargs = {"branch_id": hq_branch.id, **merged}

    owner = await make_user_with_role(
        db_session,
        organization_id=org.id,
        role_name=role_name,
        **kwargs,
    )
    db_session.add(owner)
    await db_session.flush()

    # ── Step 4: patch circular FK ─────────────────────────────────────────────
    org.owner_id = owner.id
    await db_session.flush()

    return org, owner