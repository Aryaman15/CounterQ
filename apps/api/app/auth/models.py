from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.ids import uuid7

if TYPE_CHECKING:
    from app.interviews.models import InterviewSession
    from app.problems.models import Problem


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
