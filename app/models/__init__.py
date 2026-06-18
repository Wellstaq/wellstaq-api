"""
SQLAlchemy models live here, one file per domain group, e.g.:

    organizations.py   organizations, branches, organization_invites
    users.py            users, user_wellbeing_baselines, user_priorities
    lookups.py          categories, wellbeing_challenges, hashtags, etc.
    departments.py       departments, department_members, department_rank_snapshots
    clubs.py            clubs, club_members, club_chat_messages
    events.py           events, event_participants, event_chat_messages
    challenges.py        challenges, challenge_participants
    posts.py            posts, post_likes, post_comments, post_shares, post_saves, post_hashtags
    stories.py          stories
    messaging.py        direct_messages
    notifications.py     notifications, user_activity_feed
    wellbeing.py         daily_checkins, user_health_assessments, wellbeing_scores
    analytics.py         activity_logs, engagement_logs, leaderboard_snapshots,
                         hashtag_trend_snapshots, targets, kpi_snapshots

Empty for now — filled in incrementally, starting with organizations.py
and users.py since most other tables depend on them.
"""
