from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.models import AIInvocation, AIPolicyVersion
from app.evidence.models import (
    Assessment,
    AssessmentSource,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)
from app.interviews.models import InterviewSession
from app.problems.models import Concept


async def canonical_evaluation_snapshot(
    session: AsyncSession, interview_session_id: UUID
) -> dict[str, object]:
    interview = await session.get(InterviewSession, interview_session_id)
    if interview is None:
        raise ValueError("InterviewSession was not found")

    assessments = list(
        await session.scalars(
            select(Assessment)
            .where(Assessment.interview_session_id == interview_session_id)
            .order_by(Assessment.created_at, Assessment.id)
        )
    )
    assessment_json: list[dict[str, object]] = []
    for assessment in assessments:
        sources = list(
            await session.scalars(
                select(AssessmentSource)
                .where(AssessmentSource.assessment_id == assessment.id)
                .order_by(AssessmentSource.sequence)
            )
        )
        policy = await session.get(AIPolicyVersion, assessment.ai_policy_version_id)
        invocation = await session.get(AIInvocation, assessment.ai_invocation_id)
        assessment_json.append(
            {
                "id": str(assessment.id),
                "evaluation_key": assessment.evaluation_key,
                "dimension": assessment.assessment_dimension,
                "polarity": assessment.polarity,
                "confidence": float(assessment.confidence),
                "status": assessment.status,
                "source_event_ids": [str(source.interview_event_id) for source in sources],
                "evaluator_policy": (
                    {"key": policy.policy_key, "version": policy.version} if policy else None
                ),
                "invocation": (
                    {"id": str(invocation.id), "status": invocation.status} if invocation else None
                ),
            }
        )

    evidence_rows = list(
        await session.scalars(
            select(Evidence)
            .where(Evidence.interview_session_id == interview_session_id)
            .order_by(Evidence.created_at, Evidence.id)
        )
    )
    evidence_json: list[dict[str, object]] = []
    for evidence in evidence_rows:
        sources = list(
            await session.scalars(
                select(EvidenceSource).where(EvidenceSource.evidence_id == evidence.id)
            )
        )
        concept_rows = (
            await session.execute(
                select(Concept.canonical_key)
                .join(EvidenceConcept, EvidenceConcept.concept_id == Concept.id)
                .where(EvidenceConcept.evidence_id == evidence.id)
                .order_by(EvidenceConcept.is_primary.desc(), Concept.canonical_key)
            )
        ).scalars()
        skill_rows = (
            await session.execute(
                select(SkillDimension.canonical_key)
                .join(
                    EvidenceSkill,
                    EvidenceSkill.skill_dimension_id == SkillDimension.id,
                )
                .where(EvidenceSkill.evidence_id == evidence.id)
                .order_by(EvidenceSkill.is_primary.desc(), SkillDimension.canonical_key)
            )
        ).scalars()
        validation_policy = await session.get(
            AIPolicyVersion, evidence.validation_policy_version_id
        )
        evidence_json.append(
            {
                "id": str(evidence.id),
                "assessment_id": str(evidence.originating_assessment_id),
                "polarity": evidence.polarity,
                "strength": evidence.strength,
                "independence": evidence.independence_level,
                "confidence": float(evidence.confidence),
                "finding": evidence.finding,
                "concept_keys": list(concept_rows),
                "skill_dimension_keys": list(skill_rows),
                "source_event_ids": [str(source.interview_event_id) for source in sources],
                "validation_policy": (
                    {"key": validation_policy.policy_key, "version": validation_policy.version}
                    if validation_policy
                    else None
                ),
                "active": (
                    evidence.validation_status == "VALID" and evidence.invalidated_at is None
                ),
                "validation_status": evidence.validation_status,
                "invalidated_at": (
                    evidence.invalidated_at.isoformat() if evidence.invalidated_at else None
                ),
                "invalidation_reason": evidence.invalidation_reason,
            }
        )

    breakpoints = list(
        await session.scalars(
            select(Breakpoint)
            .where(Breakpoint.user_id == interview.user_id)
            .order_by(Breakpoint.created_at, Breakpoint.id)
        )
    )
    breakpoint_json: list[dict[str, object]] = []
    for breakpoint in breakpoints:
        concept = await session.get(Concept, breakpoint.concept_id)
        skill = await session.get(SkillDimension, breakpoint.skill_dimension_id)
        links = list(
            await session.scalars(
                select(BreakpointEvidence)
                .where(BreakpointEvidence.breakpoint_id == breakpoint.id)
                .order_by(BreakpointEvidence.created_at, BreakpointEvidence.evidence_id)
            )
        )
        breakpoint_json.append(
            {
                "id": str(breakpoint.id),
                "key": breakpoint.breakpoint_key,
                "concept_key": concept.canonical_key if concept else None,
                "skill_dimension_key": skill.canonical_key if skill else None,
                "status": breakpoint.status,
                "severity": breakpoint.severity,
                "first_detected_session_id": str(breakpoint.first_detected_session_id),
                "first_detected_at": breakpoint.first_detected_at.isoformat(),
                "resolved_at": (
                    breakpoint.resolved_at.isoformat() if breakpoint.resolved_at else None
                ),
                "resolution_reason": breakpoint.resolution_reason,
                "evidence_links": [
                    {
                        "evidence_id": str(link.evidence_id),
                        "relationship": link.relationship,
                    }
                    for link in links
                ],
            }
        )
    return {
        "interview_session_id": str(interview_session_id),
        "assessments": assessment_json,
        "evidence": evidence_json,
        "breakpoints": breakpoint_json,
    }
