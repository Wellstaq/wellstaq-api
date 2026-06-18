"""
Application entry point.

Run locally with:
    uvicorn     .main:app --reload --port 8000

Or simply:
    python -m app.main
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import auth, general, uploads

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
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


@app.get("/")
async def root():
    """Root route — simple hello response to confirm the app is up."""
    return {"message": "Hello from Wellstaq API"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.APP_PORT, reload=True)
