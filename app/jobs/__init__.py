"""
Background and scheduled jobs live here, using ARQ rather than Celery —
see the original rationale preserved below.

Organized by domain, not lumped into one tasks.py:

  analytics/      — scheduled snapshot/score recomputation jobs
    engagement.py       — compute_engagement_logs
    leaderboard.py       — compute_leaderboard_snapshots
    department_rank.py   — compute_department_rank_snapshots
    hashtag_trends.py     — compute_hashtag_trend_snapshots
    kpi.py                — compute_kpi_snapshots

  challenges/     — challenge lifecycle jobs
    lifecycle.py          — activate_and_close_challenges

  notifications/  — on-demand notification jobs
    notification.py       — fanout_notification
    email.py               — send_invite_email

  media/          — on-demand media processing jobs
    thumbnails.py          — resize_thumbnail

  helpers/        — TRUE cross-domain shared utilities only. A helper
    lives here only once at least two different job domains actually
    need it — not speculatively. Job-specific helpers stay next to
    their owning task module (e.g. an engagement-only helper belongs in
    analytics/, not here), same pattern as services/*/​_helpers.py
    throughout the rest of the codebase.
    queries.py     — shared bulk-query builders (e.g. windowed
                     ActivityLog aggregation), once >1 job needs them
    scoring.py     — shared scoring primitives (e.g. 0-100 capping,
                     trend-direction comparison), once >1 job needs them
    dictionaries.py — shared enum/vocabulary constants, once >1 job
                      needs them

  worker.py       — WorkerSettings only: imports every task from its
    real location and registers it (functions list + cron_jobs). This
    file should never contain task logic itself.

  shared.py       — thin, job-system-level utilities that aren't
    domain-specific business logic (e.g. a get_job_db_session() wrapper
    if one's ever needed) — deliberately separate from helpers/, which
    is for business-logic helpers, not infrastructure plumbing.

Run the worker locally with:
    arq app.jobs.worker.WorkerSettings
"""