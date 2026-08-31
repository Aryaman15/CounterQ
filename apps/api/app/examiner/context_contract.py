from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.examiner.analysis_schema import ExaminerProbeStrategy


class StrictExaminerContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionContextSummary(StrictExaminerContextModel):
    run_status: Literal[
        "RUNNING",
        "SUCCEEDED",
        "COMPILE_ERROR",
        "RUNTIME_ERROR",
        "TIMED_OUT",
        "OUTPUT_LIMIT_EXCEEDED",
        "PROVIDER_ERROR",
    ]
    stdout: str | None = None
    stderr: str | None = None
    compiler_output: str | None = None
    execution_run_id: str | None = None
    source_run_watermark: int | None = Field(default=None, ge=1)
    code_snapshot_id: str | None = None
    code_snapshot_version: int | None = Field(default=None, ge=1)
    matches_current_code: bool
    contextual_only: bool


class RecentClaimSummary(StrictExaminerContextModel):
    normalized_claim: str
    claim_type: str
    extraction_confidence: float = Field(ge=0, le=1)
    source_event_watermark: int = Field(ge=1)


class RecentDeliveredPromptIntentSummary(StrictExaminerContextModel):
    prompt_kind: str
    strategy: ExaminerProbeStrategy | None
    target_concept_id: str | None = None
    target_claim_type: str | None = None
    target_claim: str | None = None
    target_code_snapshot_id: str | None = None
    target_code_snapshot_version: int | None = Field(default=None, ge=1)
    candidate_safe_intent: str
    delivery_state: Literal[
        "STARTED",
        "DELIVERED",
        "PARTIALLY_DELIVERED",
        "INTERRUPTED",
    ]


class SyntheticPriorContextItem(StrictExaminerContextModel):
    kind: Literal["synthetic_prior_weakness"]
    concept: str
    note: str


class SyntheticPriorContext(StrictExaminerContextModel):
    kind: Literal["evaluation_only_synthetic_context"]
    items: list[SyntheticPriorContextItem]


class ExaminerDiagnosticContext(StrictExaminerContextModel):
    """Compact Stage-4 diagnostic summaries; never canonical Evidence or history."""

    remaining_probe_budget: int = Field(ge=0)
    recent_transcript: list[str] = Field(default_factory=list, max_length=6)
    execution_context: ExecutionContextSummary | None = None
    recent_claims: list[RecentClaimSummary] = Field(default_factory=list, max_length=6)
    recent_delivered_prompt_intents: list[RecentDeliveredPromptIntentSummary] = Field(
        default_factory=list,
        max_length=6,
    )
    synthetic_prior_context: SyntheticPriorContext | None = None
