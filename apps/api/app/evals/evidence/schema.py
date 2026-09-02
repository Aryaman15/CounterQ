from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.evidence.assessment_schema import AssessmentAnalysisResult


class ExpectedAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensions: list[str]
    polarities: list[str]
    concept_keys: list[str]
    skill_keys: list[str]
    strengths: list[str]
    independence: str | None
    breakpoint: bool
    allow_no_findings: bool = False


class EvidenceEvaluationFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_id: str
    description: str
    model_input: dict[str, Any]
    expected: ExpectedAssessment
    forbidden_conclusions: list[str]


class EvidenceSemanticScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_finding_correctness: bool
    unsupported_finding_false_positive: bool
    polarity_appropriate: bool
    concept_target_correct: bool
    skill_target_correct: bool
    source_provenance_correct: bool
    independence_correct: bool
    strength_appropriate: bool
    trivial_error_breakpoint_suppressed: bool
    meaningful_breakpoint_detected: bool


class EvidenceEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_id: str
    output: AssessmentAnalysisResult | None
    score: EvidenceSemanticScore
    provider_status: str
    provider: str | None
    model: str | None
    capability: str
    reasoning_effort: str
    latency_ms: int | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    estimated_cost: str | None
    currency: str | None
    error_category: str | None
