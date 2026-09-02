from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence.contracts import (
    AssessmentSourceInput,
    CreateAssessmentCommand,
    EvidenceConceptInput,
    EvidenceSkillInput,
    EvidenceSourceInput,
)
from app.evidence.models import (
    Assessment,
    AssessmentSource,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)


class EvidenceRepository:
    """Stage 5 persistence primitives; callers own the surrounding transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_assessment(self, command: CreateAssessmentCommand) -> Assessment:
        assessment = Assessment(
            interview_session_id=command.interview_session_id,
            candidate_response_id=command.candidate_response_id,
            target_claim_id=command.target_claim_id,
            source_code_snapshot_id=command.source_code_snapshot_id,
            assessment_dimension=command.assessment_dimension,
            polarity=command.polarity,
            rationale=command.rationale,
            confidence=command.confidence,
            status=command.status,
            evaluation_key=command.evaluation_key,
            ai_invocation_id=command.ai_invocation_id,
            ai_policy_version_id=command.ai_policy_version_id,
        )
        self._session.add(assessment)
        await self._session.flush()
        await self.add_assessment_sources(
            assessment_id=assessment.id,
            interview_session_id=assessment.interview_session_id,
            sources=command.sources,
        )
        return assessment

    async def add_assessment_sources(
        self,
        *,
        assessment_id: UUID,
        interview_session_id: UUID,
        sources: tuple[AssessmentSourceInput, ...],
    ) -> None:
        self._session.add_all(
            AssessmentSource(
                assessment_id=assessment_id,
                interview_event_id=source.interview_event_id,
                interview_session_id=interview_session_id,
                source_role=source.source_role,
                sequence=source.sequence,
            )
            for source in sources
        )
        if sources:
            await self._session.flush()

    async def assessment(self, assessment_id: UUID) -> Assessment | None:
        value = await self._session.get(Assessment, assessment_id)
        return cast(Assessment | None, value)

    async def assessment_by_evaluation_key(
        self, *, interview_session_id: UUID, evaluation_key: str
    ) -> Assessment | None:
        value = await self._session.scalar(
            select(Assessment).where(
                Assessment.interview_session_id == interview_session_id,
                Assessment.evaluation_key == evaluation_key,
            )
        )
        return cast(Assessment | None, value)

    async def add_evidence(
        self,
        *,
        interview_session_id: UUID,
        evidence_type: str,
        polarity: str,
        strength: str,
        confidence: Decimal,
        finding: str,
        independence_level: str,
        originating_assessment_id: UUID,
        validation_policy_version_id: UUID,
        sources: tuple[EvidenceSourceInput, ...],
        concepts: tuple[EvidenceConceptInput, ...],
        skills: tuple[EvidenceSkillInput, ...],
    ) -> Evidence:
        evidence = Evidence(
            interview_session_id=interview_session_id,
            evidence_type=evidence_type,
            polarity=polarity,
            strength=strength,
            confidence=confidence,
            finding=finding,
            independence_level=independence_level,
            validation_status="VALID",
            originating_assessment_id=originating_assessment_id,
            validation_policy_version_id=validation_policy_version_id,
        )
        self._session.add(evidence)
        await self._session.flush()
        self._session.add_all(
            EvidenceSource(
                evidence_id=evidence.id,
                interview_event_id=source.interview_event_id,
                interview_session_id=interview_session_id,
                source_role=source.source_role,
            )
            for source in sources
        )
        self._session.add_all(
            EvidenceConcept(
                evidence_id=evidence.id,
                concept_id=target.concept_id,
                relevance=target.relevance,
                is_primary=target.is_primary,
            )
            for target in concepts
        )
        self._session.add_all(
            EvidenceSkill(
                evidence_id=evidence.id,
                skill_dimension_id=target.skill_dimension_id,
                relevance=target.relevance,
                is_primary=target.is_primary,
            )
            for target in skills
        )
        await self._session.flush()
        return evidence

    async def lock_evidence(self, evidence_id: UUID) -> Evidence | None:
        value = await self._session.scalar(
            select(Evidence).where(Evidence.id == evidence_id).with_for_update()
        )
        return cast(Evidence | None, value)

    async def active_for_session(self, interview_session_id: UUID) -> list[Evidence]:
        values = await self._session.scalars(
            select(Evidence)
            .where(Evidence.interview_session_id == interview_session_id)
            .where(Evidence.validation_status == "VALID")
            .where(Evidence.invalidated_at.is_(None))
            .order_by(Evidence.created_at, Evidence.id)
        )
        return list(values)

    async def skill_dimension(self, skill_dimension_id: UUID) -> SkillDimension | None:
        value = await self._session.get(SkillDimension, skill_dimension_id)
        return cast(SkillDimension | None, value)

    async def skill_dimension_by_key(self, canonical_key: str) -> SkillDimension | None:
        value = await self._session.scalar(
            select(SkillDimension).where(SkillDimension.canonical_key == canonical_key)
        )
        return cast(SkillDimension | None, value)
