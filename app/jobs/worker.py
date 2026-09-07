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
from app.jobs.analytics.wellbeing_score import compute_wellbeing_scores
from app.jobs.analytics.kpi_snapshot import compute_kpi_snapshots
from app.jobs.payment.subscription_lifecycle import expire_subscriptions
from app.jobs.analytics.leaderboard import (
    compute_leaderboard_snapshots_daily,
    compute_leaderboard_snapshots_weekly,
    compute_leaderboard_snapshots_monthly,
)
from app.jobs.analytics.engagement import compute_engagement_logs
from app.jobs.challenges.lifecycle import activate_and_close_challenges, compute_challenge_stats_snapshots
from app.jobs.events.lifecycle import activate_scheduled_events
from app.jobs.notifications.notification import fanout_notification
from app.jobs.notifications.email import send_invite_email
from app.jobs.media.thumbnails import resize_thumbnail
from app.jobs.media.story_purge import purge_expired_stories
from app.jobs.wellbeing.window_lifecycle import close_survey_windows, open_survey_windows
from app.jobs.wellbeing.pulse_snapshot import compute_pulse_snapshots
from app.jobs.payment.stale_transactions import expire_stale_pending_transactions
from app.jobs.media.asset_sweep import sweep_pending_media_assets, sweep_orphaned_media_assets


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    functions = [
        resize_thumbnail,
        send_invite_email,
        fanout_notification,
        compute_pulse_snapshots,  # on-demand, takes window_id — triggered by window_lifecycle, not scheduled
    ]

    cron_jobs = [
        #hastags
        cron(compute_hashtag_trend_snapshots, minute={0, 15, 30, 45}),

        #leaderboards
        cron(compute_leaderboard_snapshots_daily, hour=set(range(24)), minute=0),
        cron(compute_leaderboard_snapshots_weekly, weekday=0, hour=1, minute=0),   # Mondays
        cron(compute_leaderboard_snapshots_monthly, day=1, hour=2, minute=0),        # 1st of month

        #department
        cron(compute_department_rank_snapshots, hour=set(range(24)), minute=5),

        #challenge
        cron(activate_and_close_challenges, hour=set(range(24)), minute=0),
        cron(compute_challenge_stats_snapshots, hour=3, minute=0),  # once daily, after midnight rollover

        # events (completion remains a manual organizer action)
        cron(activate_scheduled_events, hour=set(range(24)), minute=0),

        #engagements
        cron(compute_engagement_logs, hour=1, minute=0),

        #wellbeing analytics
        cron(compute_wellbeing_scores, hour=3, minute=15),   # once daily
        cron(compute_kpi_snapshots, hour=3, minute=30),      # once daily

        #posts and stories
        cron(purge_expired_stories, minute={0, 15, 30, 45}),

        #payments
        cron(expire_subscriptions, hour=set(range(24)), minute=0),
        cron(expire_stale_pending_transactions, hour=set(range(24)), minute=30),  # hourly

        #wellbeing survey
        cron(open_survey_windows, hour=6, minute=0),   # once daily, opens windows whose turn has come
        cron(close_survey_windows, hour=23, minute=55), # once daily, closes windows whose window_end has passed, triggers snapshot

        #media assets
        cron(sweep_pending_media_assets, hour=set(range(24)), minute=20),   # hourly, catches abandoned uploads
        cron(sweep_orphaned_media_assets, hour=4, minute=0),                # once daily, catches delete-path misses
    ]
