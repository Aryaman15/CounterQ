from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import (
    POST_INTERVIEW_ASSESSMENT_PURPOSE,
    AIGateway,
    AIGatewayError,
    AIGatewayResult,
)
from app.ai_gateway.models import AIInvocation, AIPolicyVersion
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
from app.evidence.independence import IndependenceAttributionService
from app.evidence.models import (
    Assessment,
    AssessmentSource,
    AssessmentUnitEvaluation,
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
from app.evidence.units import (
    AssessmentInputBuilder,
    AssessmentSourceFact,
    AssessmentUnit,
    is_successful_recovery_unit,
)
from app.evidence.validation import EvidenceValidationService
from app.interviews.models import CandidateResponse, InterviewerPrompt, InterviewSession
from app.observation.models import InterviewEvent

UnitStatus = Literal["COMPLETED", "SKIPPED", "FAILED"]
CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE = "candidate_response_assessment"


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
            units = await AssessmentInputBuilder(read_session).build_completed_session(
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
            gateway_result: AIGatewayResult[AssessmentAnalysisResult] | None = None
            for attempt in (1, 2):
                try:
                    gateway_result = await self._gateway.reason_structured(
                        interview_session_id=interview_session_id,
                        capability="STANDARD_REASONING",
                        purpose=POST_INTERVIEW_ASSESSMENT_PURPOSE,
                        policy=assessment_evaluator_policy_descriptor(),
                        instructions=ASSESSMENT_EVALUATOR_INSTRUCTIONS,
                        input_content=unit.serialize(),
                        output_model=AssessmentAnalysisResult,
                        correlation_id=f"{unit.unit_key}:attempt:{attempt}",
                        metadata={
                            "assessment_unit_key": unit.unit_key,
                            "assessment_unit_kind": unit.kind.value,
                            "attempt": attempt,
                        },
                    )
                except (AIGatewayError, ReasoningProviderError) as exc:
                    error_category = getattr(exc, "category", "AI_GATEWAY_ERROR")
                    if (
                        attempt == 1
                        and isinstance(exc, AIGatewayError)
                        and error_category == "STRUCTURED_OUTPUT_INVALID"
                    ):
                        continue
                    results.append(
                        UnitEvaluationResult(
                            unit_key=unit.unit_key,
                            unit_kind=unit.kind.value,
                            status="FAILED",
                            error_category=error_category,
                        )
                    )
                    break
                else:
                    break
            if gateway_result is None:
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

    async def evaluate_active_checkpoint(
        self, interview_session_id: UUID
    ) -> SessionEvaluationResult:
        """Evaluate the newest stable Coach unit once, without schema retry.

        This uses the frozen Assessment/Evidence pipeline and the live portion of
        the reasoning budget. Partial responses and RUNNING executions never
        become units in ``build_active_checkpoint``.
        """

        async with self._sessionmaker() as read_session:
            units = await AssessmentInputBuilder(read_session).build_active_checkpoint(
                interview_session_id
            )
            selected = units[-1:] if units else []
            existing_results = {
                unit.unit_key: result
                for unit in selected
                if (result := await _existing_unit_result(read_session, unit)) is not None
            }
        if not selected:
            return SessionEvaluationResult(interview_session_id, ())
        unit = selected[0]
        existing = existing_results.get(unit.unit_key)
        if existing is not None:
            return SessionEvaluationResult(interview_session_id, (existing,))
        if unit.independence_level is None:
            return SessionEvaluationResult(
                interview_session_id,
                (
                    UnitEvaluationResult(
                        unit_key=unit.unit_key,
                        unit_kind=unit.kind.value,
                        status="SKIPPED",
                        error_category="INDEPENDENCE_UNRESOLVED",
                    ),
                ),
            )
        try:
            gateway_result = await self._gateway.reason_structured(
                interview_session_id=interview_session_id,
                capability="STANDARD_REASONING",
                purpose=CANDIDATE_RESPONSE_ASSESSMENT_PURPOSE,
                policy=assessment_evaluator_policy_descriptor(),
                instructions=ASSESSMENT_EVALUATOR_INSTRUCTIONS,
                input_content=unit.serialize(),
                output_model=AssessmentAnalysisResult,
                correlation_id=f"{unit.unit_key}:active-checkpoint",
                metadata={
                    "assessment_unit_key": unit.unit_key,
                    "assessment_unit_kind": unit.kind.value,
                    "active_checkpoint": True,
                },
            )
        except (AIGatewayError, ReasoningProviderError) as exc:
            return SessionEvaluationResult(
                interview_session_id,
                (
                    UnitEvaluationResult(
                        unit_key=unit.unit_key,
                        unit_kind=unit.kind.value,
                        status="FAILED",
                        error_category=getattr(exc, "category", "AI_GATEWAY_ERROR"),
                    ),
                ),
            )
        try:
            result = await self._persist_result(
                original_unit=unit,
                analysis=gateway_result.parsed,
                invocation_id=gateway_result.invocation_id,
                evaluator_policy_version_id=gateway_result.policy_version_id,
            )
        except Exception as exc:
            result = UnitEvaluationResult(
                unit_key=unit.unit_key,
                unit_kind=unit.kind.value,
                status="FAILED",
                error_category=type(exc).__name__,
            )
        return SessionEvaluationResult(interview_session_id, (result,))

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
                fresh_units = await AssessmentInputBuilder(session).build_for_revalidation(
                    original_unit.interview_session_id
                )
                fresh_unit = next(
                    (item for item in fresh_units if item.unit_key == original_unit.unit_key), None
                )
                if fresh_unit is None or fresh_unit.serialize() != original_unit.serialize():
                    raise ValueError("AssessmentUnit changed before deterministic admission")
                if fresh_unit.independence_level is None:
                    raise ValueError("AssessmentUnit independence became unresolved")

                existing_completion = await session.scalar(
                    select(AssessmentUnitEvaluation).where(
                        AssessmentUnitEvaluation.interview_session_id
                        == fresh_unit.interview_session_id,
                        AssessmentUnitEvaluation.unit_key == fresh_unit.unit_key,
                        AssessmentUnitEvaluation.evaluator_policy_version_id
                        == evaluator_policy_version_id,
                    )
                )
                if existing_completion is not None:
                    return await _evaluated_unit_result(
                        session,
                        fresh_unit,
                        evaluator_policy_version_id=evaluator_policy_version_id,
                    )
                if not await _evaluator_policy_is_exact(
                    session, evaluator_policy_version_id
                ):
                    raise ValueError("Assessment evaluator policy changed before admission")
                invocation = await session.scalar(
                    select(AIInvocation).where(
                        AIInvocation.id == invocation_id,
                        AIInvocation.interview_session_id == fresh_unit.interview_session_id,
                        AIInvocation.ai_policy_version_id == evaluator_policy_version_id,
                        AIInvocation.status == "SUCCEEDED",
                    )
                )
                if invocation is None:
                    raise ValueError("Successful Assessment AIInvocation is not admissible")

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
                session.add(
                    AssessmentUnitEvaluation(
                        interview_session_id=fresh_unit.interview_session_id,
                        unit_key=fresh_unit.unit_key,
                        unit_kind=fresh_unit.kind.value,
                        evaluator_policy_version_id=evaluator_policy_version_id,
                        successful_ai_invocation_id=invocation_id,
                        finding_count=len(analysis.findings),
                        completed_at=datetime.now(UTC),
                    )
                )
                await session.flush()
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
        independence_level = await _finding_independence_level(session, unit, finding)
        evidence_result = await validation.validate_into_evidence(
            ValidateEvidenceCommand(
                interview_session_id=unit.interview_session_id,
                assessment_id=assessment.id,
                polarity=finding.polarity,
                strength=finding.proposed_strength,
                confidence=Decimal(str(finding.confidence)),
                finding=finding.evidence_finding,
                independence_level=independence_level,
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
            independence_level=independence_level,
        )
        return assessment.id, evidence_result.evidence_id, links

    async def _orchestrate_breakpoint(
        self,
        *,
        session: AsyncSession,
        unit: AssessmentUnit,
        finding: AssessmentFinding,
        evidence_id: UUID,
        independence_level: str,
    ) -> tuple[UUID, ...]:
        if not finding.concept_keys or not finding.skill_dimension_keys:
            return ()
        concept_id = unit.concept_ids_by_key[finding.concept_keys[0]]
        skill_id = unit.skill_ids_by_key[finding.skill_dimension_keys[0]]
        interview = await session.get(InterviewSession, unit.interview_session_id)
        assert interview is not None
        service = BreakpointService(session)
        if finding.breakpoint_effect == "WEAKNESS":
            if is_successful_recovery_unit(unit):
                return ()
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
        if independence_level == "DIRECTLY_TAUGHT":
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


async def _finding_independence_level(
    session: AsyncSession,
    unit: AssessmentUnit,
    finding: AssessmentFinding,
) -> str:
    """Apply assistance contamination only when its persisted target matches."""

    base = cast(str, unit.independence_level)
    if unit.candidate_response_id is None or base not in {
        "AFTER_LIGHT_GUIDANCE",
        "AFTER_STRONG_HINT",
        "DIRECTLY_TAUGHT",
    }:
        if unit.candidate_response_id is not None:
            return base
        events = list(
            await session.scalars(
                select(InterviewEvent).where(
                    InterviewEvent.id.in_(fact.event_id for fact in unit.sources)
                )
            )
        )
        attribution = await IndependenceAttributionService(session).for_event_window(
            events,
            assistance_target_concept_ids={
                unit.concept_ids_by_key[key] for key in finding.concept_keys
            },
            assistance_target_skill_ids={
                unit.skill_ids_by_key[key] for key in finding.skill_dimension_keys
            },
        )
        if attribution.level is None:
            raise ValueError("Finding independence became unresolved after target matching")
        return attribution.level
    response = await session.get(CandidateResponse, unit.candidate_response_id)
    if response is None or response.interviewer_prompt_id is None:
        return base
    prompt = await session.get(InterviewerPrompt, response.interviewer_prompt_id)
    if prompt is None or prompt.assistance_type is None:
        return base
    concept_match = prompt.target_concept_id is None or prompt.target_concept_id in {
        unit.concept_ids_by_key[key] for key in finding.concept_keys
    }
    skill_match = prompt.target_skill_dimension_id is None or prompt.target_skill_dimension_id in {
        unit.skill_ids_by_key[key] for key in finding.skill_dimension_keys
    }
    return base if concept_match and skill_match else "INDEPENDENT"


async def _existing_unit_result(
    session: AsyncSession, unit: AssessmentUnit
) -> UnitEvaluationResult | None:
    evaluator_policy_version_id = await session.scalar(
        select(AIPolicyVersion.id).where(
            AIPolicyVersion.policy_key == ASSESSMENT_EVALUATOR_POLICY_KEY,
            AIPolicyVersion.version == ASSESSMENT_EVALUATOR_POLICY_VERSION,
        )
    )
    if evaluator_policy_version_id is None:
        return None
    completion = await session.scalar(
        select(AssessmentUnitEvaluation.id).where(
            AssessmentUnitEvaluation.interview_session_id == unit.interview_session_id,
            AssessmentUnitEvaluation.unit_key == unit.unit_key,
            AssessmentUnitEvaluation.evaluator_policy_version_id
            == evaluator_policy_version_id,
        )
    )
    result = await _evaluated_unit_result(
        session,
        unit,
        evaluator_policy_version_id=evaluator_policy_version_id,
    )
    return result if completion is not None or result.assessment_ids else None


async def _evaluated_unit_result(
    session: AsyncSession,
    unit: AssessmentUnit,
    *,
    evaluator_policy_version_id: UUID,
) -> UnitEvaluationResult:
    candidates = list(
        await session.scalars(
            select(Assessment).where(
                Assessment.interview_session_id == unit.interview_session_id,
                Assessment.ai_policy_version_id == evaluator_policy_version_id,
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
