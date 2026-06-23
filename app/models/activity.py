"""
User engagement models — priorities, daily check-ins, and the unified
activity feed.

UserPriority design principles:
- A user can rank multiple priorities (e.g. "reduce_stress" = 1,
  "build_energy" = 2). priority is a free string, validated at the
  app layer, same pattern as Organization.organization_type.
- rank is a plain int, no DB-level uniqueness on (user_id, rank) — the
  app layer is responsible for keeping ranks sane (1..N, no gaps/dupes)
  since enforcing that at the DB level would block normal reordering
  operations (swap two ranks = transient duplicate mid-update).
  A CheckConstraint(rank > 0) is still enforced at the DB level —
  it doesn't interfere with reordering (which swaps existing positive
  values), it only rejects nonsensical values like 0 or negatives
  reaching the DB if app-layer validation is ever bypassed or buggy.
- ondelete=CASCADE on user_id — priorities are meaningless without the
  user they belong to.

DailyCheckin design principles:
- mood / energy_level / stress_level are free strings sharing the same
  vocabulary (good | bad | stressed | tired) per the schema notes.
  This vocabulary is small and fixed, so it's enforced with a DB-level
  CheckConstraint (IN (...)) rather than a Postgres enum type or a
  reference table — cheaper than an enum migration, and it directly
  prevents dirty-data variants ("Good", "GOOD", "gud") from ever
  reaching the table, regardless of what the app layer does.
- checked_in_at is the precise timestamp of the check-in.
- checkin_date is a separate plain Date column, NOT in the original
  DBML, added specifically to enforce "one check-in per user per day"
  with a real unique constraint. checked_in_at (timestamp) can't
  reliably back a daily-uniqueness constraint since two check-ins on
  the same calendar day will almost never share the exact same
  timestamp. checkin_date also pays for itself later: streak counts
  and "did this user check in today" queries are both simple equality
  lookups on an indexed Date column instead of a date-range scan over
  a timestamp column.
  NOTE: checkin_date is currently derived from UTC
  (datetime.now(timezone.utc).date()), which means it reflects the
  server's calendar day, not the user's local calendar day. This is a
  known simplification while the schema is still being designed and
  the project runs on SQLite — revisit once user-level timezone
  storage is in place, since a user checking in late at night or early
  morning local time could otherwise be recorded against the wrong day.
- ondelete=CASCADE on user_id — a check-in is meaningless without the
  user who submitted it.

UserActivityFeed design principles:
- Single unified table for all user actions, polymorphic by design:
  reference_id + reference_type point at the actual record (a club,
  event, post, etc.) without a real FK, since no single table could be
  the FK target for every possible reference_type. Integrity here is
  enforced at the app layer, not the DB — same tradeoff already made
  for Branch.created_by / Event-style audit fields, just applied to a
  polymorphic reference instead of a single user reference.
  A CheckConstraint enforces that reference_type and reference_id are
  either both NULL or both set — that pairing invariant is cheap to
  guarantee at the DB level and doesn't depend on every write path
  remembering to validate it.
- activity_type is a free string (app-layer validated).
- organization_id is required (NOT NULL), ondelete=CASCADE — per
  decision, every activity always happens within an org context.
- Both relationships (user, organization) specify foreign_keys=
  explicitly even though each only has one FK candidate column — this
  matches the convention used everywhere else in the codebase (Branch,
  Department, Event, Challenge), and avoids relying on SQLAlchemy's
  FK-inference holding correctly as the model surface grows.
- The `metadata` column is named "metadata" in the DBML/DB, but
  `Base.metadata` is a reserved SQLAlchemy attribute (it holds the
  ORM's internal table/schema registry) — naming a Python attribute
  `metadata` directly on a model raises an error. The Python attribute
  is `activity_metadata`; mapped_column's first positional arg keeps
  the actual DB column name as "metadata", so the schema matches the
  DBML exactly while the Python-side name avoids the collision.
- JSON used instead of a Postgres-only JSONB type so the column works
  identically on SQLite (tests) and Postgres (prod) — SQLAlchemy's
  generic JSON type compiles to JSONB automatically on the Postgres
  dialect, so this isn't a downgrade in production.
"""

