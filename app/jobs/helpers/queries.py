"""
app/jobs/helpers/queries.py

Shared bulk-query builders, once at least two different job domains
need the same query shape (e.g. "windowed ActivityLog aggregation by
user" is likely needed by both engagement scoring AND leaderboard
computation — a strong candidate to land here once the engagement
rewrite and the leaderboard job are both built and the overlap is
confirmed, not assumed in advance).

Currently empty.
"""