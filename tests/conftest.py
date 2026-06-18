"""
Shared test fixtures.

Uses a separate in-memory SQLite database so tests never touch
the real app.db file. Each test session starts with a clean slate.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.core.database import get_db
from app.models.base import Base

# ── In-memory SQLite for tests — never touches app.db ────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,  # set True to debug SQL in tests
)

TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    expire_on_commit=False,
    autoflush=False,
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    """Create all tables once per test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Fresh DB session per test, rolled back after each test."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()  # ← keeps tests isolated


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