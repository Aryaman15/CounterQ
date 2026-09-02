"""stage 5a canonical evaluation foundation

Revision ID: 202609020112
Revises: 202608310111
Create Date: 2026-09-02
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609020112"
down_revision: str | None = "202608310111"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


ASSESSMENT_DIMENSIONS = (
    "CORRECTNESS",
    "DEPTH",
    "INDEPENDENCE",
    "TRANSFER",
    "EXPLANATION_QUALITY",
)
ASSESSMENT_STATUSES = ("PROPOSED", "VALIDATED", "REJECTED", "SUPERSEDED")
EVIDENCE_POLARITIES = ("POSITIVE", "NEGATIVE", "MIXED")
EVIDENCE_STRENGTHS = ("WEAK", "MODERATE", "STRONG")
EVIDENCE_INDEPENDENCE_LEVELS = (
    "INDEPENDENT",
    "AFTER_PROBE",
    "AFTER_LIGHT_GUIDANCE",
    "AFTER_STRONG_HINT",
    "DIRECTLY_TAUGHT",
)
EVIDENCE_VALIDATION_STATUSES = ("VALID", "REJECTED", "INVALIDATED")
EVIDENCE_SOURCE_ROLES = ("PRIMARY", "SUPPORTING", "CONTRADICTING", "CONTEXT")
BREAKPOINT_STATUSES = ("OPEN", "RETEST_PENDING", "IMPROVING", "RESOLVED", "DISMISSED")
BREAKPOINT_EVIDENCE_RELATIONSHIPS = (
    "CREATED",
    "REINFORCED",
    "CONTRADICTED",
    "RESOLUTION_SUPPORT",
)

SKILL_DIMENSIONS = (
    (
        UUID("01991f00-0000-7000-8000-000000000001"),
        "correctness",
        "Correctness",
        "Technical and implementation correctness demonstrated by the candidate.",
    ),
    (
        UUID("01991f00-0000-7000-8000-000000000002"),
        "explanation_clarity",
        "Explanation Clarity",
        "Clarity and precision of the candidate's technical explanation.",
    ),
    (
        UUID("01991f00-0000-7000-8000-000000000003"),
        "complexity_reasoning",
        "Complexity Reasoning",
        "Reasoning about time and space complexity.",
    ),
    (
        UUID("01991f00-0000-7000-8000-000000000004"),
        "edge_case_reasoning",
        "Edge-case Reasoning",
        "Identification and analysis of edge cases.",
    ),
    (
        UUID("01991f00-0000-7000-8000-000000000005"),
        "trade_off_reasoning",
        "Trade-off Reasoning",
        "Evaluation of implementation and algorithmic trade-offs.",
    ),
    (
        UUID("01991f00-0000-7000-8000-000000000006"),
        "follow_up_adaptability",
        "Follow-up Adaptability",
        "Ability to respond to diagnostic follow-up questions.",
    ),
    (
        UUID("01991f00-0000-7000-8000-000000000007"),
        "debugging",
        "Debugging",
        "Ability to locate and correct implementation failures.",
    ),
    (
        UUID("01991f00-0000-7000-8000-000000000008"),
        "constraint_adaptation",
        "Constraint Adaptation",
        "Ability to adapt reasoning when problem constraints change.",
    ),
    (
        UUID("01991f00-0000-7000-8000-000000000009"),
        "thinking_aloud",
        "Thinking Aloud",
        "Externalization of relevant reasoning during the interview.",
    ),
    (
        UUID("01991f00-0000-7000-8000-00000000000a"),
        "communication",
        "Communication",
        "Professional communication of technical decisions and uncertainty.",
    ),
)


def upgrade() -> None:
    op.create_table(
        "skill_dimensions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_key", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("canonical_key", name="uq_skill_dimensions_canonical_key"),
    )
    skill_dimensions = sa.table(
        "skill_dimensions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("canonical_key", sa.String(128)),
        sa.column("display_name", sa.String(256)),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String(32)),
    )
    op.bulk_insert(
        skill_dimensions,
        [
            {
                "id": identifier,
                "canonical_key": key,
                "display_name": display_name,
                "description": description,
                "status": "ACTIVE",
            }
            for identifier, key, display_name, description in SKILL_DIMENSIONS
        ],
    )

    op.create_table(
        "assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_response_id", postgresql.UUID(as_uuid=True)),
        sa.Column("target_claim_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_code_snapshot_id", postgresql.UUID(as_uuid=True)),
        sa.Column("assessment_dimension", sa.String(64), nullable=False),
        sa.Column("polarity", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("ai_invocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ai_policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("assessment_dimension", ASSESSMENT_DIMENSIONS),
            name="dimension",
        ),
        sa.CheckConstraint(_in_values("polarity", EVIDENCE_POLARITIES), name="polarity"),
        sa.CheckConstraint(_in_values("status", ASSESSMENT_STATUSES), name="status"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_unit_interval",
        ),
        sa.CheckConstraint("length(btrim(rationale)) > 0", name="rationale_nonempty"),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_response_id"], ["candidate_responses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["target_claim_id"], ["candidate_claims.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_code_snapshot_id"], ["code_snapshots.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["ai_invocation_id"], ["ai_invocations.id"]),
        sa.ForeignKeyConstraint(["ai_policy_version_id"], ["ai_policy_versions.id"]),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "candidate_response_id"],
            ["candidate_responses.interview_session_id", "candidate_responses.id"],
            name="fk_assessments_session_response",
            ondelete="SET NULL (candidate_response_id)",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "target_claim_id"],
            ["candidate_claims.interview_session_id", "candidate_claims.id"],
            name="fk_assessments_session_claim",
            ondelete="SET NULL (target_claim_id)",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "source_code_snapshot_id"],
            ["code_snapshots.interview_session_id", "code_snapshots.id"],
            name="fk_assessments_session_code_snapshot",
            ondelete="SET NULL (source_code_snapshot_id)",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_assessments_session_ai_invocation",
        ),
        sa.UniqueConstraint("interview_session_id", "id", name="uq_assessments_session_id"),
    )
    op.create_index(
        "ix_assessments_session_created_at", "assessments", ["interview_session_id", "created_at"]
    )

    op.create_table(
        "assessment_sources",
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_role", sa.String(32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            _in_values("source_role", EVIDENCE_SOURCE_ROLES),
            name="source_role",
        ),
        sa.CheckConstraint("sequence > 0", name="sequence_positive"),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interview_event_id"], ["interview_events.id"]),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "assessment_id"],
            ["assessments.interview_session_id", "assessments.id"],
            name="fk_assessment_sources_session_assessment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "interview_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_assessment_sources_session_event",
        ),
        sa.UniqueConstraint("assessment_id", "sequence", name="uq_assessment_sources_sequence"),
    )

    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("polarity", sa.String(32), nullable=False),
        sa.Column("strength", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("finding", sa.Text(), nullable=False),
        sa.Column("independence_level", sa.String(64), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("originating_assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("validation_policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("invalidation_reason", sa.Text()),
        sa.CheckConstraint("length(btrim(evidence_type)) > 0", name="type_nonempty"),
        sa.CheckConstraint(_in_values("polarity", EVIDENCE_POLARITIES), name="polarity"),
        sa.CheckConstraint(_in_values("strength", EVIDENCE_STRENGTHS), name="strength"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_unit_interval"),
        sa.CheckConstraint("length(btrim(finding)) > 0", name="finding_nonempty"),
        sa.CheckConstraint(
            _in_values("independence_level", EVIDENCE_INDEPENDENCE_LEVELS),
            name="independence_level",
        ),
        sa.CheckConstraint(
            _in_values("validation_status", EVIDENCE_VALIDATION_STATUSES),
            name="validation_status",
        ),
        sa.CheckConstraint(
            "(validation_status = 'INVALIDATED' AND invalidated_at IS NOT NULL "
            "AND invalidation_reason IS NOT NULL AND length(btrim(invalidation_reason)) > 0) OR "
            "(validation_status <> 'INVALIDATED' AND invalidated_at IS NULL "
            "AND invalidation_reason IS NULL)",
            name="invalidation_state_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["originating_assessment_id"], ["assessments.id"]),
        sa.ForeignKeyConstraint(["validation_policy_version_id"], ["ai_policy_versions.id"]),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "originating_assessment_id"],
            ["assessments.interview_session_id", "assessments.id"],
            name="fk_evidence_session_assessment",
        ),
        sa.UniqueConstraint("interview_session_id", "id", name="uq_evidence_session_id"),
    )
    op.create_index(
        "ix_evidence_session_created_at", "evidence", ["interview_session_id", "created_at"]
    )
    op.create_index(
        "ix_evidence_session_active",
        "evidence",
        ["interview_session_id", "created_at"],
        postgresql_where=sa.text("validation_status = 'VALID' AND invalidated_at IS NULL"),
    )

    op.create_table(
        "evidence_sources",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_role", sa.String(32), nullable=False),
        sa.CheckConstraint(
            _in_values("source_role", EVIDENCE_SOURCE_ROLES),
            name="source_role",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interview_event_id"], ["interview_events.id"]),
        sa.ForeignKeyConstraint(
            ["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "evidence_id"],
            ["evidence.interview_session_id", "evidence.id"],
            name="fk_evidence_sources_session_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "interview_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_evidence_sources_session_event",
        ),
    )
    op.create_index("ix_evidence_sources_evidence", "evidence_sources", ["evidence_id"])

    op.create_table(
        "evidence_concepts",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("relevance", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "relevance > 0 AND relevance <= 1",
            name="relevance_unit_interval",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_evidence_concepts_concept_evidence",
        "evidence_concepts",
        ["concept_id", "evidence_id"],
    )
    op.create_index(
        "uq_evidence_concepts_one_primary",
        "evidence_concepts",
        ["evidence_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "evidence_skills",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("skill_dimension_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("relevance", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "relevance > 0 AND relevance <= 1",
            name="relevance_unit_interval",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["skill_dimension_id"], ["skill_dimensions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_evidence_skills_skill_evidence",
        "evidence_skills",
        ["skill_dimension_id", "evidence_id"],
    )
    op.create_index(
        "uq_evidence_skills_one_primary",
        "evidence_skills",
        ["evidence_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "breakpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_dimension_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("breakpoint_key", sa.String(256), nullable=False),
        sa.Column("first_detected_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_reason", sa.Text()),
        sa.CheckConstraint(_in_values("status", BREAKPOINT_STATUSES), name="status"),
        sa.CheckConstraint(
            "breakpoint_key ~ '^[a-z0-9]+(_[a-z0-9]+)*$'",
            name="key_normalized",
        ),
        sa.CheckConstraint("length(btrim(severity)) > 0", name="severity_nonempty"),
        sa.CheckConstraint("length(btrim(summary)) > 0", name="summary_nonempty"),
        sa.CheckConstraint(
            "(status IN ('RESOLVED', 'DISMISSED') AND resolved_at IS NOT NULL "
            "AND resolution_reason IS NOT NULL AND length(btrim(resolution_reason)) > 0) OR "
            "(status NOT IN ('RESOLVED', 'DISMISSED') AND resolved_at IS NULL "
            "AND resolution_reason IS NULL)",
            name="resolution_state_consistent",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["skill_dimension_id"], ["skill_dimensions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["first_detected_session_id"], ["interview_sessions.id"]),
        sa.ForeignKeyConstraint(
            ["first_detected_session_id", "user_id"],
            ["interview_sessions.id", "interview_sessions.user_id"],
            name="fk_breakpoints_session_user",
        ),
    )
    op.create_index(
        "uq_breakpoints_active_identity",
        "breakpoints",
        ["user_id", "concept_id", "skill_dimension_id", "breakpoint_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN', 'RETEST_PENDING', 'IMPROVING')"),
    )
    op.create_index(
        "ix_breakpoints_user_active",
        "breakpoints",
        ["user_id", "concept_id"],
        postgresql_where=sa.text("status IN ('OPEN', 'RETEST_PENDING', 'IMPROVING')"),
    )

    op.create_table(
        "breakpoint_evidence",
        sa.Column("breakpoint_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("relationship", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("relationship", BREAKPOINT_EVIDENCE_RELATIONSHIPS),
            name="relationship",
        ),
        sa.ForeignKeyConstraint(["breakpoint_id"], ["breakpoints.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"]),
    )
    op.create_index("ix_breakpoint_evidence_evidence", "breakpoint_evidence", ["evidence_id"])


def downgrade() -> None:
    op.drop_index("ix_breakpoint_evidence_evidence", table_name="breakpoint_evidence")
    op.drop_table("breakpoint_evidence")
    op.drop_index("ix_breakpoints_user_active", table_name="breakpoints")
    op.drop_index("uq_breakpoints_active_identity", table_name="breakpoints")
    op.drop_table("breakpoints")
    op.drop_index("uq_evidence_skills_one_primary", table_name="evidence_skills")
    op.drop_index("ix_evidence_skills_skill_evidence", table_name="evidence_skills")
    op.drop_table("evidence_skills")
    op.drop_index("uq_evidence_concepts_one_primary", table_name="evidence_concepts")
    op.drop_index("ix_evidence_concepts_concept_evidence", table_name="evidence_concepts")
    op.drop_table("evidence_concepts")
    op.drop_index("ix_evidence_sources_evidence", table_name="evidence_sources")
    op.drop_table("evidence_sources")
    op.drop_index("ix_evidence_session_active", table_name="evidence")
    op.drop_index("ix_evidence_session_created_at", table_name="evidence")
    op.drop_table("evidence")
    op.drop_table("assessment_sources")
    op.drop_index("ix_assessments_session_created_at", table_name="assessments")
    op.drop_table("assessments")
    op.drop_table("skill_dimensions")
