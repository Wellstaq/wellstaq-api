"""
ARQ worker configuration.

Run with:
    arq app.jobs.worker.WorkerSettings
"""

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.jobs.analytics.hashtag_trends import compute_hashtag_trend_snapshots
from app.jobs.analytics.department_rank import compute_department_rank_snapshots
from app.jobs.payment.subscription_lifecycle import expire_trials
from app.jobs.analytics.leaderboard import (
    compute_leaderboard_snapshots_daily,
    compute_leaderboard_snapshots_weekly,
    compute_leaderboard_snapshots_monthly,
)
from app.jobs.analytics.engagement import compute_engagement_logs
from app.jobs.challenges.lifecycle import activate_and_close_challenges
from app.jobs.notifications.notification import fanout_notification
from app.jobs.notifications.email import send_invite_email
from app.jobs.media.thumbnails import resize_thumbnail
from app.jobs.media.story_purge import purge_expired_stories

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    functions = [
        resize_thumbnail,
        send_invite_email,
        fanout_notification,
    ]

    cron_jobs = [
        cron(compute_hashtag_trend_snapshots, minute={0, 15, 30, 45}),
        cron(compute_leaderboard_snapshots_daily, hour=set(range(24)), minute=0),
        cron(compute_leaderboard_snapshots_weekly, weekday=0, hour=1, minute=0),   # Mondays
        cron(compute_leaderboard_snapshots_monthly, day=1, hour=2, minute=0),        # 1st of month
        cron(compute_department_rank_snapshots, hour=set(range(24)), minute=5),
        cron(activate_and_close_challenges, hour=set(range(24)), minute=0),
        cron(compute_engagement_logs, hour=1, minute=0),
        cron(purge_expired_stories, minute={0, 15, 30, 45}),
        cron(expire_trials, hour=set(range(24)), minute=0)
    ]