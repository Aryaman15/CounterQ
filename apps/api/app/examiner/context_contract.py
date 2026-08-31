from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.examiner.analysis_schema import ExaminerProbeStrategy


class StrictExaminerContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionContextSummary(StrictExaminerContextModel):
    outcome: Literal[
        "PASSED",
        "FAILED",
        "COMPILE_ERROR",
        "RUNTIME_ERROR",
        "TIMED_OUT",
        "OUTPUT_LIMIT_EXCEEDED",
        "PROVIDER_ERROR",
    ]
    stdout: str | None = None
    stderr: str | None = None
    contextual_only: bool = True


class RecentClaimSummary(StrictExaminerContextModel):
    text: str
    transcription_confidence: float | None = Field(default=None, ge=0, le=1)


class RecentDeliveredPromptIntentSummary(StrictExaminerContextModel):
    target_concept: str
    strategy: ExaminerProbeStrategy


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
    recent_transcript: list[str] = Field(default_factory=list)
    execution_context: ExecutionContextSummary | None = None
    recent_claims: list[RecentClaimSummary] = Field(default_factory=list)
    recent_delivered_prompt_intents: list[RecentDeliveredPromptIntentSummary] = Field(
        default_factory=list
    )
    synthetic_prior_context: SyntheticPriorContext | None = None
