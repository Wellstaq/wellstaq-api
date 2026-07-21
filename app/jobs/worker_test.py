# app/jobs/worker_test.py
"""
TEMPORARY — manual testing only. Runs every job every minute so you can
watch them fire without waiting for real cron intervals. Never deploy
this; never point production at this file. Delete once satisfied.
"""

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.jobs.analytics.hashtag_trends import compute_hashtag_trend_snapshots
from app.jobs.analytics.department_rank import compute_department_rank_snapshots
from app.jobs.analytics.leaderboard import compute_leaderboard_snapshots
from app.jobs.analytics.engagement import compute_engagement_logs
from app.jobs.challenges.lifecycle import activate_and_close_challenges
from app.jobs.notifications.notification import fanout_notification
from app.jobs.notifications.email import send_invite_email
from app.jobs.media.thumbnails import resize_thumbnail
from app.jobs.analytics.leaderboard import (
    compute_leaderboard_snapshots_daily,
    compute_leaderboard_snapshots_weekly,
    compute_leaderboard_snapshots_monthly,
)

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    functions = [resize_thumbnail, send_invite_email, fanout_notification]

    # Every job, every minute — for manual testing only.
    cron_jobs = [
         cron(compute_leaderboard_snapshots_daily, minute=set(range(60))),
        cron(compute_leaderboard_snapshots_weekly,minute=set(range(60))),   # Mondays
        cron(compute_leaderboard_snapshots_monthly,minute=set(range(60))),   
        cron(compute_engagement_logs, minute=set(range(60))),
    ]