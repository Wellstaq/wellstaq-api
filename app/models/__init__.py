"""
SQLAlchemy models live here, one file per domain group.
"""

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin
from app.models.user import User, UserSettings, UserDevice, UserAuthProvider, MobilePushToken
from app.models.organization import Organization, OrganizationInvite
from app.models.branch import Branch
from app.models.department import Department, DepartmentMember, DepartmentRankSnapshot
from app.models.club import Club, ClubMember
from app.models.event import Event, EventParticipant
from app.models.chat import  GroupChatMessage
from app.models.notification import Notification
from app.models.post import Post, PostComment, PostSave, PostLike, PostShare, PostHashtag
from app.models.hashtag import Hashtag, HashtagTrendSnapshot
from app.models.challenge import Challenge, ChallengeParticipant,ChallengeProgressEntry, ChallengeStatsSnapshot
from app.models.category import Category, HealthCategory
from app.models.wellbeing import(
    WellbeingDimension,
    WellbeingChallenge, 
    WellbeingScore,
    OrganizationWellbeingChallenge,
    UserWellbeingBaseline,
    UserHealthAssessment
)
from app.models.activity import UserActivityFeed, UserPriority, DailyCheckin
from app.models.story import Story
from app.models.story_like import StoryLike
from app.models.permission import Permission, Role, RolePermission, UserPermission, UserRoleAssignment
from app.models.engagement import ActivityLog, EngagementLog, HealthActivitySync
from app.models.snapshot_target import LeaderboardSnapshot, KPISnapshot, Target
from app.models.payment import SubscriptionPlan, PaymentTransaction, OrganizationSubscription
from app.models.wellbeing_survey import(
    WellbeingPulseSnapshot,
    WellbeingSurveyConfig,
    WellbeingSurveyWindow,
    WellbeingSurveyResponse,
) 
from app.models.media_asset import MediaAsset
from app.models.integration import (
    HealthDataConsent, HealthMeasurement, IntegrationExternalRecord,
    OrganizationIntegration, UserIntegration,
)
from app.models.support_ticket import SupportTicket
from app.models.admin_user import AdminUser, AdminSession
from app.models.audit_entry import AuditEntry
from app.models.platform_event import PlatformEvent, PlatformEventOrganization, PlatformEventParticipant
__all__ = ["Base", "UUIDPrimaryKeyMixin", "TimestampMixin", "SoftDeleteMixin"]
