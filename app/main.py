"""
Application entry point.

Run locally with:
    uvicorn app.main:app --reload --port 8000

Or simply:
    python -m app.main
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import engine
from app.models.base import Base
from app.routers import (
    auth, 
    general, 
    uploads, 
    departments,
    organizations,
    roles,
    branches,
    clubs,
    permissions,
    events,
    posts,
    hashtags, 
    chats,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic: Create database tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield
    
    # Shutdown logic (if you need to clean up resources later, put it here)


app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public + protected routers. Every future domain router (departments,
# clubs, events, posts, ...) gets included here the same way.
app.include_router(general.router)
app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(departments.router)
app.include_router(organizations.router)
app.include_router(roles.router)
app.include_router(branches.router)
app.include_router(clubs.router)
app.include_router(permissions.router)
app.include_router(events.router)
app.include_router(posts.router)
app.include_router(hashtags.router)
app.include_router(chats.router)

@app.get("/")
async def root():
    """Root route — simple hello response to confirm the app is up."""
    return {"message": "Hello from Wellstaq API"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.APP_PORT, reload=True)