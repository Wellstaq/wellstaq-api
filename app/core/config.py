"""
Central application configuration.

All values are read from environment variables (or a local .env file
during development). Copy .env.example to .env and fill in real
values locally — never commit real secrets.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
   
    APP_NAME: str = "Wellstaq API"
    APP_ENV: str = "development"  # development | staging | production
    APP_PORT: int = 8000
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"

    #PRODUCTION PURPOSE
    #DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/wellstaq"

    # Auth / JWT
    # Generate a real secret with: openssl rand -hex 32
    JWT_SECRET_KEY: str = "CHANGE_ME_BEFORE_PRODUCTION"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── Redis (caching, leaderboards, trending, ARQ queue) ─
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Object storage (S3 or Cloudflare R2 — both speak the
    # same S3 API, so swapping providers is just changing
    # these values, not the code) ─────────────────────────
    STORAGE_ENDPOINT_URL: str = ""  # leave blank for AWS S3; set for R2, e.g. https://<account_id>.r2.cloudflarestorage.com
    STORAGE_REGION: str = "auto"  # "auto" for R2; real region (e.g. us-east-1) for S3
    STORAGE_ACCESS_KEY_ID: str = ""
    STORAGE_SECRET_ACCESS_KEY: str = ""
    STORAGE_BUCKET_NAME: str = "wellstaq-media"
    STORAGE_PUBLIC_BASE_URL: str = ""  # public CDN/base URL used to build media_url for clients
    PRESIGNED_URL_EXPIRE_SECONDS: int = 300  # 5 minutes to complete the direct upload

    # ── CORS ─────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    SENDGRID_API_KEY: str ="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"
    MAIL_FROM: str ="iyanue15@gmail.com"
    MAIL_FROM_NAME: str ="Wellstaq"


    # OAuth — Google/Apple verify against these
    GOOGLE_CLIENT_ID: str = ""
    APPLE_CLIENT_ID: str = ""  # your app's bundle id / services id

    # Permission cache TTL — belt-and-suspenders; explicit invalidation
    # on every role change makes this mostly irrelevant, but caps
    # staleness if an invalidation call is ever missed.
    PERMISSION_CACHE_TTL_SECONDS: int = 300


# Single shared settings instance — import this, never instantiate Settings() elsewhere
settings = Settings()
