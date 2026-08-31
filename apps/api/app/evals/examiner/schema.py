from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.examiner.analysis_schema import ExaminerAction, ExaminerProbeStrategy, ExaminerTargetKind


class EvaluationFixture(BaseModel):
    """Versioned, evaluation-only input. Labels never enter model_input_json()."""

    model_config = ConfigDict(extra="forbid")

    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    schema_version: Literal["stage4-examiner-eval.v1"]
    description: str
    candidate_level: str
    mode: Literal["SIMULATION", "COACH"]
    state: str
    time_context: dict[str, object]
    remaining_probe_budget: int = Field(ge=0)
    problem_context: dict[str, object]
    interview_pack_excerpt: dict[str, object] | str
    source_observation_type: str
    recent_transcript: list[str] = Field(default_factory=list)
    candidate_statement: str | None = None
    code_snapshot: str | None = None
    code_diff: str | None = None
    execution_context: dict[str, object] | None = None
    recent_claims: list[dict[str, object]] = Field(default_factory=list)
    recent_delivered_prompt_intents: list[dict[str, object]] = Field(default_factory=list)
    existing_evaluation_context: list[dict[str, object]] = Field(default_factory=list)
    expected_action: ExaminerAction
    acceptable_strategies: list[ExaminerProbeStrategy] = Field(default_factory=list)
    forbidden_strategies: list[ExaminerProbeStrategy] = Field(default_factory=list)
    acceptable_target_kinds: list[ExaminerTargetKind] = Field(default_factory=list)
    forbidden_target_kinds: list[ExaminerTargetKind] = Field(default_factory=list)
    technical_rationale_expectation: str
    must_not_reveal: list[str] = Field(default_factory=list)
    manual_review_notes: str | None = None
    tags: list[str] = Field(min_length=1)
    expect_stale_suppression: bool = False
    expect_duplicate_suppression: bool = False

    @model_validator(mode="after")
    def validate_expectations(self) -> EvaluationFixture:
        if self.expected_action == "PROBE" and not self.acceptable_strategies:
            raise ValueError("PROBE fixtures require at least one acceptable strategy")
        if self.expected_action != "PROBE" and self.acceptable_strategies:
            raise ValueError("Only PROBE fixtures may specify acceptable strategies")
        if set(self.acceptable_strategies) & set(self.forbidden_strategies):
            raise ValueError("A strategy cannot be both acceptable and forbidden")
        if set(self.acceptable_target_kinds) & set(self.forbidden_target_kinds):
            raise ValueError("A target kind cannot be both acceptable and forbidden")
        return self


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
    unnecessary_probe: bool
    obvious_answer_leakage: bool
    stale_behavior_violation: bool
    duplicate_probe_violation: bool
    manual_technical_review_required: bool
    technical_rationale: str
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: str | None = None
    currency: str | None = None
