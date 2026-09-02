from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class AssessmentSourceInput:
    interview_event_id: UUID
    source_role: str
    sequence: int


@dataclass(frozen=True)
class CreateAssessmentCommand:
    interview_session_id: UUID
    assessment_dimension: str
    polarity: str
    rationale: str
    confidence: Decimal
    status: str
    ai_invocation_id: UUID
    ai_policy_version_id: UUID
    evaluation_key: str | None = None
    candidate_response_id: UUID | None = None
    target_claim_id: UUID | None = None
    source_code_snapshot_id: UUID | None = None
    sources: tuple[AssessmentSourceInput, ...] = ()


@dataclass(frozen=True)
class EvidenceSourceInput:
    interview_event_id: UUID
    source_role: str


@dataclass(frozen=True)
class EvidenceConceptInput:
    concept_id: UUID
    relevance: Decimal
    is_primary: bool = False


@dataclass(frozen=True)
class EvidenceSkillInput:
    skill_dimension_id: UUID
    relevance: Decimal
    is_primary: bool = False


@dataclass(frozen=True)
class ValidateEvidenceCommand:
    interview_session_id: UUID
    assessment_id: UUID
    polarity: str
    strength: str
    confidence: Decimal
    finding: str
    independence_level: str
    validation_policy_version_id: UUID
    sources: tuple[EvidenceSourceInput, ...]
    concepts: tuple[EvidenceConceptInput, ...] = ()
    skills: tuple[EvidenceSkillInput, ...] = ()


@dataclass(frozen=True)
class EvidenceValidationFailure:
    code: str
    message: str


@dataclass(frozen=True)
class EvidenceValidationResult:
    accepted: bool
    evidence_id: UUID | None = None
    failures: tuple[EvidenceValidationFailure, ...] = ()


@dataclass(frozen=True)
class EvidenceInvalidationResult:
    evidence_id: UUID
    changed: bool
    invalidated_at: datetime
    reason: str
