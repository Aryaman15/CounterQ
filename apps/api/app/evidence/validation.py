from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.db.constants import (
    ASSESSMENT_DIMENSIONS,
    ASSESSMENT_STATUSES,
    EVIDENCE_INDEPENDENCE_LEVELS,
    EVIDENCE_POLARITIES,
    EVIDENCE_SOURCE_ROLES,
    EVIDENCE_STRENGTHS,
)
from app.evidence.contracts import (
    CreateAssessmentCommand,
    EvidenceInvalidationResult,
    EvidenceValidationFailure,
    EvidenceValidationResult,
    ValidateEvidenceCommand,
)
from app.evidence.models import Assessment, AssessmentSource, SkillDimension
from app.evidence.repository import EvidenceRepository
from app.examiner.models import CandidateClaim
from app.interviews.models import CandidateResponse, CandidateResponseSource, InterviewSession
from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent, TranscriptSegment
from app.problems.models import Concept

EVIDENCE_VALIDATION_POLICY_KEY = "evidence_validation"
EVIDENCE_VALIDATION_POLICY_VERSION = "v1"
EVIDENCE_VALIDATION_POLICY_CONFIGURATION: dict[str, object] = {
    "kind": "deterministic_software",
    "requires_validated_assessment": True,
    "requires_factual_event_source": True,
    "requires_canonical_target": True,
}


class AssessmentValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EvidenceInvalidationError(ValueError):
    pass


