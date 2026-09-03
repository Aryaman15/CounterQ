"""Deterministic, read-only Session Report source snapshot builder."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence.models import (
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)
from app.examiner.models import CandidateClaim
from app.execution.models import ExecutionRun, TestResult
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
)
from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent, TranscriptSegment
from app.problems.models import Concept, ProblemVersion
from app.reports.schema import (
    SESSION_REPORT_INPUT_CONTRACT_VERSION,
    CandidateClaimSource,
    CanonicalTarget,
    DeliveredAssistanceSource,
    DeliveredPromptSource,
    ExecutionSource,
    ObservedSourceReference,
    ReportBreakpointSource,
    ReportEvidenceSource,
    ReportSessionFacts,
    SessionReportSourceBundle,
)
from app.reports.schema import (
    CandidateResponseSource as ReportCandidateResponseSource,
)


class SessionReportSourceUnavailable(ValueError):
    pass


class SessionReportSourceBuilder:
    """Build one stable source boundary from canonical durable records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(self, interview_session_id: UUID) -> SessionReportSourceBundle:
        interview = await self._session.get(InterviewSession, interview_session_id)
        if interview is None:
            raise SessionReportSourceUnavailable("Interview session was not found")
        if interview.status != "COMPLETED" or interview.completed_at is None:
            raise SessionReportSourceUnavailable("Session Report requires a completed interview")
        configuration = await self._session.get(
            InterviewConfiguration, interview.interview_configuration_id
        )
        problem = await self._session.get(ProblemVersion, interview.problem_version_id)
        if configuration is None or problem is None:
            raise SessionReportSourceUnavailable("Completed interview source facts are incomplete")

        evidence = await self._evidence(interview.id)
        breakpoints = await self._breakpoints(interview.id, evidence)
        delivered_prompts, assistance = await self._deliveries(interview.id)
        return SessionReportSourceBundle(
            input_contract_version=SESSION_REPORT_INPUT_CONTRACT_VERSION,
            session=ReportSessionFacts(
                interview_session_id=interview.id,
                mode=configuration.mode,
                level=configuration.level,
                language=configuration.language,
                problem_version_id=problem.id,
                problem_title=problem.title,
                started_at=interview.started_at.isoformat(),
                completed_at=interview.completed_at.isoformat(),
                duration_seconds=max(
                    0, int((interview.completed_at - interview.started_at).total_seconds())
                ),
                source_watermark=interview.last_server_sequence,
            ),
            evidence=evidence,
            breakpoints=breakpoints,
            delivered_prompts=delivered_prompts,
            delivered_assistance=assistance,
            candidate_claims=await self._claims(interview.id),
            candidate_responses=await self._responses(interview.id),
            executions=await self._executions(interview.id),
        )

    async def _evidence(self, session_id: UUID) -> list[ReportEvidenceSource]:
        rows = list(
            await self._session.scalars(
                select(Evidence)
                .where(
                    Evidence.interview_session_id == session_id,
                    Evidence.validation_status == "VALID",
                    Evidence.invalidated_at.is_(None),
                )
                .order_by(Evidence.created_at, Evidence.id)
            )
        )
        result: list[ReportEvidenceSource] = []
        for evidence in rows:
            source_links = list(
                await self._session.scalars(
                    select(EvidenceSource)
                    .where(EvidenceSource.evidence_id == evidence.id)
                    .order_by(EvidenceSource.source_role, EvidenceSource.interview_event_id)
                )
            )
            source_rows: list[ObservedSourceReference] = []
            excluded_starter_baseline = False
            for link in source_links:
                event = await self._session.get(InterviewEvent, link.interview_event_id)
                if event is None or event.interview_session_id != session_id:
                    continue
                if (
                    link.source_role == "PRIMARY"
                    and event.event_type == "CODE_SNAPSHOT_CREATED"
                    and event.payload.get("trigger") == "INITIAL_EDITOR_STATE"
                ):
                    excluded_starter_baseline = True
                    break
                source_rows.append(await self._event_reference(event))
            if excluded_starter_baseline or not source_rows:
                continue
            concept_targets = [
                CanonicalTarget(
                    id=concept.id,
                    canonical_key=concept.canonical_key,
                    display_name=concept.display_name,
                )
                for concept in list(
                    await self._session.scalars(
                        select(Concept)
                        .join(EvidenceConcept, EvidenceConcept.concept_id == Concept.id)
                        .where(EvidenceConcept.evidence_id == evidence.id)
                        .order_by(EvidenceConcept.is_primary.desc(), Concept.canonical_key)
                    )
                )
            ]
            skill_targets = [
                CanonicalTarget(
                    id=skill.id,
                    canonical_key=skill.canonical_key,
                    display_name=skill.display_name,
                )
                for skill in list(
                    await self._session.scalars(
                        select(SkillDimension)
                        .join(
                            EvidenceSkill,
                            EvidenceSkill.skill_dimension_id == SkillDimension.id,
                        )
                        .where(EvidenceSkill.evidence_id == evidence.id)
                        .order_by(EvidenceSkill.is_primary.desc(), SkillDimension.canonical_key)
                    )
                )
            ]
            if not concept_targets and not skill_targets:
                continue
            result.append(
                ReportEvidenceSource(
                    id=evidence.id,
                    finding=evidence.finding,
                    polarity=evidence.polarity,
                    strength=evidence.strength,
                    independence_level=evidence.independence_level,
                    concept_targets=concept_targets,
                    skill_targets=skill_targets,
                    sources=source_rows,
                )
            )
        return result

    async def _event_reference(self, event: InterviewEvent) -> ObservedSourceReference:
        excerpt: str | None = None
        source_kind = "EVENT"
        segment = await self._session.scalar(
            select(TranscriptSegment).where(TranscriptSegment.interview_event_id == event.id)
        )
        if segment is not None:
            source_kind = "CANDIDATE_TRANSCRIPT" if segment.speaker == "CANDIDATE" else "PROMPT"
            excerpt = _bounded_excerpt(segment.text)
        elif event.code_snapshot_id is not None or event.event_type == "CODE_SNAPSHOT_CREATED":
            snapshot_id = event.code_snapshot_id
            if snapshot_id is None:
                snapshot_id = await self._session.scalar(
                    select(CodeSnapshot.id).where(CodeSnapshot.created_from_event_id == event.id)
                )
            snapshot = await self._session.get(CodeSnapshot, snapshot_id) if snapshot_id else None
            if snapshot is not None:
                source_kind = "CODE_SNAPSHOT"
                excerpt = f"{snapshot.language} code snapshot version {snapshot.version_number}"
        elif event.event_type in {"RUN_CLICKED", "COMPILE_COMPLETED", "TEST_COMPLETED"}:
            source_kind = "EXECUTION"
            status = event.payload.get("status")
            excerpt = f"{event.event_type.replace('_', ' ').title()}"
            if isinstance(status, str):
                excerpt += f": {status.replace('_', ' ').lower()}"
        return ObservedSourceReference(
            event_id=event.id,
            server_sequence=event.server_sequence,
            event_type=event.event_type,
            source_kind=source_kind,
            candidate_safe_excerpt=excerpt,
        )

    async def _breakpoints(
        self,
        session_id: UUID,
        evidence: list[ReportEvidenceSource],
    ) -> list[ReportBreakpointSource]:
        active_ids = {item.id for item in evidence}
        if not active_ids:
            return []
        rows = list(
            await self._session.scalars(
                select(Breakpoint)
                .join(BreakpointEvidence, BreakpointEvidence.breakpoint_id == Breakpoint.id)
                .join(Evidence, Evidence.id == BreakpointEvidence.evidence_id)
                .where(
                    Evidence.interview_session_id == session_id,
                    Evidence.id.in_(active_ids),
                )
                .distinct()
                .order_by(Breakpoint.first_detected_at, Breakpoint.id)
            )
        )
        result: list[ReportBreakpointSource] = []
        for breakpoint in rows:
            concept = await self._session.get(Concept, breakpoint.concept_id)
            skill = await self._session.get(SkillDimension, breakpoint.skill_dimension_id)
            if concept is None or skill is None:
                continue
            support = list(
                await self._session.scalars(
                    select(BreakpointEvidence.evidence_id).where(
                        BreakpointEvidence.breakpoint_id == breakpoint.id,
                        BreakpointEvidence.evidence_id.in_(active_ids),
                    )
                )
            )
            if not support:
                continue
            result.append(
                ReportBreakpointSource(
                    id=breakpoint.id,
                    status=breakpoint.status,
                    severity=breakpoint.severity,
                    summary=breakpoint.summary,
                    concept_target=CanonicalTarget(
                        id=concept.id,
                        canonical_key=concept.canonical_key,
                        display_name=concept.display_name,
                    ),
                    skill_target=CanonicalTarget(
                        id=skill.id,
                        canonical_key=skill.canonical_key,
                        display_name=skill.display_name,
                    ),
                    supporting_evidence_ids=sorted(support, key=str),
                )
            )
        return result

    async def _deliveries(
        self, session_id: UUID
    ) -> tuple[list[DeliveredPromptSource], list[DeliveredAssistanceSource]]:
        rows = list(
            (
                await self._session.execute(
                    select(
                        InterviewerPromptDelivery,
                        InterviewerPrompt,
                        TranscriptSegment,
                        InterviewEvent,
                    )
                    .join(
                        InterviewerPrompt,
                        InterviewerPrompt.id == InterviewerPromptDelivery.interviewer_prompt_id,
                    )
                    .join(
                        TranscriptSegment,
                        TranscriptSegment.id
                        == InterviewerPromptDelivery.actual_transcript_segment_id,
                    )
                    .join(InterviewEvent, InterviewEvent.id == TranscriptSegment.interview_event_id)
                    .where(
                        InterviewerPromptDelivery.interview_session_id == session_id,
                        InterviewerPrompt.interview_session_id == session_id,
                        InterviewEvent.interview_session_id == session_id,
                        InterviewerPromptDelivery.delivery_state.in_(
                            ("DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED")
                        ),
                        TranscriptSegment.text != "",
                    )
                    .order_by(InterviewEvent.server_sequence, InterviewerPromptDelivery.id)
                )
            ).all()
        )
        prompts: list[DeliveredPromptSource] = []
        assistance: list[DeliveredAssistanceSource] = []
        for delivery, prompt, segment, event in rows:
            prompts.append(
                DeliveredPromptSource(
                    prompt_id=prompt.id,
                    delivery_id=delivery.id,
                    kind=prompt.kind,
                    probe_strategy=prompt.probe_strategy,
                    actual_text=segment.text,
                    delivery_state=delivery.delivery_state,
                    delivered_server_sequence=event.server_sequence,
                )
            )
            if prompt.assistance_type is not None and prompt.hint_level is not None:
                assistance.append(
                    DeliveredAssistanceSource(
                        prompt_id=prompt.id,
                        delivery_id=delivery.id,
                        assistance_type=prompt.assistance_type,
                        hint_level=prompt.hint_level,
                        actual_text=segment.text,
                        delivery_state=delivery.delivery_state,
                        delivered_server_sequence=event.server_sequence,
                        target_concept_id=prompt.target_concept_id,
                        target_skill_dimension_id=prompt.target_skill_dimension_id,
                    )
                )
        return prompts, assistance

    async def _claims(self, session_id: UUID) -> list[CandidateClaimSource]:
        rows = list(
            await self._session.scalars(
                select(CandidateClaim)
                .where(
                    CandidateClaim.interview_session_id == session_id,
                    CandidateClaim.status == "ACCEPTED_AS_INTERPRETATION",
                )
                .order_by(CandidateClaim.created_at, CandidateClaim.id)
            )
        )
        result: list[CandidateClaimSource] = []
        for claim in rows:
            source_event = await self._claim_source_event(claim)
            if source_event is None or source_event.interview_session_id != session_id:
                continue
            result.append(
                CandidateClaimSource(
                    id=claim.id,
                    claim_type=claim.claim_type,
                    normalized_claim=claim.normalized_claim,
                    source_event_id=source_event.id,
                    source_server_sequence=source_event.server_sequence,
                )
            )
        return result

    async def _claim_source_event(self, claim: CandidateClaim) -> InterviewEvent | None:
        if claim.source_event_id is not None:
            return await self._session.get(InterviewEvent, claim.source_event_id)
        if claim.source_transcript_segment_id is not None:
            segment = await self._session.get(TranscriptSegment, claim.source_transcript_segment_id)
            return (
                await self._session.get(InterviewEvent, segment.interview_event_id)
                if segment is not None
                else None
            )
        if claim.source_code_snapshot_id is not None:
            snapshot = await self._session.get(CodeSnapshot, claim.source_code_snapshot_id)
            return (
                await self._session.get(InterviewEvent, snapshot.created_from_event_id)
                if snapshot is not None
                else None
            )
        if claim.source_code_diff_id is not None:
            diff = await self._session.get(CodeDiff, claim.source_code_diff_id)
            return (
                await self._session.get(InterviewEvent, diff.created_from_event_id)
                if diff is not None
                else None
            )
        return None

    async def _responses(self, session_id: UUID) -> list[ReportCandidateResponseSource]:
        rows = list(
            await self._session.scalars(
                select(CandidateResponse)
                .where(CandidateResponse.interview_session_id == session_id)
                .order_by(CandidateResponse.started_at, CandidateResponse.id)
            )
        )
        result: list[ReportCandidateResponseSource] = []
        for response in rows:
            source_ids = list(
                await self._session.scalars(
                    select(CandidateResponseSource.interview_event_id)
                    .join(
                        InterviewEvent,
                        InterviewEvent.id == CandidateResponseSource.interview_event_id,
                    )
                    .where(CandidateResponseSource.candidate_response_id == response.id)
                    .where(InterviewEvent.interview_session_id == session_id)
                    .order_by(CandidateResponseSource.sequence)
                )
            )
            result.append(
                ReportCandidateResponseSource(
                    id=response.id,
                    prompt_id=response.interviewer_prompt_id,
                    summary=response.summary,
                    source_event_ids=source_ids,
                )
            )
        return result

    async def _executions(self, session_id: UUID) -> list[ExecutionSource]:
        runs = list(
            await self._session.scalars(
                select(ExecutionRun)
                .where(ExecutionRun.interview_session_id == session_id)
                .order_by(ExecutionRun.started_at, ExecutionRun.id)
            )
        )
        result: list[ExecutionSource] = []
        for run in runs:
            counts: dict[str, int] = defaultdict(int)
            statuses = list(
                await self._session.scalars(
                    select(TestResult.status).where(
                        TestResult.execution_run_id == run.id,
                        TestResult.is_visible.is_(True),
                    )
                )
            )
            for status in statuses:
                counts[status] += 1
            result.append(
                ExecutionSource(
                    id=run.id,
                    code_snapshot_id=run.code_snapshot_id,
                    language=run.language,
                    status=run.status,
                    visible_passed=counts["PASSED"],
                    visible_failed=counts["FAILED"],
                    completed_at=run.completed_at.isoformat() if run.completed_at else None,
                )
            )
        return result


def _bounded_excerpt(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized[:320]
