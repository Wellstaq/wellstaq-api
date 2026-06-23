"""
Category models — lookup tables for categorizing entities.

Category design:
- Used for department badges — name + badge_color for UI display.
- badge_color is a plain string (e.g. a hex code like "#FF5733" or a
  named color token), not validated at the DB level.
- This is the table Department.category_id was waiting on — see the
  commented-out category_id column and relationship in department.py.

HealthCategory design:
- Minimal lookup table for health/wellbeing-related entities — just a
  name, no extra metadata defined yet. Kept separate from Category since
  it serves a different domain (wellbeing vs department badges).
"""

from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Category(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    badge_color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Relationships
    departments: Mapped[list["Department"]] = relationship(
        "Department",
        back_populates="category",
    )
    clubs: Mapped[list["Club"]] = relationship(
        "Club",
        back_populates="category",
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_categories_name"),
        CheckConstraint("length(trim(name)) > 0", name="ck_categories_name_not_empty"),
    )

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name}>"


class HealthCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "health_categories"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Relationships
    assessments: Mapped[list["UserHealthAssessment"]] = relationship(
        "UserHealthAssessment",
        back_populates="category",
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_health_categories_name"),
        CheckConstraint("length(trim(name)) > 0", name="ck_health_categories_name_not_empty"),
    )

    def __repr__(self) -> str:
        return f"<HealthCategory id={self.id} name={self.name}>"