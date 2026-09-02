from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import AIGateway, AIGatewayError
from app.ai_gateway.models import AIPolicyVersion
from app.ai_gateway.provider import ReasoningProviderError
from app.evidence.assessment_schema import AssessmentAnalysisResult, AssessmentFinding
from app.evidence.breakpoints import (
    BreakpointCandidate,
    BreakpointPolicyError,
    BreakpointService,
)
from app.evidence.contracts import (
    AssessmentSourceInput,
    CreateAssessmentCommand,
    EvidenceConceptInput,
    EvidenceSkillInput,
    EvidenceSourceInput,
    ValidateEvidenceCommand,
)
from app.evidence.models import (
    Assessment,
    AssessmentSource,
    BreakpointEvidence,
    Evidence,
)
from app.evidence.policy import (
    ASSESSMENT_EVALUATOR_INSTRUCTIONS,
    ASSESSMENT_EVALUATOR_POLICY_KEY,
    ASSESSMENT_EVALUATOR_POLICY_VERSION,
    assessment_evaluator_policy_descriptor,
)
from app.evidence.repository import EvidenceRepository
from app.evidence.source_admission import evidence_source_admission
from app.evidence.units import AssessmentInputBuilder, AssessmentSourceFact, AssessmentUnit
from app.evidence.validation import EvidenceValidationService
from app.interviews.models import CandidateResponse, InterviewSession

UnitStatus = Literal["COMPLETED", "SKIPPED", "FAILED"]


@dataclass(frozen=True)
class UnitEvaluationResult:
    unit_key: str
    unit_kind: str
    status: UnitStatus
    assessment_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    breakpoint_ids: tuple[UUID, ...] = ()
    error_category: str | None = None


@dataclass(frozen=True)
class SessionEvaluationResult:
    interview_session_id: UUID
    units: tuple[UnitEvaluationResult, ...]

    @property
    def completed_units(self) -> int:
        return sum(unit.status == "COMPLETED" for unit in self.units)

    @property
    def failed_units(self) -> int:
        return sum(unit.status == "FAILED" for unit in self.units)

    @property
    def skipped_units(self) -> int:
        return sum(unit.status == "SKIPPED" for unit in self.units)


