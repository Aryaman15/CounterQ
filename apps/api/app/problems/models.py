from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.ids import uuid7

if TYPE_CHECKING:
    from app.ai_gateway.models import AIInvocation, AIPolicyVersion
    from app.auth.models import User
    from app.interviews.models import InterviewConfiguration, InterviewSession


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


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
    __table_args__ = (
        UniqueConstraint(
            "problem_version_id",
            "authored_version",
            name="uq_interview_pack_versions_problem_authored_version",
        ),
        UniqueConstraint(
            "id",
            "problem_version_id",
            name="uq_interview_pack_versions_id_problem_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    problem_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("problem_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    authored_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
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
        foreign_keys="InterviewSession.interview_pack_version_id",
    )


class Concept(Base):
    __tablename__ = "concepts"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    canonical_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_concept_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class ConceptAlias(Base):
    __tablename__ = "concept_aliases"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    concept_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    alias_type: Mapped[str] = mapped_column(String(32), nullable=False)


class ConceptRelationship(Base):
    __tablename__ = "concept_relationships"
    __table_args__ = (
        UniqueConstraint(
            "from_concept_id", "to_concept_id", "relationship_type", name="uq_concept_relationship"
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    from_concept_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False
    )
    to_concept_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)


class ProblemConcept(Base):
    __tablename__ = "problem_concepts"
    __table_args__ = (
        UniqueConstraint(
            "problem_version_id", "concept_id", name="uq_problem_concepts_version_concept"
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    problem_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("problem_versions.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("concepts.id", ondelete="RESTRICT"), nullable=False
    )
    relevance: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_importance: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(32), nullable=False)


class CustomProblemPreparation(Base):
    """Owner-scoped workflow state; prepared artifacts remain immutable canonical rows."""

    __tablename__ = "custom_problem_preparations"
    __table_args__ = (
        CheckConstraint(
            _in_values("operational_status", ("PENDING", "PROCESSING", "FAILED", "COMPLETED")),
            name="operational_status",
        ),
        CheckConstraint(
            "quality_outcome IS NULL OR quality_outcome IN "
            "('READY', 'NEEDS_CORRECTION', 'REJECTED')",
            name="quality_outcome",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "(operational_status = 'COMPLETED' AND quality_outcome IS NOT NULL) OR "
            "(operational_status <> 'COMPLETED' AND quality_outcome IS NULL)",
            name="completed_has_quality_outcome",
        ),
        CheckConstraint(
            "(quality_outcome = 'READY' AND prepared_problem_version_id IS NOT NULL "
            "AND prepared_pack_version_id IS NOT NULL) OR "
            "(quality_outcome IS DISTINCT FROM 'READY' AND prepared_problem_version_id IS NULL "
            "AND prepared_pack_version_id IS NULL)",
            name="ready_has_prepared_artifacts",
        ),
        ForeignKeyConstraint(
            ["prepared_pack_version_id", "prepared_problem_version_id"],
            ["interview_pack_versions.id", "interview_pack_versions.problem_version_id"],
            name="fk_custom_preparations_pack_problem_version",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_custom_preparations_user_key"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    original_problem_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    preparation_policy_key: Mapped[str] = mapped_column(String(128), nullable=False)
    preparation_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_gate_version: Mapped[str] = mapped_column(String(64), nullable=False)
    operational_status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_outcome: Mapped[str | None] = mapped_column(String(32))
    candidate_message: Mapped[str | None] = mapped_column(Text)
    candidate_reasons_json: Mapped[list[object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    failure_category: Mapped[str | None] = mapped_column(String(64))
    normalization_ai_invocation_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_invocations.id", ondelete="SET NULL")
    )
    pack_ai_invocation_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_invocations.id", ondelete="SET NULL")
    )
    prepared_problem_version_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("problem_versions.id", ondelete="RESTRICT")
    )
    prepared_pack_version_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    sandbox_validation_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    owner: Mapped[User] = relationship()
    normalization_ai_invocation: Mapped[AIInvocation | None] = relationship(
        foreign_keys=[normalization_ai_invocation_id]
    )
    pack_ai_invocation: Mapped[AIInvocation | None] = relationship(
        foreign_keys=[pack_ai_invocation_id]
    )
    prepared_problem_version: Mapped[ProblemVersion | None] = relationship(
        foreign_keys=[prepared_problem_version_id]
    )
    prepared_pack_version: Mapped[InterviewPackVersion | None] = relationship(
        foreign_keys=[prepared_pack_version_id]
    )
    interview_configurations: Mapped[list[InterviewConfiguration]] = relationship(
        back_populates="custom_problem_preparation"
    )
