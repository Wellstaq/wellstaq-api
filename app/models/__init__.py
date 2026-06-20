"""
SQLAlchemy models live here, one file per domain group.
"""

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin
from app.models.user import User, UserSettings, UserDevice, UserAuthProvider
from app.models.organization import Organization, OrganizationInvite
from app.models.branch import Branch
from app.models.department import Department, DepartmentMember, DepartmentRankSnapshot
from app.models.club import Club, ClubMember
from app.models.event import Event, EventParticipant
from app.models.chat import DirectMessage, GroupChatMessage
from app.models.notification import Notification
from app.models.post import Post, PostComment, PostSave, PostLike, PostShare, PostHashtag
from app.models.hashtag import Hashtag, HashtagTrendSnapshot
from app.models.challenge import Challenge, ChallengeParticipant
from app.models.category import Category, HealthCategory
from app.models.wellbeing import WellbeingDimension, WellbeingChallenge, WellbeingScore, OrganizationWellbeingChallenge, UserWellbeingBaseline
from app.models.activity import UserActivityFeed, UserPriority, DailyCheckin
__all__ = ["Base", "UUIDPrimaryKeyMixin", "TimestampMixin", "SoftDeleteMixin"]