class EvidenceValidationService:
    """Deterministic boundary from interpretations to canonical Evidence.

    Methods flush but never commit. The caller owns one transaction containing the
    parent and all provenance/target rows.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = EvidenceRepository(session)

    async def ensure_validation_policy_version(self) -> AIPolicyVersion:
        policy = await self._session.scalar(
            select(AIPolicyVersion).where(
                AIPolicyVersion.policy_key == EVIDENCE_VALIDATION_POLICY_KEY,
                AIPolicyVersion.version == EVIDENCE_VALIDATION_POLICY_VERSION,
            )
        )
        if policy is not None:
            if (
                policy.prompt_hash is not None
                or policy.configuration_json != EVIDENCE_VALIDATION_POLICY_CONFIGURATION
            ):
                raise AssessmentValidationError(
                    "VALIDATION_POLICY_CONFLICT",
                    "Evidence validation policy identity has conflicting immutable semantics",
                )
            return cast(AIPolicyVersion, policy)

        policy = AIPolicyVersion(
            policy_key=EVIDENCE_VALIDATION_POLICY_KEY,
            version=EVIDENCE_VALIDATION_POLICY_VERSION,
            prompt_hash=None,
            configuration_json=EVIDENCE_VALIDATION_POLICY_CONFIGURATION,
            activated_at=datetime.now(UTC),
        )
        self._session.add(policy)
        await self._session.flush()
        return policy

    async def create_assessment(self, command: CreateAssessmentCommand) -> Assessment:
        self._validate_assessment_values(command)
        if not any(
            (
                command.candidate_response_id,
                command.target_claim_id,
                command.source_code_snapshot_id,
                command.sources,
            )
        ):
            raise AssessmentValidationError(
                "ASSESSMENT_PROVENANCE_REQUIRED",
                "Assessment requires a CandidateResponse, CandidateClaim, "
                "CodeSnapshot, or event source",
            )

        interview = await self._session.get(InterviewSession, command.interview_session_id)
        if interview is None:
            raise AssessmentValidationError("SESSION_NOT_FOUND", "InterviewSession does not exist")

        invocation = await self._session.get(AIInvocation, command.ai_invocation_id)
        if (
            invocation is None
            or invocation.interview_session_id != command.interview_session_id
            or invocation.status != "SUCCEEDED"
            or invocation.ai_policy_version_id != command.ai_policy_version_id
        ):
            raise AssessmentValidationError(
                "EVALUATOR_PROVENANCE_INVALID",
                "Assessment evaluator invocation must be successful and belong "
                "to the same session/policy",
            )
        if await self._session.get(AIPolicyVersion, command.ai_policy_version_id) is None:
            raise AssessmentValidationError(
                "EVALUATOR_POLICY_NOT_FOUND", "Assessment evaluator policy does not exist"
            )

        await self._require_same_session(
            CandidateResponse,
            command.candidate_response_id,
            command.interview_session_id,
            "CANDIDATE_RESPONSE_SESSION_MISMATCH",
        )
        await self._require_same_session(
            CandidateClaim,
            command.target_claim_id,
            command.interview_session_id,
            "CANDIDATE_CLAIM_SESSION_MISMATCH",
        )
        await self._require_same_session(
            CodeSnapshot,
            command.source_code_snapshot_id,
            command.interview_session_id,
            "CODE_SNAPSHOT_SESSION_MISMATCH",
        )

        source_event_ids = [source.interview_event_id for source in command.sources]
        if len(source_event_ids) != len(set(source_event_ids)):
            raise AssessmentValidationError(
                "DUPLICATE_ASSESSMENT_SOURCE", "Assessment event sources must be unique"
            )
        sequences = [source.sequence for source in command.sources]
        if any(sequence <= 0 for sequence in sequences) or len(sequences) != len(set(sequences)):
            raise AssessmentValidationError(
                "INVALID_ASSESSMENT_SOURCE_SEQUENCE",
                "Assessment source sequences must be positive and unique",
            )
        if any(source.source_role not in EVIDENCE_SOURCE_ROLES for source in command.sources):
            raise AssessmentValidationError(
                "INVALID_ASSESSMENT_SOURCE_ROLE", "Assessment source role is not supported"
            )
        await self._require_events_in_session(source_event_ids, command.interview_session_id)

        return await self._repository.add_assessment(command)

    async def validate_into_evidence(
        self, command: ValidateEvidenceCommand
    ) -> EvidenceValidationResult:
        failures: list[EvidenceValidationFailure] = []
        assessment = await self._repository.assessment(command.assessment_id)
        if assessment is None:
            failures.append(_failure("ASSESSMENT_NOT_FOUND", "Assessment does not exist"))
            return _rejected(failures)
        if assessment.interview_session_id != command.interview_session_id:
            failures.append(
                _failure("ASSESSMENT_SESSION_MISMATCH", "Assessment belongs to another session")
            )
        if assessment.status != "VALIDATED":
            failures.append(
                _failure(
                    "ASSESSMENT_NOT_VALIDATED",
                    "Only a VALIDATED Assessment is eligible for Evidence validation",
                )
            )

        failures.extend(await self._validate_evaluator_provenance(assessment))
        failures.extend(
            await self._validate_validation_policy(command.validation_policy_version_id)
        )
        failures.extend(self._validate_evidence_values(command))
        failures.extend(await self._validate_targets(command))

        requested_source_ids = [source.interview_event_id for source in command.sources]
        if not requested_source_ids:
            failures.append(
                _failure(
                    "FACTUAL_SOURCE_REQUIRED",
                    "Canonical Evidence requires at least one InterviewEvent source",
                )
            )
        elif len(requested_source_ids) != len(set(requested_source_ids)):
            failures.append(
                _failure("DUPLICATE_EVIDENCE_SOURCE", "Evidence event sources must be unique")
            )
        else:
            source_events = await self._events(requested_source_ids)
            if len(source_events) != len(requested_source_ids):
                failures.append(
                    _failure("FACTUAL_SOURCE_NOT_FOUND", "One or more InterviewEvents do not exist")
                )
            elif any(
                event.interview_session_id != command.interview_session_id
                for event in source_events
            ):
                failures.append(
                    _failure(
                        "SOURCE_SESSION_MISMATCH",
                        "Every Evidence source must belong to the Evidence session",
                    )
                )
            elif any(source.source_role not in EVIDENCE_SOURCE_ROLES for source in command.sources):
                failures.append(
                    _failure("INVALID_SOURCE_ROLE", "Evidence source role is not supported")
                )
            else:
                assessment_event_ids = await self._assessment_factual_event_ids(assessment)
                if not assessment_event_ids:
                    failures.append(
                        _failure(
                            "ASSESSMENT_FACTUAL_PROVENANCE_MISSING",
                            "Assessment no longer resolves to factual InterviewEvent provenance",
                        )
                    )
                elif assessment_event_ids.isdisjoint(requested_source_ids):
                    failures.append(
                        _failure(
                            "SOURCE_NOT_ASSESSMENT_PROVENANCE",
                            "Evidence must retain at least one factual source "
                            "used by its Assessment",
                        )
                    )

        if failures:
            return _rejected(failures)

        evidence = await self._repository.add_evidence(
            interview_session_id=command.interview_session_id,
            evidence_type=assessment.assessment_dimension,
            polarity=command.polarity,
            strength=command.strength,
            confidence=command.confidence,
            finding=command.finding.strip(),
            independence_level=command.independence_level,
            originating_assessment_id=assessment.id,
            validation_policy_version_id=command.validation_policy_version_id,
            sources=command.sources,
            concepts=command.concepts,
            skills=command.skills,
        )
        return EvidenceValidationResult(accepted=True, evidence_id=evidence.id)

    async def invalidate(
        self,
        *,
        interview_session_id: UUID,
        evidence_id: UUID,
        reason: str,
        invalidated_at: datetime | None = None,
    ) -> EvidenceInvalidationResult:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise EvidenceInvalidationError("Evidence invalidation requires a reason")
        evidence = await self._repository.lock_evidence(evidence_id)
        if evidence is None or evidence.interview_session_id != interview_session_id:
            raise EvidenceInvalidationError("Evidence does not exist in the requested session")
        if evidence.validation_status == "INVALIDATED":
            assert evidence.invalidated_at is not None
            assert evidence.invalidation_reason is not None
            return EvidenceInvalidationResult(
                evidence_id=evidence.id,
                changed=False,
                invalidated_at=evidence.invalidated_at,
                reason=evidence.invalidation_reason,
            )
        if evidence.validation_status != "VALID":
            raise EvidenceInvalidationError("Only active VALID Evidence can be invalidated")

        effective_time = invalidated_at or datetime.now(UTC)
        evidence.validation_status = "INVALIDATED"
        evidence.invalidated_at = effective_time
        evidence.invalidation_reason = normalized_reason
        await self._session.flush()
        return EvidenceInvalidationResult(
            evidence_id=evidence.id,
            changed=True,
            invalidated_at=effective_time,
            reason=normalized_reason,
        )

    @staticmethod
    def _validate_assessment_values(command: CreateAssessmentCommand) -> None:
        if command.assessment_dimension not in ASSESSMENT_DIMENSIONS:
            raise AssessmentValidationError(
                "INVALID_ASSESSMENT_DIMENSION", "Assessment dimension is not supported"
            )
        if command.polarity not in EVIDENCE_POLARITIES:
            raise AssessmentValidationError(
                "INVALID_ASSESSMENT_POLARITY", "Assessment polarity is not supported"
            )
        if command.status not in ASSESSMENT_STATUSES:
            raise AssessmentValidationError(
                "INVALID_ASSESSMENT_STATUS", "Assessment status is not supported"
            )
        if not Decimal("0") <= command.confidence <= Decimal("1"):
            raise AssessmentValidationError(
                "INVALID_ASSESSMENT_CONFIDENCE", "Assessment confidence must be in [0, 1]"
            )
        if not command.rationale.strip():
            raise AssessmentValidationError(
                "ASSESSMENT_RATIONALE_REQUIRED", "Assessment rationale cannot be empty"
            )

    async def _validate_evaluator_provenance(
        self, assessment: Assessment
    ) -> list[EvidenceValidationFailure]:
        invocation = await self._session.get(AIInvocation, assessment.ai_invocation_id)
        policy = await self._session.get(AIPolicyVersion, assessment.ai_policy_version_id)
        if (
            invocation is None
            or policy is None
            or invocation.status != "SUCCEEDED"
            or invocation.interview_session_id != assessment.interview_session_id
            or invocation.ai_policy_version_id != assessment.ai_policy_version_id
        ):
            return [
                _failure(
                    "EVALUATOR_PROVENANCE_INVALID",
                    "Assessment evaluator invocation/policy provenance is invalid",
                )
            ]
        return []

    async def _validate_validation_policy(
        self, validation_policy_version_id: UUID
    ) -> list[EvidenceValidationFailure]:
        policy = await self._session.get(AIPolicyVersion, validation_policy_version_id)
        if (
            policy is None
            or policy.policy_key != EVIDENCE_VALIDATION_POLICY_KEY
            or policy.version != EVIDENCE_VALIDATION_POLICY_VERSION
            or policy.prompt_hash is not None
            or policy.configuration_json != EVIDENCE_VALIDATION_POLICY_CONFIGURATION
        ):
            return [
                _failure(
                    "VALIDATION_POLICY_INVALID",
                    "Evidence requires the dedicated deterministic evidence validation policy",
                )
            ]
        return []

    @staticmethod
    def _validate_evidence_values(
        command: ValidateEvidenceCommand,
    ) -> list[EvidenceValidationFailure]:
        failures: list[EvidenceValidationFailure] = []
        if command.polarity not in EVIDENCE_POLARITIES:
            failures.append(_failure("INVALID_POLARITY", "Evidence polarity is not supported"))
        if command.strength not in EVIDENCE_STRENGTHS:
            failures.append(_failure("INVALID_STRENGTH", "Evidence strength is not supported"))
        if command.independence_level not in EVIDENCE_INDEPENDENCE_LEVELS:
            failures.append(
                _failure("INVALID_INDEPENDENCE", "Evidence independence level is not supported")
            )
        if not Decimal("0") <= command.confidence <= Decimal("1"):
            failures.append(_failure("INVALID_CONFIDENCE", "Evidence confidence must be in [0, 1]"))
        if not command.finding.strip():
            failures.append(_failure("FINDING_REQUIRED", "Evidence finding cannot be empty"))
        return failures

    async def _validate_targets(
        self, command: ValidateEvidenceCommand
    ) -> list[EvidenceValidationFailure]:
        failures: list[EvidenceValidationFailure] = []
        if not command.concepts and not command.skills:
            return [
                _failure(
                    "CANONICAL_TARGET_REQUIRED",
                    "Evidence requires at least one canonical Concept or SkillDimension",
                )
            ]
        concept_ids = [target.concept_id for target in command.concepts]
        skill_ids = [target.skill_dimension_id for target in command.skills]
        if len(concept_ids) != len(set(concept_ids)):
            failures.append(_failure("DUPLICATE_CONCEPT", "Evidence Concepts must be unique"))
        if len(skill_ids) != len(set(skill_ids)):
            failures.append(_failure("DUPLICATE_SKILL", "Evidence Skills must be unique"))
        if sum(target.is_primary for target in command.concepts) > 1:
            failures.append(
                _failure("MULTIPLE_PRIMARY_CONCEPTS", "Evidence may have one primary Concept")
            )
        if sum(target.is_primary for target in command.skills) > 1:
            failures.append(
                _failure("MULTIPLE_PRIMARY_SKILLS", "Evidence may have one primary SkillDimension")
            )
        concept_relevance_invalid = any(
            not Decimal("0") < target.relevance <= Decimal("1") for target in command.concepts
        )
        skill_relevance_invalid = any(
            not Decimal("0") < target.relevance <= Decimal("1") for target in command.skills
        )
        if concept_relevance_invalid or skill_relevance_invalid:
            failures.append(_failure("INVALID_RELEVANCE", "Target relevance must be in (0, 1]"))

        if concept_ids:
            concepts = list(
                await self._session.scalars(select(Concept).where(Concept.id.in_(concept_ids)))
            )
            if len(concepts) != len(set(concept_ids)) or any(
                concept.status != "ACTIVE" for concept in concepts
            ):
                failures.append(
                    _failure(
                        "CONCEPT_NOT_CANONICAL",
                        "Every Evidence Concept must exist and be active in the curated ontology",
                    )
                )
        if skill_ids:
            skills = list(
                await self._session.scalars(
                    select(SkillDimension).where(SkillDimension.id.in_(skill_ids))
                )
            )
            if len(skills) != len(set(skill_ids)) or any(
                skill.status != "ACTIVE" for skill in skills
            ):
                failures.append(
                    _failure(
                        "SKILL_NOT_CANONICAL",
                        "Every Evidence SkillDimension must exist and be active",
                    )
                )
        return failures

    async def _assessment_factual_event_ids(self, assessment: Assessment) -> set[UUID]:
        event_ids = set(
            await self._session.scalars(
                select(AssessmentSource.interview_event_id).where(
                    AssessmentSource.assessment_id == assessment.id
                )
            )
        )
        if assessment.candidate_response_id is not None:
            event_ids.update(
                await self._session.scalars(
                    select(CandidateResponseSource.interview_event_id).where(
                        CandidateResponseSource.candidate_response_id
                        == assessment.candidate_response_id
                    )
                )
            )
        if assessment.source_code_snapshot_id is not None:
            snapshot = await self._session.get(CodeSnapshot, assessment.source_code_snapshot_id)
            if snapshot is not None:
                event_ids.add(snapshot.created_from_event_id)
        if assessment.target_claim_id is not None:
            claim = await self._session.get(CandidateClaim, assessment.target_claim_id)
            if claim is not None:
                if claim.source_event_id is not None:
                    event_ids.add(claim.source_event_id)
                if claim.source_transcript_segment_id is not None:
                    segment = await self._session.get(
                        TranscriptSegment, claim.source_transcript_segment_id
                    )
                    if segment is not None:
                        event_ids.add(segment.interview_event_id)
                if claim.source_code_snapshot_id is not None:
                    snapshot = await self._session.get(CodeSnapshot, claim.source_code_snapshot_id)
                    if snapshot is not None:
                        event_ids.add(snapshot.created_from_event_id)
                if claim.source_code_diff_id is not None:
                    code_diff = await self._session.get(CodeDiff, claim.source_code_diff_id)
                    if code_diff is not None:
                        event_ids.add(code_diff.created_from_event_id)
        return event_ids

    async def _require_same_session(
        self,
        model: type[CandidateResponse] | type[CandidateClaim] | type[CodeSnapshot],
        entity_id: UUID | None,
        interview_session_id: UUID,
        code: str,
    ) -> None:
        if entity_id is None:
            return
        entity = await self._session.get(model, entity_id)
        if entity is None or getattr(entity, "interview_session_id", None) != interview_session_id:
            raise AssessmentValidationError(
                code, "Assessment provenance belongs to another session"
            )

    async def _require_events_in_session(
        self, event_ids: list[UUID], interview_session_id: UUID
    ) -> None:
        if not event_ids:
            return
        events = await self._events(event_ids)
        if len(events) != len(event_ids) or any(
            event.interview_session_id != interview_session_id for event in events
        ):
            raise AssessmentValidationError(
                "ASSESSMENT_SOURCE_SESSION_MISMATCH",
                "Assessment event sources must exist in the Assessment session",
            )

    async def _events(self, event_ids: list[UUID]) -> list[InterviewEvent]:
        return list(
            await self._session.scalars(
                select(InterviewEvent).where(InterviewEvent.id.in_(event_ids))
            )
        )


def _failure(code: str, message: str) -> EvidenceValidationFailure:
    return EvidenceValidationFailure(code=code, message=message)


def _rejected(failures: list[EvidenceValidationFailure]) -> EvidenceValidationResult:
    return EvidenceValidationResult(accepted=False, failures=tuple(failures))
