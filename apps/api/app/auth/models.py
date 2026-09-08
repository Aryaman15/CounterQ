from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.ids import uuid7

if TYPE_CHECKING:
    from app.interviews.models import InterviewSession
    from app.problems.models import Problem


PROFILE_LANGUAGES = ("cpp", "java", "python")
PROFILE_INTERVIEW_MODES = ("COACH", "SIMULATION")
PROFILE_INTERVIEW_LEVELS = ("INTERN", "NEW_GRAD", "EARLY_CAREER")


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "external_auth_provider",
            "external_auth_subject",
            name="uq_users_external_auth_provider",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    external_auth_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_auth_subject: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    interview_sessions: Mapped[list[InterviewSession]] = relationship(back_populates="user")
    owned_problems: Mapped[list[Problem]] = relationship(back_populates="owner_user")
    candidate_profile: Mapped[CandidateProfile | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    __table_args__ = (
        CheckConstraint(
            _in_values("preferred_language", PROFILE_LANGUAGES),
            name="preferred_language",
        ),
        CheckConstraint(
            _in_values("default_interview_mode", PROFILE_INTERVIEW_MODES),
            name="default_interview_mode",
        ),
        CheckConstraint(
            _in_values("interview_level", PROFILE_INTERVIEW_LEVELS),
            name="interview_level",
        ),
        CheckConstraint("profile_version > 0", name="profile_version_positive"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(120))
    preferred_language: Mapped[str] = mapped_column(String(16), nullable=False)
    default_interview_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    interview_level: Mapped[str] = mapped_column(String(32), nullable=False)
    target_role: Mapped[str | None] = mapped_column(String(160))
    timezone: Mapped[str | None] = mapped_column(String(64))
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    user: Mapped[User] = relationship(back_populates="candidate_profile")
