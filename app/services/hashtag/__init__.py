"""
app/services/hashtag/__init__.py
"""

from .get_hashtag import get_posts_by_hashtag, get_trending_hashtags, search_hashtags
from .hashtag_service import get_or_create_hashtag, remove_all_post_hashtags, sync_post_hashtags

__all__ = [
    "get_or_create_hashtag",
    "sync_post_hashtags",
    "remove_all_post_hashtags",
    "search_hashtags",
    "get_trending_hashtags",
    "get_posts_by_hashtag",
]