class SessionEvidenceEvaluationCoordinator:
    """Explicit Stage 5 application service; it creates no later-stage projection."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        ai_gateway: AIGateway,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._gateway = ai_gateway

    async def evaluate(self, interview_session_id: UUID) -> SessionEvaluationResult:
        # The read transaction is closed before any AI work begins.
        async with self._sessionmaker() as read_session:
            units = await AssessmentInputBuilder(read_session).build_completed_simulation(
                interview_session_id
            )
            existing_results = {
                unit.unit_key: result
                for unit in units
                if (result := await _existing_unit_result(read_session, unit)) is not None
            }

        results: list[UnitEvaluationResult] = []
        for unit in units:
            existing_result = existing_results.get(unit.unit_key)
            if existing_result is not None:
                results.append(existing_result)
                continue
            if unit.independence_level is None:
                results.append(
                    UnitEvaluationResult(
                        unit_key=unit.unit_key,
                        unit_kind=unit.kind.value,
                        status="SKIPPED",
                        error_category="INDEPENDENCE_UNRESOLVED",
                    )
                )
                continue
            try:
                gateway_result = await self._gateway.reason_structured(
                    interview_session_id=interview_session_id,
                    capability="STANDARD_REASONING",
                    purpose="post_interview_assessment",
                    policy=assessment_evaluator_policy_descriptor(),
                    instructions=ASSESSMENT_EVALUATOR_INSTRUCTIONS,
                    input_content=unit.serialize(),
                    output_model=AssessmentAnalysisResult,
                    correlation_id=unit.unit_key,
                    metadata={
                        "assessment_unit_key": unit.unit_key,
                        "assessment_unit_kind": unit.kind.value,
                    },
                )
            except (AIGatewayError, ReasoningProviderError) as exc:
                results.append(
                    UnitEvaluationResult(
                        unit_key=unit.unit_key,
                        unit_kind=unit.kind.value,
                        status="FAILED",
                        error_category=getattr(exc, "category", "AI_GATEWAY_ERROR"),
                    )
                )
                continue

            try:
                result = await self._persist_result(
                    original_unit=unit,
                    analysis=gateway_result.parsed,
                    invocation_id=gateway_result.invocation_id,
                    evaluator_policy_version_id=gateway_result.policy_version_id,
                )
            except Exception as exc:
                # A failed deterministic admission for one unit cannot fabricate
                # Evidence and does not prevent independent later units.
                results.append(
                    UnitEvaluationResult(
                        unit_key=unit.unit_key,
                        unit_kind=unit.kind.value,
                        status="FAILED",
                        error_category=type(exc).__name__,
                    )
                )
                continue
            results.append(result)
        return SessionEvaluationResult(interview_session_id, tuple(results))

    async def _persist_result(
        self,
        *,
        original_unit: AssessmentUnit,
        analysis: AssessmentAnalysisResult,
        invocation_id: UUID,
        evaluator_policy_version_id: UUID,
    ) -> UnitEvaluationResult:
        assessment_ids: list[UUID] = []
        evidence_ids: list[UUID] = []
        breakpoint_ids: list[UUID] = []
        async with self._sessionmaker() as session:
            async with session.begin():
                locked_session = await session.scalar(
                    select(InterviewSession)
                    .where(InterviewSession.id == original_unit.interview_session_id)
                    .with_for_update()
                )
                if locked_session is None:
                    raise ValueError("InterviewSession disappeared during Assessment")
                fresh_units = await AssessmentInputBuilder(session).build_completed_simulation(
                    original_unit.interview_session_id
                )
                fresh_unit = next(
                    (item for item in fresh_units if item.unit_key == original_unit.unit_key), None
                )
                if fresh_unit is None or fresh_unit.serialize() != original_unit.serialize():
                    raise ValueError("AssessmentUnit changed before deterministic admission")
                if fresh_unit.independence_level is None:
                    raise ValueError("AssessmentUnit independence became unresolved")

                validation = EvidenceValidationService(session)
                validation_policy = await validation.ensure_validation_policy_version()
                repository = EvidenceRepository(session)
                for finding in analysis.findings:
                    outcome = await self._persist_finding(
                        session=session,
                        repository=repository,
                        validation=validation,
                        validation_policy_version_id=validation_policy.id,
                        unit=fresh_unit,
                        finding=finding,
                        invocation_id=invocation_id,
                        evaluator_policy_version_id=evaluator_policy_version_id,
                    )
                    assessment_ids.append(outcome[0])
                    if outcome[1] is not None:
                        evidence_ids.append(outcome[1])
                    breakpoint_ids.extend(outcome[2])
        return UnitEvaluationResult(
            unit_key=original_unit.unit_key,
            unit_kind=original_unit.kind.value,
            status="COMPLETED",
            assessment_ids=tuple(dict.fromkeys(assessment_ids)),
            evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            breakpoint_ids=tuple(dict.fromkeys(breakpoint_ids)),
        )

    async def _persist_finding(
        self,
        *,
        session: AsyncSession,
        repository: EvidenceRepository,
        validation: EvidenceValidationService,
        validation_policy_version_id: UUID,
        unit: AssessmentUnit,
        finding: AssessmentFinding,
        invocation_id: UUID,
        evaluator_policy_version_id: UUID,
    ) -> tuple[UUID, UUID | None, tuple[UUID, ...]]:
        aliases = {fact.alias: fact for fact in unit.sources}
        selected = [aliases[alias] for alias in finding.source_aliases if alias in aliases]
        valid_selection = (
            len(finding.source_aliases) == len(set(finding.source_aliases))
            and len(selected) == len(finding.source_aliases)
            and _sources_admitted(selected)
        )
        concepts_valid = all(key in unit.concept_ids_by_key for key in finding.concept_keys)
        skills_valid = all(key in unit.skill_ids_by_key for key in finding.skill_dimension_keys)
        targets_valid = bool(finding.concept_keys or finding.skill_dimension_keys)
        breakpoint_targets_valid = finding.breakpoint_effect == "NONE" or (
            len(finding.concept_keys) == 1 and len(finding.skill_dimension_keys) == 1
        )
        evaluation_key = assessment_evaluation_key(
            unit=unit,
            finding=finding,
            selected_sources=selected if valid_selection else list(unit.sources),
        )
        existing = await repository.assessment_by_evaluation_key(
            interview_session_id=unit.interview_session_id,
            evaluation_key=evaluation_key,
        )
        if existing is not None:
            evidence_id = await session.scalar(
                select(Evidence.id).where(Evidence.originating_assessment_id == existing.id)
            )
            links = (
                tuple(
                    await session.scalars(
                        select(BreakpointEvidence.breakpoint_id).where(
                            BreakpointEvidence.evidence_id == evidence_id
                        )
                    )
                )
                if evidence_id is not None
                else ()
            )
            return existing.id, cast(UUID | None, evidence_id), links

        assessment = await validation.create_assessment(
            CreateAssessmentCommand(
                interview_session_id=unit.interview_session_id,
                candidate_response_id=unit.candidate_response_id,
                source_code_snapshot_id=unit.source_code_snapshot_id,
                assessment_dimension=finding.assessment_dimension,
                polarity=finding.polarity,
                rationale=finding.technical_rationale,
                confidence=Decimal(str(finding.confidence)),
                status="PROPOSED",
                evaluation_key=evaluation_key,
                ai_invocation_id=invocation_id,
                ai_policy_version_id=evaluator_policy_version_id,
                sources=tuple(
                    AssessmentSourceInput(
                        interview_event_id=fact.event_id,
                        source_role=fact.source_role,
                        sequence=index,
                    )
                    for index, fact in enumerate(unit.sources, start=1)
                ),
            )
        )
        await session.flush()  # Level-B PROPOSED exists before admission.
        response_valid = await _response_is_final(session, unit.candidate_response_id)
        evaluator_valid = await _evaluator_policy_is_exact(session, assessment.ai_policy_version_id)
        if not (
            valid_selection
            and concepts_valid
            and skills_valid
            and targets_valid
            and breakpoint_targets_valid
            and response_valid
            and evaluator_valid
        ):
            assessment.status = "REJECTED"
            await session.flush()
            return assessment.id, None, ()

        assessment.status = "VALIDATED"
        await session.flush()
        evidence_result = await validation.validate_into_evidence(
            ValidateEvidenceCommand(
                interview_session_id=unit.interview_session_id,
                assessment_id=assessment.id,
                polarity=finding.polarity,
                strength=finding.proposed_strength,
                confidence=Decimal(str(finding.confidence)),
                finding=finding.evidence_finding,
                independence_level=cast(str, unit.independence_level),
                validation_policy_version_id=validation_policy_version_id,
                sources=tuple(
                    EvidenceSourceInput(fact.event_id, fact.source_role) for fact in selected
                ),
                concepts=tuple(
                    EvidenceConceptInput(
                        concept_id=unit.concept_ids_by_key[key],
                        relevance=Decimal("1.0"),
                        is_primary=index == 0,
                    )
                    for index, key in enumerate(finding.concept_keys)
                ),
                skills=tuple(
                    EvidenceSkillInput(
                        skill_dimension_id=unit.skill_ids_by_key[key],
                        relevance=Decimal("1.0"),
                        is_primary=index == 0,
                    )
                    for index, key in enumerate(finding.skill_dimension_keys)
                ),
            )
        )
        if not evidence_result.accepted or evidence_result.evidence_id is None:
            assessment.status = "REJECTED"
            await session.flush()
            return assessment.id, None, ()

        links = await self._orchestrate_breakpoint(
            session=session,
            unit=unit,
            finding=finding,
            evidence_id=evidence_result.evidence_id,
        )
        return assessment.id, evidence_result.evidence_id, links

    async def _orchestrate_breakpoint(
        self,
        *,
        session: AsyncSession,
        unit: AssessmentUnit,
        finding: AssessmentFinding,
        evidence_id: UUID,
    ) -> tuple[UUID, ...]:
        if not finding.concept_keys or not finding.skill_dimension_keys:
            return ()
        concept_id = unit.concept_ids_by_key[finding.concept_keys[0]]
        skill_id = unit.skill_ids_by_key[finding.skill_dimension_keys[0]]
        interview = await session.get(InterviewSession, unit.interview_session_id)
        assert interview is not None
        service = BreakpointService(session)
        if finding.breakpoint_effect == "WEAKNESS":
            try:
                result = await service.create_or_reinforce(
                    BreakpointCandidate(
                        user_id=interview.user_id,
                        interview_session_id=interview.id,
                        concept_id=concept_id,
                        skill_dimension_id=skill_id,
                        assessment_dimension=finding.assessment_dimension,
                        evidence_ids=(evidence_id,),
                        boundary_kind=finding.boundary_kind,
                        summary=finding.evidence_finding,
                        severity=cast(str, finding.breakpoint_severity),
                        known_subtype=finding.breakpoint_subtype,
                    )
                )
            except BreakpointPolicyError:
                return ()
            return (result.breakpoint_id,) if result.breakpoint_id is not None else ()
        if finding.breakpoint_effect not in ("CONTRADICTED", "RESOLUTION_SUPPORT"):
            return ()
        breakpoint_id = await service.link_evidence_to_active_boundary(
            user_id=interview.user_id,
            concept_id=concept_id,
            skill_dimension_id=skill_id,
            assessment_dimension=finding.assessment_dimension,
            known_subtype=finding.breakpoint_subtype,
            evidence_id=evidence_id,
            relationship=finding.breakpoint_effect,
        )
        return (breakpoint_id,) if breakpoint_id is not None else ()


def assessment_evaluation_key(
    *,
    unit: AssessmentUnit,
    finding: AssessmentFinding,
    selected_sources: list[AssessmentSourceFact],
) -> str:
    identity = {
        "unit_key": unit.unit_key,
        "evaluator_policy": (
            f"{ASSESSMENT_EVALUATOR_POLICY_KEY}.{ASSESSMENT_EVALUATOR_POLICY_VERSION}"
        ),
        "assessment_dimension": finding.assessment_dimension,
        "source_event_ids": sorted(str(source.event_id) for source in selected_sources),
        "concept_keys": sorted(finding.concept_keys),
        "skill_dimension_keys": sorted(finding.skill_dimension_keys),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _sources_admitted(sources: list[AssessmentSourceFact]) -> bool:
    candidate_demonstration = False
    for source in sources:
        admission = evidence_source_admission(
            event_type=source.event_type,
            event_source=source.event_source,
            source_role=source.source_role,
        )
        if not admission.admitted:
            return False
        candidate_demonstration = (
            candidate_demonstration or admission.counts_as_candidate_demonstration
        )
    return candidate_demonstration


async def _response_is_final(session: AsyncSession, response_id: UUID | None) -> bool:
    if response_id is None:
        return True
    response = await session.get(CandidateResponse, response_id)
    return (
        response is not None
        and response.ended_at is not None
        and response.completion_reason
        in {
            "COMPLETE",
            "INTERRUPTED",
            "SUPERSEDED",
            "TIMEOUT",
            "SPONTANEOUS",
        }
    )


async def _evaluator_policy_is_exact(session: AsyncSession, policy_id: UUID) -> bool:
    policy = await session.get(AIPolicyVersion, policy_id)
    return bool(
        policy is not None
        and policy.policy_key == ASSESSMENT_EVALUATOR_POLICY_KEY
        and policy.version == ASSESSMENT_EVALUATOR_POLICY_VERSION
    )


async def _existing_unit_result(
    session: AsyncSession, unit: AssessmentUnit
) -> UnitEvaluationResult | None:
    candidates = list(
        await session.scalars(
            select(Assessment)
            .join(AIPolicyVersion, AIPolicyVersion.id == Assessment.ai_policy_version_id)
            .where(
                Assessment.interview_session_id == unit.interview_session_id,
                AIPolicyVersion.policy_key == ASSESSMENT_EVALUATOR_POLICY_KEY,
                AIPolicyVersion.version == ASSESSMENT_EVALUATOR_POLICY_VERSION,
            )
        )
    )
    expected_sources = {fact.event_id for fact in unit.sources}
    matching: list[Assessment] = []
    for assessment in candidates:
        source_ids = set(
            await session.scalars(
                select(AssessmentSource.interview_event_id).where(
                    AssessmentSource.assessment_id == assessment.id
                )
            )
        )
        if source_ids == expected_sources:
            matching.append(assessment)
    if not matching:
        return None
    assessment_ids = tuple(assessment.id for assessment in matching)
    evidence_ids = tuple(
        await session.scalars(
            select(Evidence.id).where(Evidence.originating_assessment_id.in_(assessment_ids))
        )
    )
    breakpoint_ids = (
        tuple(
            await session.scalars(
                select(BreakpointEvidence.breakpoint_id).where(
                    BreakpointEvidence.evidence_id.in_(evidence_ids)
                )
            )
        )
        if evidence_ids
        else ()
    )
    return UnitEvaluationResult(
        unit_key=unit.unit_key,
        unit_kind=unit.kind.value,
        status="SKIPPED",
        assessment_ids=assessment_ids,
        evidence_ids=evidence_ids,
        breakpoint_ids=tuple(dict.fromkeys(breakpoint_ids)),
        error_category="ALREADY_EVALUATED",
    )
