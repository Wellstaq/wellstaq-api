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

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.database import engine
from app.core.limiter import limiter
from app.models.base import Base
from app.routers import (
    auth, 
    departments,
    organizations,
    roles,
    branches,
    branch_activities,
    clubs,
    permissions,
    events,
    posts,
    hashtags,
    stories, 
    chats,
    notifications,
    challenges,
    activities,
    activity_logs,
    leaderboards,
    wellbeings,
    wellbeing_surveys,
    categories,
    engagement_logs,
    engagements,
    storages,
    kpi_snapshots,
    targets,
    dashboards,
    department_ranks,
    support_tickets,
    integrations,   
   
    
)
from app.routers.payments import(
    router as payment_router,
    webhook_router as payment_webhook_router,
    plans_router as available_subscription_router,
)

from app.routers.user_settings import(
    router as update_user_profile,
    auth_password_router as password_update,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    
    # Shutdown logic (if you need to clean up resources later, put it here)


app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# Rate limiting (slowapi) — app-level, identity-aware layer.
# Sits behind Nginx's IP-based limiting; see app/core/limiter.py.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public + protected routers. Every future domain router (departments,
# clubs, events, posts, ...) gets included here the same way.

app.include_router(auth.router)
app.include_router(departments.router)
app.include_router(organizations.router)
app.include_router(roles.router)
app.include_router(branches.router)
app.include_router(branch_activities.router)
app.include_router(clubs.router)
app.include_router(permissions.router)
app.include_router(events.router)
app.include_router(posts.router)
app.include_router(hashtags.router)
app.include_router(stories.router)
app.include_router(chats.router)
app.include_router(notifications.router)
app.include_router(challenges.router)
app.include_router(activities.router)
app.include_router(activity_logs.router)
app.include_router(leaderboards.router)
app.include_router(wellbeings.router)
app.include_router(wellbeing_surveys.router)
app.include_router(categories.router)
app.include_router(engagements.router)
app.include_router(engagement_logs.router)
app.include_router(storages.router)
app.include_router(targets.router)
app.include_router(dashboards.router)
app.include_router(department_ranks.router)
app.include_router(kpi_snapshots.router)
app.include_router(support_tickets.router)
app.include_router(integrations.router)
app.include_router(payment_router)
app.include_router(payment_webhook_router)
app.include_router(available_subscription_router)
app.include_router(update_user_profile)
app.include_router(password_update)


@app.get("/")
async def root():
    """Root route — simple hello response to confirm the app is up."""
    return {"message": "Hello from Wellstaq API"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.APP_PORT, reload=True)
