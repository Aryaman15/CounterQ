from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.ids import uuid7

if TYPE_CHECKING:
    from app.ai_gateway.models import AIPolicyVersion
    from app.auth.models import User
    from app.interviews.models import InterviewSession


class Problem(Base):
    __tablename__ = "problems"
    __table_args__ = (UniqueConstraint("source_type", "slug", name="uq_problems_source_type_slug"),)

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(256))
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    owner_user: Mapped[User | None] = relationship(back_populates="owned_problems")
    versions: Mapped[list[ProblemVersion]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ProblemVersion(Base):
    __tablename__ = "problem_versions"
    __table_args__ = (
        UniqueConstraint("problem_id", "version", name="uq_problem_versions_problem_version"),
        UniqueConstraint("problem_id", "content_hash", name="uq_problem_versions_problem_hash"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    problem_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("problems.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    constraints_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    examples_json: Mapped[list[object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    io_schema_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    problem: Mapped[Problem] = relationship(back_populates="versions")
    interview_pack_versions: Mapped[list[InterviewPackVersion]] = relationship(
        back_populates="problem_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    interview_sessions: Mapped[list[InterviewSession]] = relationship(
        back_populates="problem_version",
    )


class InterviewPackVersion(Base):
    __tablename__ = "interview_pack_versions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    problem_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("problem_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    preparation_policy_key: Mapped[str | None] = mapped_column(String(128))
    ai_policy_version_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ai_policy_versions.id"),
    )
    pack_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    problem_version: Mapped[ProblemVersion] = relationship(back_populates="interview_pack_versions")
    ai_policy_version: Mapped[AIPolicyVersion | None] = relationship()
    interview_sessions: Mapped[list[InterviewSession]] = relationship(
        back_populates="interview_pack_version",
    )
