# app/jobs/worker_test.py
"""
TEMPORARY — manual testing only. Runs every job every minute so you can
watch them fire without waiting for real cron intervals. Never deploy
this; never point production at this file. Delete once satisfied.

Survey lifecycle testing note: open_survey_windows only opens a window
if an org has an active WellbeingSurveyConfig AND no current open
window exists — after the first successful run creates one, subsequent
ticks will no-op (nothing new to open) until you manually close/expire
it. close_survey_windows only closes a window once its window_end date
has passed — with a real weekly/biweekly/monthly cadence, that's days
out, so it won't fire meaningfully within a quick test unless you
manually set a window's window_end to a past date in the DB to force
it through.
"""

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.jobs.analytics.hashtag_trends import compute_hashtag_trend_snapshots
from app.jobs.analytics.department_rank import compute_department_rank_snapshots
from app.jobs.analytics.engagement import compute_engagement_logs
from app.jobs.analytics.leaderboard import (
    compute_leaderboard_snapshots_daily,
    compute_leaderboard_snapshots_weekly,
    compute_leaderboard_snapshots_monthly,
)
from app.jobs.challenges.lifecycle import activate_and_close_challenges
from app.jobs.notifications.notification import fanout_notification
from app.jobs.notifications.email import send_invite_email
from app.jobs.media.thumbnails import resize_thumbnail
from app.jobs.wellbeing.window_lifecycle import close_survey_windows, open_survey_windows


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    functions = [resize_thumbnail, send_invite_email, fanout_notification]

    # Every job, every minute — for manual testing only.
    cron_jobs = [
        # cron(compute_leaderboard_snapshots_daily, minute=set(range(60))),
        # cron(compute_leaderboard_snapshots_weekly, minute=set(range(60))),
        # cron(compute_leaderboard_snapshots_monthly, minute=set(range(60))),
        # cron(compute_engagement_logs, minute=set(range(60))),
        cron(open_survey_windows, minute=set(range(60))),
        cron(close_survey_windows, minute=set(range(60))),
    ]