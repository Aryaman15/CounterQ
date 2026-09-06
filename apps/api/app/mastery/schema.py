"""Candidate-safe production Mastery response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

MasteryState = Literal["UNTESTED", "EXPOSED", "WEAK", "DEVELOPING", "STRONG"]


class MasteryContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateMasteryEvidenceItem(MasteryContractModel):
    evidence_id: UUID
    contribution: Literal["SUPPORTING", "CONTRADICTING", "LEARNING_LIMITED"]
    recorded_at: datetime
    problem: str
    mode: Literal["COACH", "SIMULATION"]
    candidate_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
    polarity: Literal["POSITIVE", "NEGATIVE", "MIXED"]
    strength: Literal["WEAK", "MODERATE", "STRONG"]
    independence: Literal[
        "INDEPENDENT",
        "AFTER_PROBE",
        "AFTER_LIGHT_GUIDANCE",
        "AFTER_STRONG_HINT",
        "DIRECTLY_TAUGHT",
    ]
    retest_linked: bool
    finding: str
    source_session_id: UUID


class CandidateMasteryTarget(MasteryContractModel):
    target_type: Literal["CONCEPT", "SKILL", "PARENT_SUMMARY"]
    target_id: UUID
    canonical_key: str
    display_name: str
    category: str
    state: MasteryState
    state_label: str
    evidence_sufficiency: Literal["LOW", "MEDIUM", "HIGH"]
    evidence_sufficiency_label: str
    freshness: Literal["CURRENT", "AGING", "RETEST_DUE"]
    freshness_label: str
    reason: str
    evidence_count: int
    distinct_session_count: int
    distinct_problem_count: int
    distinct_context_count: int
    retest_due: bool
    recommendation_id: UUID | None
    next_action: str
    unresolved_breakpoint_ids: list[UUID]
    evidence: list[CandidateMasteryEvidenceItem]
    child_target_ids: list[UUID]


class CandidateRetestRecommendation(MasteryContractModel):
    recommendation_id: UUID
    target_type: Literal["CONCEPT", "SKILL"]
    target_id: UUID
    target_name: str
    status: Literal["PENDING", "SCHEDULED"]
    reason: str
    action_label: str = "CounterQ me again"
    action_enabled: bool = False
    availability_message: str = "Quick Drill execution begins in Stage 8B."


class CandidateMasteryOverviewResponse(MasteryContractModel):
    status: Literal["EMPTY", "READY", "UPDATING", "FAILED"]
    user_id: UUID
    mastery_policy_version: str
    target_level: Literal["INTERN", "NEW_GRAD", "EARLY_CAREER"]
    updated_at: datetime
    message: str
    technical_concepts: list[CandidateMasteryTarget]
    parent_summaries: list[CandidateMasteryTarget]
    interview_skills: list[CandidateMasteryTarget]
    retest_recommendations: list[CandidateRetestRecommendation]


class DevelopmentMasteryFixtureResponse(MasteryContractModel):
    fixture_id: str
    label: str
    description: str
    overview: CandidateMasteryOverviewResponse


class DevelopmentMasteryInspection(MasteryContractModel):
    user_id: UUID
    mastery_policy_version: str
    recalculation_state: str
    concept_projection_count: int
    skill_projection_count: int
    concept_association_count: int
    skill_association_count: int
    transition_count: int
    recommendation_count: int
    recommendation_statuses: dict[str, int]
    state_distribution: dict[str, int]
    latest_failure_category: str | None