from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Shared vocabulary for DailyCheckin.mood / energy_level / stress_level.
# Kept as a module-level tuple so the CheckConstraint and any app-layer
# validation can both reference the same source of truth.
CHECKIN_VOCABULARY = ("good", "bad", "stressed", "tired")


class UserPriority(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_priorities"

    # ── FK ────────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Priority data ─────────────────────────────────────────────────────
    # Free string — e.g. reduce_stress | improve_balance | build_energy
    priority: Mapped[str] = mapped_column(String(100), nullable=False)
    # 1 = highest priority. Ordering/uniqueness enforced at the app layer;
    # rank > 0 enforced at the DB layer (see CheckConstraint below).
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Relationships ─────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="priorities",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        CheckConstraint("rank > 0", name="ck_user_priorities_rank_positive"),
    )

    def __repr__(self) -> str:
        return f"<UserPriority user_id={self.user_id} priority={self.priority} rank={self.rank}>"


class DailyCheckin(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "daily_checkins"

    # ── FK ────────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Check-in data ─────────────────────────────────────────────────────
    # Free strings sharing the good | bad | stressed | tired vocabulary,
    # enforced at the DB layer via CheckConstraint (see __table_args__).
    mood: Mapped[str] = mapped_column(String(50), nullable=False)
    energy_level: Mapped[str] = mapped_column(String(50), nullable=False)
    stress_level: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    # Plain Date, derived from checked_in_at at write time — backs the
    # one-check-in-per-day constraint and fast "checked in today" /
    # streak queries. See module docstring for the full reasoning,
    # including the known UTC-vs-local-day simplification.
    checkin_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).date(),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="daily_checkins",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        Index("ix_daily_checkins_user_checked_in_at", "user_id", "checked_in_at"),
        Index(
            "uq_daily_checkins_user_date",
            "user_id",
            "checkin_date",
            unique=True,
        ),
        CheckConstraint(
            "mood IN ('good', 'bad', 'stressed', 'tired')",
            name="ck_daily_checkins_mood_vocab",
        ),
        CheckConstraint(
            "energy_level IN ('good', 'bad', 'stressed', 'tired')",
            name="ck_daily_checkins_energy_level_vocab",
        ),
        CheckConstraint(
            "stress_level IN ('good', 'bad', 'stressed', 'tired')",
            name="ck_daily_checkins_stress_level_vocab",
        ),
    )

    def __repr__(self) -> str:
        return f"<DailyCheckin user_id={self.user_id} date={self.checkin_date} mood={self.mood}>"


class UserActivityFeed(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "user_activity_feed"

    # ── FKs ───────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Required per decision — every activity always happens within an org.
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Activity data ─────────────────────────────────────────────────────
    # Free string — e.g. joined_department | joined_club | created_post | ...
    activity_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # Polymorphic reference — no real FK possible since reference_type can
    # point at any one of several tables. Integrity enforced at app layer,
    # except for the null-pairing invariant, which is cheap to enforce at
    # the DB layer (see CheckConstraint below): either both columns are
    # set, or neither is.
    reference_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # e.g. department | club | event | challenge | post | activity_log
    reference_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    activity_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────
    # foreign_keys= specified explicitly on both, even with only one FK
    # candidate each — matches the convention used on Branch, Department,
    # Event, Challenge, rather than relying on SQLAlchemy's inference.
    user: Mapped["User"] = relationship(
        "User",
        back_populates="activity_feed",
        foreign_keys=[user_id],
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="activity_feed",
        foreign_keys=[organization_id],
    )

    __table_args__ = (
        Index("ix_user_activity_feed_user_created_at", "user_id", "created_at"),
        Index("ix_user_activity_feed_org_created_at", "organization_id", "created_at"),
        Index("ix_user_activity_feed_activity_type", "activity_type"),
        CheckConstraint(
            "(reference_type IS NULL AND reference_id IS NULL) OR "
            "(reference_type IS NOT NULL AND reference_id IS NOT NULL)",
            name="ck_user_activity_feed_reference_pairing",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<UserActivityFeed user_id={self.user_id} "
            f"activity_type={self.activity_type} created_at={self.created_at}>"
        )