"""
app/models/platform_event.py

Featured events — created and managed by Wellstaq staff (AdminUser),
not by any organization. Distinct from app.models.event.Event (org
events), which belong to one organization's branch. A featured event
can target everyone on the platform, individuals only, employees of
every org, or a specific list of organizations — see
PlatformEventOrganization and the audience docstring below.

recurrence_rule is stored but not evaluated — the admin console sends
and displays it but has no editor for it yet (v1 scope decision), so
this is a plain nullable string (iCal RRULE format, e.g.
"FREQ=WEEKLY;BYDAY=MO;COUNT=8") with no expansion logic. Each
occurrence of a recurring event is still just this one row; building
real recurrence (materializing instances, editing a single occurrence,
etc.) is a v2 concern.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PLATFORM_EVENT_STATUSES = ("scheduled", "ongoing", "completed", "cancelled")
PLATFORM_EVENT_AUDIENCES = ("everyone", "individuals", "employees", "selected_organizations")


class PlatformEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "platform_events"

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False, default="Africa/Lagos")
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    # "selected_organizations" is the only value where
    # PlatformEventOrganization rows are read/written; for the other
    # three, any such rows are ignored (there shouldn't be any).
    audience: Mapped[str] = mapped_column(String(30), nullable=False, default="everyone")

    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True,
    )

    organizations: Mapped[list["PlatformEventOrganization"]] = relationship(
        "PlatformEventOrganization", back_populates="event", cascade="all, delete-orphan",
    )
    participants: Mapped[list["PlatformEventParticipant"]] = relationship(
        "PlatformEventParticipant", back_populates="event", cascade="all, delete-orphan",
    )
    creator: Mapped["AdminUser"] = relationship("AdminUser", foreign_keys=[created_by])

    __table_args__ = (
        CheckConstraint(f"status IN {PLATFORM_EVENT_STATUSES!r}", name="ck_platform_events_status_valid"),
        CheckConstraint(f"audience IN {PLATFORM_EVENT_AUDIENCES!r}", name="ck_platform_events_audience_valid"),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_platform_events_date_range",
        ),
        CheckConstraint("length(trim(title)) > 0", name="ck_platform_events_title_not_empty"),
    )

    def __repr__(self) -> str:
        return f"<PlatformEvent id={self.id} title={self.title} audience={self.audience}>"


class PlatformEventOrganization(Base, UUIDPrimaryKeyMixin):
    """Join row for audience='selected_organizations' — which orgs'
    employees can see this event. Irrelevant for the other 3 audience
    values (deleted automatically via the event's own cascade)."""

    __tablename__ = "platform_event_organizations"

    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("platform_events.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    event: Mapped["PlatformEvent"] = relationship("PlatformEvent", back_populates="organizations")
    organization: Mapped["Organization"] = relationship("Organization")

    __table_args__ = (
        UniqueConstraint("event_id", "organization_id", name="uq_platform_event_organization"),
    )


class PlatformEventParticipant(Base, UUIDPrimaryKeyMixin):
    """A user (employee or individual) who joined a featured event.
    user_id is a plain FK to users.id — individual (org-less) users
    aren't modeled yet, so for now only employees can actually join;
    see the Individuals open question from the admin contract."""

    __tablename__ = "platform_event_participants"

    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("platform_events.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
    )

    event: Mapped["PlatformEvent"] = relationship("PlatformEvent", back_populates="participants")
    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_platform_event_participant"),
    )
