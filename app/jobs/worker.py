"""
ARQ worker configuration.

Run with:
    arq app.jobs.worker.WorkerSettings

This single process handles both:
    - cron_jobs:   scheduled snapshot recomputation (leaderboard, kpi,
                   hashtag trends, department rank)
    - functions:   on-demand jobs enqueued from routes/services, e.g.
                   `await redis.enqueue_job("resize_thumbnail", object_key)`

The cron schedules below are starting points — tune the cron
expressions once you know real traffic patterns. hashtag trends are
deliberately on the tightest schedule (15 min) since trending is the
most time-sensitive of the four snapshot types; the others run hourly
as a reasonable default.
"""

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.jobs.tasks import (
    compute_department_rank_snapshots,
    compute_hashtag_trend_snapshots,
    compute_kpi_snapshots,
    compute_leaderboard_snapshots,
    fanout_notification,
    resize_thumbnail,
    send_invite_email,
)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    # On-demand functions — enqueued from routes/services by name.
    functions = [
        resize_thumbnail,
        send_invite_email,
        fanout_notification,
    ]

    # Scheduled functions — run automatically on the cron expression below.
    cron_jobs = [
        cron(compute_hashtag_trend_snapshots, minute={0, 15, 30, 45}),
        cron(compute_leaderboard_snapshots, hour=set(range(24)), minute=0),
        cron(compute_department_rank_snapshots, hour=set(range(24)), minute=5),
        cron(compute_kpi_snapshots, hour=0, minute=10),  # once daily
    ]
