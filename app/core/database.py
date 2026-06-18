"""
Database engine and session setup, using SQLAlchemy 2.0 async style
with the asyncpg driver.

Import `get_db` as a FastAPI dependency wherever a route needs DB access:

    from app.core.database import get_db

    @router.get("/things")
    async def list_things(db: AsyncSession = Depends(get_db)):
        ...
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session and always closes it."""
    async with AsyncSessionLocal() as session:
        yield session
