"""
Event and EventParticipant models — one file per domain group, same
pairing pattern as Club/ClubMember.

Event design principles:
- organization_id is nullable with ondelete=SET NULL: null = platform-wide
  general event. The event survives org deletion and degrades to
  platform-wide rather than being deleted with it.
- branch_id is nullable with ondelete=SET NULL: null = org-level event
  (or platform-wide if organization_id is also null). Same degrade-gracefully
  reasoning — losing a branch shouldn't delete its events.
- created_by is a real FK to users with ondelete=SET NULL: lets you query
  "events created by user X" via relationship, while the event survives
  if the creator is deleted (degrades to creator-unknown, not destroyed).
- recurrence_rule is a plain string pattern (e.g. "weekly_monday") rather
  than a structured table — keep it simple until recurrence needs grow.
- created_at / updated_at come from TimestampMixin, no need to redeclare.
- No soft delete in the schema, so no SoftDeleteMixin.

EventParticipant design principles:
- Join table between User and Event with a single `attended` boolean flag.
  No separate "registered" state — if you need to distinguish registered
  vs attended vs no-show later, this is the place to add that status field.
- Composite unique constraint on (event_id, user_id): a user can only have
  one participation record per event.
- event_id is CASCADE: a participation record is meaningless without its
  event, so it's deleted along with it.
- user_id is CASCADE: per decision, a participation record is meaningless
  without its user, so it's deleted along with them (no audit trail kept
  here, unlike Event.created_by / Branch.created_by).
"""

from datetime import date, time, datetime

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    UniqueConstraint,
    DateTime,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Event(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "events"

    # ── Identity ──────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # ── Scheduling ────────────────────────────────────────────────────────
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Date of the event, or first occurrence if recurring",
    )
    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Last occurrence date if recurring; null = one-time or open-ended",
    )
    recurrence_rule: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Null = one-time event, otherwise pattern e.g. weekly_monday",
    )
    time: Mapped[time] = mapped_column(Time, nullable=False)

    # ── Status ────────────────────────────────────────────────────────────
    # scheduled | ongoing | completed | cancelled
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="scheduled")

    # ── Scope ─────────────────────────────────────────────────────────────
    # Nullable, SET NULL — null = platform-wide general event; survives
    # org deletion instead of being deleted with it.
    organization_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Nullable, SET NULL — null = org-level event, not branch-specific;
    # survives branch deletion instead of being deleted with it.
    # No use_alter needed here: unlike User/Branch, Event has no circular
    # FK dependency (nothing points back to events), so the FK can be
    # created inline with the table.
    branch_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Audit ─────────────────────────────────────────────────────────────
    # Real FK, ondelete=SET NULL — lets us query "events created by user X"
    # via relationship while preserving the event if the creator is deleted.
    # Unlike Branch.created_by (plain string), Event wants a live join back
    # to User for org/branch creator tracking.
    created_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="events",
        foreign_keys=[organization_id],
    )

    branch: Mapped["Branch"] = relationship(
        "Branch",
        back_populates="events",
        foreign_keys=[branch_id],
    )

    creator: Mapped["User"] = relationship(
        "User",
        back_populates="created_events",
        foreign_keys=[created_by],
    )

    participants: Mapped[list["EventParticipant"]] = relationship(
        "EventParticipant",
        back_populates="event",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_events_org_start_date", "organization_id", "start_date"),
    )

    def __repr__(self) -> str:
        return f"<Event id={self.id} title={self.title} start={self.start_date}>"


"""
EventParticipant model — tracks who is participating in an event.

Design principles:
- A participant record links exactly one user to one event.
- invited_at records when the invitation was sent; nullable for
  self-registered (open-join) participants who were never formally invited.
- status tracks the RSVP lifecycle — expected values are 'invited',
  'accepted', 'declined', 'pending'. Stored as varchar rather than a DB
  enum to allow new states without an ALTER TYPE migration.
- attended is a post-event boolean flag, separate from status: status
  reflects RSVP intent before the event, attended reflects actual presence.
- (event_id, user_id) is unique — one participation record per user per event.
- Both FKs use CASCADE — removing an event or user removes their
  participation rows automatically.
"""


class EventParticipant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "event_participants"

    # ── Links ─────────────────────────────────────────────────────────────
    event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Invitation ────────────────────────────────────────────────────────
    # Nullable — open-join events don't go through an invitation flow
    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # ── RSVP status ───────────────────────────────────────────────────────
    # Expected values: 'invited' | 'accepted' | 'declined' | 'pending'
    # varchar not enum — avoids ALTER TYPE migrations for new states
    status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # ── Post-event ────────────────────────────────────────────────────────
    # Separate from status — status tracks RSVP intent, attended tracks
    # actual presence recorded after the event concludes.
    attended: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────
    event: Mapped["Event"] = relationship(
        "Event",
        back_populates="participants",
        foreign_keys=[event_id],
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="event_participations",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_participant_event_user"),
    )

    def __repr__(self) -> str:
        return (
            f"<EventParticipant event={self.event_id} user={self.user_id}"
            f" status={self.status} attended={self.attended}>"
        )