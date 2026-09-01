"""
Central application configuration.

All values are read from environment variables (or a local .env file
during development). Copy .env.example to .env and fill in real
values locally — never commit real secrets.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ADMIN_API_KEY: str = ""
   
    APP_NAME: str = "Wellstaq API"
    APP_ENV: str = "development"  # development | staging | production
    APP_PORT: int = 8000
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = ""


    # Auth / JWT
    # Generate a real secret with: openssl rand -hex 32
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── Redis (caching, leaderboards, trending, ARQ queue) ─
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Object storage (S3 or Cloudflare R2 — both speak the
    # same S3 API, so swapping providers is just changing
    # these values, not the code) ─────────────────────────
    STORAGE_ENDPOINT_URL: str = ""  # leave blank for AWS S3; set for R2, e.g. https://<account_id>.r2.cloudflarestorage.com
    STORAGE_REGION: str = ""
    STORAGE_ACCESS_KEY_ID: str = ""
    STORAGE_SECRET_ACCESS_KEY: str = ""
    STORAGE_BUCKET_NAME: str = ""
    STORAGE_PUBLIC_BASE_URL: str = ""  # public CDN/base URL used to build media_url for clients
    PRESIGNED_URL_EXPIRY_SECONDS: int = 300  # 5 minutes to complete the direct upload
    STORAGE_CDN_DOMAIN: str = ""

    # ── CORS ─────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "https://appwellstaq6.vercel.app"]
    PUBLIC_APP_ORIGIN: str = "http//localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    SMTP_HOST: str = "smtp-relay.brevo.com"
    SMTP_PORT: int = 2525
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    SMTP_TIMEOUT_SECONDS: float = 30.0
    MAIL_FROM: str = ""
    MAIL_FROM_NAME: str = "Wellstaq"


    # OAuth — Google/Apple verify against these
    GOOGLE_CLIENT_ID: str = ""
    APPLE_CLIENT_ID: str = ""  # your app's bundle id / services id

    # Permission cache TTL — belt-and-suspenders; explicit invalidation
    # on every role change makes this mostly irrelevant, but caps
    # staleness if an invalidation call is ever missed.
    PERMISSION_CACHE_TTL_SECONDS: int = 300

    #payment gateway
    PAYSTACK_SECRET_KEY: str=""
    PAYSTACK_PUBLIC_KEY: str=""
    PAYMENT_PENDING_EXPIRY_MINUTES: int = Field(default=60, ge=5, le=1440)
    PAYMENT_WEBHOOK_MAX_BODY_BYTES: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    PAYMENT_CALLBACK_ALLOWED_ORIGINS: list[str] = []


# Single shared settings instance — import this, never instantiate Settings() elsewhere
settings = Settings()
