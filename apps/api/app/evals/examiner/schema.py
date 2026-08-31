from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.constants import INTERVIEW_LEVELS, INTERVIEW_STAGES
from app.examiner.analysis_schema import ExaminerAction, ExaminerProbeStrategy, ExaminerTargetKind
from app.examiner.context import ELIGIBLE_LIVE_EXAMINER_OBSERVATIONS

CandidateLevel = Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
InterviewStage = Literal[
    "SETUP",
    "INTRODUCTION",
    "PROBLEM_UNDERSTANDING",
    "APPROACH_DISCOVERY",
    "APPROACH_DEFENSE",
    "IMPLEMENTATION",
    "TESTING_DEBUGGING",
    "COMPLEXITY_EDGE_CASES",
    "CONSTRAINT_MUTATION",
    "FINAL_DEFENSE",
    "WRAP_UP",
    "COMPLETED",
]
SourceObservationType = Literal["CANDIDATE_TRANSCRIPT_FINALIZED", "CODE_MEANINGFULLY_CHANGED"]


class EvaluationInput(BaseModel):
    """Only this model is accepted by the production-parity input serializer."""

    model_config = ConfigDict(extra="forbid")
    candidate_level: CandidateLevel
    mode: Literal["SIMULATION", "COACH"]
    state: InterviewStage
    time_context: dict[str, object]
    remaining_probe_budget: int = Field(ge=0)
    problem_context: dict[str, object]
    interview_pack_excerpt: dict[str, object] | str
    source_observation_type: SourceObservationType
    recent_transcript: list[str] = Field(default_factory=list)
    candidate_statement: str | None = None
    code_snapshot: str | None = None
    code_diff: str | None = None
    execution_context: dict[str, object] | None = None
    recent_claims: list[dict[str, object]] = Field(default_factory=list)
    recent_delivered_prompt_intents: list[dict[str, object]] = Field(default_factory=list)
    evaluation_context_extension: dict[str, object] | None = None

    @model_validator(mode="after")
    def validate_domain_values(self) -> EvaluationInput:
        if self.candidate_level not in INTERVIEW_LEVELS or self.state not in INTERVIEW_STAGES:
            raise ValueError("Unknown frozen interview domain value")
        if self.source_observation_type not in ELIGIBLE_LIVE_EXAMINER_OBSERVATIONS:
            raise ValueError("Evaluation source must be Live Examiner eligible")
        return self


class TechnicalRationaleRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    technical_issue: str
    diagnostic_value: str | None = None
    false_accusation_interpretation: str | None = None
    restraint_ambiguity: str | None = None


class EvaluationExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_action: ExaminerAction
    acceptable_strategies: list[ExaminerProbeStrategy] = Field(default_factory=list)
    forbidden_strategies: list[ExaminerProbeStrategy] = Field(default_factory=list)
    acceptable_target_kinds: list[ExaminerTargetKind] = Field(default_factory=list)
    forbidden_target_kinds: list[ExaminerTargetKind] = Field(default_factory=list)
    technical_rationale_rubric: TechnicalRationaleRubric
    must_not_reveal: list[str] = Field(default_factory=list)
    expect_stale_suppression: bool = False
    expect_duplicate_suppression: bool = False
    label_sentinel: str = Field(pattern=r"^EVAL_ONLY_[A-Z0-9_]+$")

    @model_validator(mode="after")
    def validate_expectations(self) -> EvaluationExpectations:
        if self.expected_action == "PROBE" and not self.acceptable_strategies:
            raise ValueError("PROBE fixtures require at least one acceptable strategy")
        if self.expected_action != "PROBE" and self.acceptable_strategies:
            raise ValueError("Only PROBE fixtures may specify acceptable strategies")
        if set(self.acceptable_strategies) & set(self.forbidden_strategies):
            raise ValueError("A strategy cannot be both acceptable and forbidden")
        if set(self.acceptable_target_kinds) & set(self.forbidden_target_kinds):
            raise ValueError("A target kind cannot be both acceptable and forbidden")
        return self


class EvaluationReviewMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    manual_review_notes: str | None = None
    tags: list[str] = Field(min_length=1)
    requires_manual_technical_review: bool = False
    requires_candidate_specificity_review: bool = False


class EvaluationFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    schema_version: Literal["stage4-examiner-eval.v1"]
    input: EvaluationInput
    expectations: EvaluationExpectations
    review: EvaluationReviewMetadata


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id: str
    actual_action: ExaminerAction
    actual_strategy: ExaminerProbeStrategy | None
    actual_target_kind: ExaminerTargetKind
    action_correct: bool
    strategy_acceptable: bool | None
    forbidden_strategy_used: bool
    target_kind_acceptable: bool | None
    forbidden_target_kind_used: bool
    unnecessary_probe: bool
    obvious_answer_leakage: bool
    stale_behavior_violation: bool
    duplicate_probe_violation: bool
    manual_technical_review_required: bool
    candidate_specificity_review_required: bool
    technical_rationale: str
    candidate_facing_prompt: str
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: str | None = None
    currency: str | None = None
