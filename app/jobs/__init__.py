"""
Background and scheduled jobs live here, using ARQ rather than
Celery — ARQ is asyncio-native, so it fits naturally into an async
FastAPI codebase without the sync/async impedance mismatch Celery
introduces, and it reuses the same Redis connection already needed
for caching (leaderboards, trending hashtags), rather than requiring
separate broker infrastructure.

Two kinds of jobs live here:

Scheduled (cron-like, run automatically — see worker.py cron_jobs):
    leaderboard_snapshots      — recompute rankings periodically
    kpi_snapshots              — recompute org KPIs periodically
    hashtag_trend_snapshots     — recompute trending hashtags every 15-60 min
    department_rank_snapshots  — recompute department rankings periodically

On-demand (enqueued from a route, run once, asynchronously):
    resize_thumbnail            — Pillow-based resize after a feed image upload
    send_invite_email           — fired when an organization_invites row is created
    fanout_notification         — write notifications rows for many recipients at once

Files:
    worker.py    — ARQ WorkerSettings: registers every task + cron schedule
    tasks.py     — the actual task functions (stubs for now)

Run the worker locally with:
    arq app.jobs.worker.WorkerSettings
"""

