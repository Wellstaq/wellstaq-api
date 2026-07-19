"""
app/jobs/notifications/__init__.py

On-demand notification jobs — enqueued from routes/services, not
cron-scheduled. Distinct from the inline, synchronous
create_notification() used for single-recipient cases (likes, comments)
— these are for cases needing to notify many recipients at once without
blocking the triggering request.
"""