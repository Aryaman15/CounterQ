"""stage1 1b causal interpretation and interviewer persistence

Revision ID: 202608230104
Revises: 202608230103
Create Date: 2026-08-23 07:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608230104"
down_revision: str | Sequence[str] | None = "202608230103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


CLAIM_ORIGIN_KINDS = ("TRANSCRIPT", "CODE", "EXECUTION", "MULTIMODAL_CONTEXT")
CLAIM_TYPES = (
    "ALGORITHM_CHOICE",
    "COMPLEXITY",
    "CORRECTNESS",
    "INVARIANT",
    "DATA_STRUCTURE",
    "ASSUMPTION",
    "EDGE_CASE",
    "IMPLEMENTATION",
    "TRADE_OFF",
)
CLAIM_STATUSES = ("PROPOSED", "ACCEPTED_AS_INTERPRETATION", "REJECTED", "SUPERSEDED")
EXAMINER_ACTIONS = ("WAIT", "OBSERVE", "ASK", "PROBE")
EXAMINER_DECISION_STATUSES = (
    "PROPOSED",
    "AUTHORIZED",
    "REJECTED",
    "STALE",
    "EXPIRED",
    "SUPERSEDED",
)
POLICY_GATE_OUTCOMES = (
    "AUTHORIZED",
    "REJECTED",
    "STALE",
    "BUDGET_DENIED",
    "STAGE_INVALID",
    "LOW_CONFIDENCE",
    "SUPERSEDED",
    "EXPIRED",
)
PROBE_STRATEGIES = (
    "WHY",
    "PROVE",
    "ASSUMPTION_CHALLENGE",
    "COUNTEREXAMPLE",
    "COMPLEXITY",
    "EDGE_CASE",
    "TRADE_OFF",
    "ALTERNATIVE",
    "IMPLEMENTATION_CHOICE",
    "CONSTRAINT_MUTATION",
    "FAILURE_MODE",
    "TRANSFER",
)
PROMPT_ORIGINS = ("STATE_MACHINE", "EXAMINER_DECISION", "SYSTEM")
PROMPT_KINDS = (
    "BASE_QUESTION",
    "CLARIFICATION",
    "PROBE",
    "TRANSITION",
    "INSTRUCTION",
    "TIME_WARNING",
)
PROMPT_STATUSES = (
    "PROPOSED",
    "AUTHORIZED",
    "DELIVERED",
    "ANSWERED",
    "REJECTED",
    "STALE",
    "EXPIRED",
    "INTERRUPTED",
    "CANCELLED",
)
DELIVERY_STATES = ("STARTED", "DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED", "CANCELLED")
RESPONSE_COMPLETION_REASONS = (
    "COMPLETE",
    "INTERRUPTED",
    "SUPERSEDED",
    "TIMEOUT",
    "SPONTANEOUS",
)
RESPONSE_SOURCE_ROLES = ("PRIMARY", "SUPPORTING", "CODE_CONTEXT", "RUN_CONTEXT")
AI_INVOCATION_STATUSES = ("STARTED", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT")


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_transcript_segments_session_id",
        "transcript_segments",
        ["interview_session_id", "id"],
    )

    op.create_table(
        "ai_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("provider_model_version", sa.String(length=128), nullable=True),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=96), nullable=False),
        sa.Column("ai_policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("audio_input_units", sa.Integer(), nullable=True),
        sa.Column("audio_output_units", sa.Integer(), nullable=True),
        sa.Column("image_units", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("provider_request_id", sa.String(length=256), nullable=True),
        sa.Column("error_class", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("status", AI_INVOCATION_STATUSES), name="ck_ai_invocations_status"
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_ai_invocations_completed_after_started",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="ck_ai_invocations_latency_nonnegative"
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_ai_invocations_input_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
            name="ck_ai_invocations_cached_input_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_ai_invocations_output_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "audio_input_units IS NULL OR audio_input_units >= 0",
            name="ck_ai_invocations_audio_input_nonnegative",
        ),
        sa.CheckConstraint(
            "audio_output_units IS NULL OR audio_output_units >= 0",
            name="ck_ai_invocations_audio_output_nonnegative",
        ),
        sa.CheckConstraint(
            "image_units IS NULL OR image_units >= 0",
            name="ck_ai_invocations_image_units_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="ck_ai_invocations_estimated_cost_nonnegative",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_ai_invocations_retry_count_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["ai_policy_version_id"], ["ai_policy_versions.id"]),
        sa.UniqueConstraint("interview_session_id", "id", name="uq_ai_invocations_session_id"),
    )
    op.create_index(
        "ix_ai_invocations_session_purpose",
        "ai_invocations",
        ["interview_session_id", "purpose"],
    )
    op.create_index(
        "ix_ai_invocations_provider_model_started",
        "ai_invocations",
        ["provider", "model", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_ai_invocations_purpose_started",
        "ai_invocations",
        ["purpose", sa.text("started_at DESC")],
    )

    op.create_table(
        "code_diffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("diff_format", sa.String(length=32), nullable=False),
        sa.Column("diff_content", sa.Text(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("significance", sa.String(length=64), nullable=True),
        sa.Column("created_from_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_snapshot_id"],
            ["code_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["to_snapshot_id"], ["code_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_from_event_id"],
            ["interview_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "from_snapshot_id"],
            ["code_snapshots.interview_session_id", "code_snapshots.id"],
            name="fk_code_diffs_session_from_snapshot",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "to_snapshot_id"],
            ["code_snapshots.interview_session_id", "code_snapshots.id"],
            name="fk_code_diffs_session_to_snapshot",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "created_from_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_code_diffs_session_created_from_event",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("interview_session_id", "id", name="uq_code_diffs_session_id"),
    )
    op.create_index(
        "ix_code_diffs_session_created_at",
        "code_diffs",
        ["interview_session_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "candidate_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin_kind", sa.String(length=32), nullable=False),
        sa.Column("source_transcript_segment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_code_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_code_diff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("verbatim_excerpt", sa.Text(), nullable=True),
        sa.Column("normalized_claim", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("extraction_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("ai_invocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ai_policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("origin_kind", CLAIM_ORIGIN_KINDS),
            name="ck_candidate_claims_origin_kind",
        ),
        sa.CheckConstraint(
            _in_values("claim_type", CLAIM_TYPES), name="ck_candidate_claims_claim_type"
        ),
        sa.CheckConstraint(_in_values("status", CLAIM_STATUSES), name="ck_candidate_claims_status"),
        sa.CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_candidate_claims_extraction_confidence_unit_interval",
        ),
        sa.CheckConstraint(
            "source_transcript_segment_id IS NOT NULL OR "
            "source_event_id IS NOT NULL OR "
            "source_code_snapshot_id IS NOT NULL OR "
            "source_code_diff_id IS NOT NULL",
            name="ck_candidate_claims_has_factual_source",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["ai_invocation_id"], ["ai_invocations.id"]),
        sa.ForeignKeyConstraint(["ai_policy_version_id"], ["ai_policy_versions.id"]),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_candidate_claims_session_ai_invocation",
        ),
        sa.UniqueConstraint("interview_session_id", "id", name="uq_candidate_claims_session_id"),
    )
    _add_set_null_fk(
        "candidate_claims",
        "fk_candidate_claims_session_transcript",
        ("interview_session_id", "source_transcript_segment_id"),
        "transcript_segments",
        ("interview_session_id", "id"),
        ("source_transcript_segment_id",),
    )
    _add_set_null_fk(
        "candidate_claims",
        "fk_candidate_claims_session_event",
        ("interview_session_id", "source_event_id"),
        "interview_events",
        ("interview_session_id", "id"),
        ("source_event_id",),
    )
    _add_set_null_fk(
        "candidate_claims",
        "fk_candidate_claims_session_code_snapshot",
        ("interview_session_id", "source_code_snapshot_id"),
        "code_snapshots",
        ("interview_session_id", "id"),
        ("source_code_snapshot_id",),
    )
    _add_set_null_fk(
        "candidate_claims",
        "fk_candidate_claims_session_code_diff",
        ("interview_session_id", "source_code_diff_id"),
        "code_diffs",
        ("interview_session_id", "id"),
        ("source_code_diff_id",),
    )

    op.create_table(
        "examiner_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("target_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_code_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposed_probe_strategy", sa.String(length=64), nullable=True),
        sa.Column("technical_rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("urgency", sa.Integer(), nullable=True),
        sa.Column("source_event_watermark", sa.BigInteger(), nullable=False),
        sa.Column("source_state_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiry_policy", sa.String(length=128), nullable=True),
        sa.Column("policy_gate_outcome", sa.String(length=64), nullable=True),
        sa.Column("policy_gate_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("ai_invocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ai_policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            _in_values("action", EXAMINER_ACTIONS), name="ck_examiner_decisions_action"
        ),
        sa.CheckConstraint(
            "proposed_probe_strategy IS NULL OR "
            f"{_in_values('proposed_probe_strategy', PROBE_STRATEGIES)}",
            name="ck_examiner_decisions_proposed_probe_strategy",
        ),
        sa.CheckConstraint(
            "(action = 'PROBE' AND proposed_probe_strategy IS NOT NULL) OR "
            "(action <> 'PROBE' AND proposed_probe_strategy IS NULL)",
            name="ck_examiner_decisions_probe_strategy_matches_action",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_examiner_decisions_confidence_unit_interval",
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority >= 0", name="ck_examiner_decisions_priority_nonnegative"
        ),
        sa.CheckConstraint(
            "urgency IS NULL OR urgency >= 0", name="ck_examiner_decisions_urgency_nonnegative"
        ),
        sa.CheckConstraint(
            "source_event_watermark >= 0",
            name="ck_examiner_decisions_source_event_watermark_nonnegative",
        ),
        sa.CheckConstraint(
            "source_state_version >= 0",
            name="ck_examiner_decisions_source_state_version_nonnegative",
        ),
        sa.CheckConstraint(
            "policy_gate_outcome IS NULL OR "
            f"{_in_values('policy_gate_outcome', POLICY_GATE_OUTCOMES)}",
            name="ck_examiner_decisions_policy_gate_outcome",
        ),
        sa.CheckConstraint(
            _in_values("status", EXAMINER_DECISION_STATUSES),
            name="ck_examiner_decisions_status",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["ai_invocation_id"], ["ai_invocations.id"]),
        sa.ForeignKeyConstraint(["ai_policy_version_id"], ["ai_policy_versions.id"]),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "ai_invocation_id"],
            ["ai_invocations.interview_session_id", "ai_invocations.id"],
            name="fk_examiner_decisions_session_ai_invocation",
        ),
        sa.UniqueConstraint(
            "interview_session_id",
            "id",
            name="uq_examiner_decisions_session_id",
        ),
    )
    _add_set_null_fk(
        "examiner_decisions",
        "fk_examiner_decisions_session_claim",
        ("interview_session_id", "target_claim_id"),
        "candidate_claims",
        ("interview_session_id", "id"),
        ("target_claim_id",),
    )
    _add_set_null_fk(
        "examiner_decisions",
        "fk_examiner_decisions_session_event",
        ("interview_session_id", "target_event_id"),
        "interview_events",
        ("interview_session_id", "id"),
        ("target_event_id",),
    )
    _add_set_null_fk(
        "examiner_decisions",
        "fk_examiner_decisions_session_code_snapshot",
        ("interview_session_id", "target_code_snapshot_id"),
        "code_snapshots",
        ("interview_session_id", "id"),
        ("target_code_snapshot_id",),
    )

    op.create_table(
        "interviewer_prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("examiner_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("probe_strategy", sa.String(length=64), nullable=True),
        sa.Column("source_stage_transition_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_concept_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_skill_dimension_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            _in_values("origin", PROMPT_ORIGINS), name="ck_interviewer_prompts_origin"
        ),
        sa.CheckConstraint(_in_values("kind", PROMPT_KINDS), name="ck_interviewer_prompts_kind"),
        sa.CheckConstraint(
            f"probe_strategy IS NULL OR {_in_values('probe_strategy', PROBE_STRATEGIES)}",
            name="ck_interviewer_prompts_probe_strategy",
        ),
        sa.CheckConstraint(
            "(kind = 'PROBE' AND probe_strategy IS NOT NULL) OR "
            "(kind <> 'PROBE' AND probe_strategy IS NULL)",
            name="ck_interviewer_prompts_probe_strategy_matches_kind",
        ),
        sa.CheckConstraint(
            _in_values("status", PROMPT_STATUSES), name="ck_interviewer_prompts_status"
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["examiner_decision_id"],
            ["examiner_decisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["target_claim_id"], ["candidate_claims.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "interview_session_id",
            "id",
            name="uq_interviewer_prompts_session_id",
        ),
    )
    _add_set_null_fk(
        "interviewer_prompts",
        "fk_interviewer_prompts_session_examiner_decision",
        ("interview_session_id", "examiner_decision_id"),
        "examiner_decisions",
        ("interview_session_id", "id"),
        ("examiner_decision_id",),
    )
    _add_set_null_fk(
        "interviewer_prompts",
        "fk_interviewer_prompts_session_claim",
        ("interview_session_id", "target_claim_id"),
        "candidate_claims",
        ("interview_session_id", "id"),
        ("target_claim_id",),
    )

    op.create_table(
        "interviewer_prompt_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interviewer_prompt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_attempt", sa.Integer(), nullable=False),
        sa.Column("intended_text", sa.Text(), nullable=False),
        sa.Column("actual_transcript_segment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("delivery_state", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interrupted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realtime_provider_event_id", sa.String(length=256), nullable=True),
        sa.Column("ai_invocation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "delivery_attempt > 0",
            name="ck_interviewer_prompt_deliveries_delivery_attempt_positive",
        ),
        sa.CheckConstraint(
            _in_values("delivery_state", DELIVERY_STATES),
            name="ck_interviewer_prompt_deliveries_delivery_state",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_interviewer_prompt_deliveries_completed_after_started",
        ),
        sa.CheckConstraint(
            "interrupted_at IS NULL OR interrupted_at >= started_at",
            name="ck_interviewer_prompt_deliveries_interrupted_after_started",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interviewer_prompt_id"],
            ["interviewer_prompts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actual_transcript_segment_id"],
            ["transcript_segments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["ai_invocation_id"], ["ai_invocations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "interviewer_prompt_id"],
            ["interviewer_prompts.interview_session_id", "interviewer_prompts.id"],
            name="fk_prompt_deliveries_session_prompt",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "interviewer_prompt_id",
            "delivery_attempt",
            name="uq_prompt_deliveries_prompt_attempt",
        ),
    )
    _add_set_null_fk(
        "interviewer_prompt_deliveries",
        "fk_prompt_deliveries_session_transcript",
        ("interview_session_id", "actual_transcript_segment_id"),
        "transcript_segments",
        ("interview_session_id", "id"),
        ("actual_transcript_segment_id",),
    )
    _add_set_null_fk(
        "interviewer_prompt_deliveries",
        "fk_prompt_deliveries_session_ai_invocation",
        ("interview_session_id", "ai_invocation_id"),
        "ai_invocations",
        ("interview_session_id", "id"),
        ("ai_invocation_id",),
    )

    op.create_table(
        "candidate_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interviewer_prompt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_reason", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_candidate_responses_ended_after_started",
        ),
        sa.CheckConstraint(
            _in_values("completion_reason", RESPONSE_COMPLETION_REASONS),
            name="ck_candidate_responses_completion_reason",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interviewer_prompt_id"],
            ["interviewer_prompts.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "interview_session_id",
            "id",
            name="uq_candidate_responses_session_id",
        ),
    )
    _add_set_null_fk(
        "candidate_responses",
        "fk_candidate_responses_session_prompt",
        ("interview_session_id", "interviewer_prompt_id"),
        "interviewer_prompts",
        ("interview_session_id", "id"),
        ("interviewer_prompt_id",),
    )

    op.create_table(
        "candidate_response_sources",
        sa.Column("interview_session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_response_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("interview_event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_role", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            _in_values("source_role", RESPONSE_SOURCE_ROLES),
            name="ck_candidate_response_sources_source_role",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_candidate_response_sources_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["interview_session_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_response_id"],
            ["candidate_responses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_event_id"],
            ["interview_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "candidate_response_id"],
            ["candidate_responses.interview_session_id", "candidate_responses.id"],
            name="fk_candidate_response_sources_session_response",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_session_id", "interview_event_id"],
            ["interview_events.interview_session_id", "interview_events.id"],
            name="fk_candidate_response_sources_session_event",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "candidate_response_id",
            "sequence",
            name="uq_candidate_response_sources_response_sequence",
        ),
    )


def downgrade() -> None:
    op.drop_table("candidate_response_sources")
    op.drop_constraint(
        "fk_candidate_responses_session_prompt",
        "candidate_responses",
        type_="foreignkey",
    )
    op.drop_table("candidate_responses")
    op.drop_constraint(
        "fk_prompt_deliveries_session_ai_invocation",
        "interviewer_prompt_deliveries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_prompt_deliveries_session_transcript",
        "interviewer_prompt_deliveries",
        type_="foreignkey",
    )
    op.drop_table("interviewer_prompt_deliveries")
    op.drop_constraint(
        "fk_interviewer_prompts_session_claim",
        "interviewer_prompts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_interviewer_prompts_session_examiner_decision",
        "interviewer_prompts",
        type_="foreignkey",
    )
    op.drop_table("interviewer_prompts")
    op.drop_constraint(
        "fk_examiner_decisions_session_code_snapshot",
        "examiner_decisions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_examiner_decisions_session_event",
        "examiner_decisions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_examiner_decisions_session_claim",
        "examiner_decisions",
        type_="foreignkey",
    )
    op.drop_table("examiner_decisions")
    op.drop_constraint(
        "fk_candidate_claims_session_code_diff",
        "candidate_claims",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_candidate_claims_session_code_snapshot",
        "candidate_claims",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_candidate_claims_session_event",
        "candidate_claims",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_candidate_claims_session_transcript",
        "candidate_claims",
        type_="foreignkey",
    )
    op.drop_table("candidate_claims")
    op.drop_index("ix_code_diffs_session_created_at", table_name="code_diffs")
    op.drop_table("code_diffs")
    op.drop_index("ix_ai_invocations_purpose_started", table_name="ai_invocations")
    op.drop_index("ix_ai_invocations_provider_model_started", table_name="ai_invocations")
    op.drop_index("ix_ai_invocations_session_purpose", table_name="ai_invocations")
    op.drop_table("ai_invocations")
    op.drop_constraint(
        "uq_transcript_segments_session_id",
        "transcript_segments",
        type_="unique",
    )


def _add_set_null_fk(
    source_table: str,
    constraint_name: str,
    source_columns: tuple[str, ...],
    target_table: str,
    target_columns: tuple[str, ...],
    null_columns: tuple[str, ...],
) -> None:
    source_cols = ", ".join(source_columns)
    target_cols = ", ".join(target_columns)
    null_cols = ", ".join(null_columns)
    op.execute(
        f"""
        ALTER TABLE {source_table}
        ADD CONSTRAINT {constraint_name}
        FOREIGN KEY ({source_cols})
        REFERENCES {target_table} ({target_cols})
        ON DELETE SET NULL ({null_cols})
        """
    )
