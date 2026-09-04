"""CounterMap-specific canonical source loading; never reads Session Report prose."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence.models import (
    Assessment,
    Breakpoint,
    BreakpointEvidence,
    Evidence,
    EvidenceConcept,
    EvidenceSkill,
    EvidenceSource,
    SkillDimension,
)
from app.examiner.models import CandidateClaim, ExaminerDecision
from app.execution.models import ExecutionRun, TestResult
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewConfiguration,
    InterviewerPrompt,
    InterviewerPromptDelivery,
    InterviewSession,
    InterviewStageTransition,
)
from app.observation.models import CodeDiff, CodeSnapshot, InterviewEvent, TranscriptSegment
from app.problems.models import Concept


class CounterMapSourceUnavailable(ValueError):
    pass


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventSource(SourceModel):
    id: UUID
    server_sequence: int
    event_type: str
    source: str
    stage: str | None
    causation_id: UUID | None
    correlation_id: UUID | None
    code_snapshot_id: UUID | None
    payload: dict[str, object]


class TranscriptSource(SourceModel):
    id: UUID
    event_id: UUID
    server_sequence: int
    speaker: Literal["CANDIDATE", "COUNTERQ"]
    text: str
    stage: str
    delivery_state: str | None


class ClaimSource(SourceModel):
    id: UUID
    claim_type: str
    normalized_claim: str
    verbatim_excerpt: str | None
    source_event_id: UUID
    source_server_sequence: int
    source_code_snapshot_id: UUID | None


class ResponseSource(SourceModel):
    id: UUID
    prompt_id: UUID | None
    summary: str | None
    source_event_ids: list[UUID]
    start_sequence: int
    end_sequence: int


class CodeSnapshotSource(SourceModel):
    id: UUID
    version: int
    parent_snapshot_id: UUID | None
    language: str
    content_hash: str
    created_from_event_id: UUID
    server_sequence: int
    stage: str | None


class CodeDiffSource(SourceModel):
    id: UUID
    from_snapshot_id: UUID
    to_snapshot_id: UUID
    created_from_event_id: UUID
    server_sequence: int
    change_summary: str | None
    significance: str | None


class ExecutionSource(SourceModel):
    id: UUID
    run_event_id: UUID
    code_snapshot_id: UUID
    server_sequence: int
    status: str
    language: str
    visible_passed: int
    visible_failed: int


class DecisionSource(SourceModel):
    id: UUID
    status: str
    action: str
    target_claim_id: UUID | None
    target_event_id: UUID | None
    target_code_snapshot_id: UUID | None
    source_event_watermark: int


class DeliverySource(SourceModel):
    id: UUID
    prompt_id: UUID
    prompt_status: str
    prompt_kind: str
    prompt_origin: str
    probe_strategy: str | None
    examiner_decision_id: UUID | None
    target_claim_id: UUID | None
    target_event_id: UUID | None
    source_code_snapshot_id: UUID | None
    target_concept_id: UUID | None
    target_skill_dimension_id: UUID | None
    assistance_type: str | None
    hint_level: str | None
    actual_transcript_segment_id: UUID
    actual_event_id: UUID
    actual_text: str
    intended_text: str
    delivery_state: Literal["DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED"]
    server_sequence: int
    stage: str


class EvidenceTarget(SourceModel):
    id: UUID
    canonical_key: str
    display_name: str


class EvidenceSourceLink(SourceModel):
    event_id: UUID
    server_sequence: int
    source_role: str


class CanonicalEvidenceSource(SourceModel):
    id: UUID
    evidence_type: str
    polarity: Literal["POSITIVE", "NEGATIVE", "MIXED"]
    strength: Literal["WEAK", "MODERATE", "STRONG"]
    independence_level: Literal[
        "INDEPENDENT",
        "AFTER_PROBE",
        "AFTER_LIGHT_GUIDANCE",
        "AFTER_STRONG_HINT",
        "DIRECTLY_TAUGHT",
    ]
    finding: str
    source_links: list[EvidenceSourceLink] = Field(min_length=1)
    concept_targets: list[EvidenceTarget]
    skill_targets: list[EvidenceTarget]
    originating_assessment_id: UUID
    candidate_response_id: UUID | None
    source_code_snapshot_id: UUID | None


class BreakpointEvidenceLink(SourceModel):
    evidence_id: UUID
    relationship: Literal["CREATED", "REINFORCED", "CONTRADICTED", "RESOLUTION_SUPPORT"]


class CanonicalBreakpointSource(SourceModel):
    id: UUID
    status: str
    severity: str
    summary: str
    concept_target: EvidenceTarget
    skill_target: EvidenceTarget
    evidence_links: list[BreakpointEvidenceLink] = Field(min_length=1)


class CounterMapSourceBundle(SourceModel):
    interview_session_id: UUID
    mode: Literal["COACH", "SIMULATION"]
    source_watermark: int
    events: list[EventSource]
    transcripts: list[TranscriptSource]
    claims: list[ClaimSource]
    responses: list[ResponseSource]
    code_snapshots: list[CodeSnapshotSource]
    code_diffs: list[CodeDiffSource]
    executions: list[ExecutionSource]
    decisions: list[DecisionSource]
    deliveries: list[DeliverySource]
    evidence: list[CanonicalEvidenceSource]
    breakpoints: list[CanonicalBreakpointSource]

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @property
    def source_identity(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class CounterMapSourceBuilder:
    """Load stable canonical facts through the completed session watermark."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(self, interview_session_id: UUID) -> CounterMapSourceBundle:
        interview = await self._session.get(InterviewSession, interview_session_id)
        if interview is None:
            raise CounterMapSourceUnavailable("Interview session was not found")
        if interview.status != "COMPLETED" or interview.completed_at is None:
            raise CounterMapSourceUnavailable("CounterMap requires a completed interview")
        configuration = await self._session.get(
            InterviewConfiguration,
            interview.interview_configuration_id,
        )
        if configuration is None:
            raise CounterMapSourceUnavailable("Completed interview configuration is unavailable")

        event_rows = list(
            await self._session.scalars(
                select(InterviewEvent)
                .where(
                    InterviewEvent.interview_session_id == interview.id,
                    InterviewEvent.server_sequence <= interview.last_server_sequence,
                )
                .order_by(InterviewEvent.server_sequence, InterviewEvent.id)
            )
        )
        transitions = list(
            await self._session.scalars(
                select(InterviewStageTransition)
                .where(InterviewStageTransition.interview_session_id == interview.id)
                .order_by(InterviewStageTransition.state_version)
            )
        )
        stage_by_version = _stage_by_version(transitions)
        events = [
            EventSource(
                id=row.id,
                server_sequence=row.server_sequence,
                event_type=row.event_type,
                source=row.source,
                stage=_stage_for_version(stage_by_version, row.interview_state_version),
                causation_id=row.causation_id,
                correlation_id=row.correlation_id,
                code_snapshot_id=row.code_snapshot_id,
                payload=row.payload,
            )
            for row in event_rows
        ]
        events_by_id = {row.id: row for row in events}

        transcripts = await self._transcripts(interview.id, events_by_id)
        transcript_by_id = {row.id: row for row in transcripts}
        code_snapshots = await self._snapshots(interview.id, events_by_id)
        snapshots_by_id = {row.id: row for row in code_snapshots}

        return CounterMapSourceBundle(
            interview_session_id=interview.id,
            mode=cast(Literal["COACH", "SIMULATION"], configuration.mode),
            source_watermark=interview.last_server_sequence,
            events=events,
            transcripts=transcripts,
            claims=await self._claims(
                interview.id,
                events_by_id,
                transcript_by_id,
                snapshots_by_id,
            ),
            responses=await self._responses(interview.id, events_by_id),
            code_snapshots=code_snapshots,
            code_diffs=await self._diffs(interview.id, events_by_id),
            executions=await self._executions(interview.id, events_by_id),
            decisions=await self._decisions(interview.id),
            deliveries=await self._deliveries(interview.id, events_by_id),
            evidence=await self._evidence(interview.id, events_by_id),
            breakpoints=await self._breakpoints(interview.id),
        )

    async def _transcripts(
        self,
        session_id: UUID,
        events_by_id: dict[UUID, EventSource],
    ) -> list[TranscriptSource]:
        rows = list(
            await self._session.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.interview_session_id == session_id)
                .order_by(TranscriptSegment.sequence, TranscriptSegment.id)
            )
        )
        return [
            TranscriptSource(
                id=row.id,
                event_id=row.interview_event_id,
                server_sequence=events_by_id[row.interview_event_id].server_sequence,
                speaker=cast(Literal["CANDIDATE", "COUNTERQ"], row.speaker),
                text=row.text,
                stage=row.interview_stage,
                delivery_state=row.delivery_state,
            )
            for row in rows
            if row.interview_event_id in events_by_id and row.text.strip()
        ]

    async def _snapshots(
        self,
        session_id: UUID,
        events_by_id: dict[UUID, EventSource],
    ) -> list[CodeSnapshotSource]:
        rows = list(
            await self._session.scalars(
                select(CodeSnapshot)
                .where(CodeSnapshot.interview_session_id == session_id)
                .order_by(CodeSnapshot.version_number, CodeSnapshot.id)
            )
        )
        return [
            CodeSnapshotSource(
                id=row.id,
                version=row.version_number,
                parent_snapshot_id=row.parent_snapshot_id,
                language=row.language,
                content_hash=row.content_hash,
                created_from_event_id=row.created_from_event_id,
                server_sequence=events_by_id[row.created_from_event_id].server_sequence,
                stage=events_by_id[row.created_from_event_id].stage,
            )
            for row in rows
            if row.created_from_event_id in events_by_id
        ]

    async def _claims(
        self,
        session_id: UUID,
        events_by_id: dict[UUID, EventSource],
        transcripts_by_id: dict[UUID, TranscriptSource],
        snapshots_by_id: dict[UUID, CodeSnapshotSource],
    ) -> list[ClaimSource]:
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
        result: list[ClaimSource] = []
        for row in rows:
            event_id = row.source_event_id
            if event_id is None and row.source_transcript_segment_id is not None:
                transcript = transcripts_by_id.get(row.source_transcript_segment_id)
                event_id = transcript.event_id if transcript else None
            if event_id is None and row.source_code_snapshot_id is not None:
                snapshot = snapshots_by_id.get(row.source_code_snapshot_id)
                event_id = snapshot.created_from_event_id if snapshot else None
            if event_id is None and row.source_code_diff_id is not None:
                diff = await self._session.get(CodeDiff, row.source_code_diff_id)
                event_id = diff.created_from_event_id if diff else None
            event = events_by_id.get(event_id) if event_id else None
            if event is None:
                continue
            result.append(
                ClaimSource(
                    id=row.id,
                    claim_type=row.claim_type,
                    normalized_claim=_bounded(row.normalized_claim, 700),
                    verbatim_excerpt=(
                        _bounded(row.verbatim_excerpt, 700) if row.verbatim_excerpt else None
                    ),
                    source_event_id=event.id,
                    source_server_sequence=event.server_sequence,
                    source_code_snapshot_id=row.source_code_snapshot_id,
                )
            )
        return result

    async def _responses(
        self,
        session_id: UUID,
        events_by_id: dict[UUID, EventSource],
    ) -> list[ResponseSource]:
        rows = list(
            await self._session.scalars(
                select(CandidateResponse)
                .where(CandidateResponse.interview_session_id == session_id)
                .order_by(CandidateResponse.started_at, CandidateResponse.id)
            )
        )
        result: list[ResponseSource] = []
        for row in rows:
            source_ids = list(
                await self._session.scalars(
                    select(CandidateResponseSource.interview_event_id)
                    .where(CandidateResponseSource.candidate_response_id == row.id)
                    .order_by(CandidateResponseSource.sequence)
                )
            )
            source_ids = [value for value in source_ids if value in events_by_id]
            if not source_ids:
                continue
            sequences = [events_by_id[value].server_sequence for value in source_ids]
            result.append(
                ResponseSource(
                    id=row.id,
                    prompt_id=row.interviewer_prompt_id,
                    summary=_bounded(row.summary, 700) if row.summary else None,
                    source_event_ids=source_ids,
                    start_sequence=min(sequences),
                    end_sequence=max(sequences),
                )
            )
        return result

    async def _diffs(
        self,
        session_id: UUID,
        events_by_id: dict[UUID, EventSource],
    ) -> list[CodeDiffSource]:
        rows = list(
            await self._session.scalars(
                select(CodeDiff)
                .where(CodeDiff.interview_session_id == session_id)
                .order_by(CodeDiff.created_at, CodeDiff.id)
            )
        )
        return [
            CodeDiffSource(
                id=row.id,
                from_snapshot_id=row.from_snapshot_id,
                to_snapshot_id=row.to_snapshot_id,
                created_from_event_id=row.created_from_event_id,
                server_sequence=events_by_id[row.created_from_event_id].server_sequence,
                change_summary=_bounded(row.change_summary, 360) if row.change_summary else None,
                significance=row.significance,
            )
            for row in rows
            if row.created_from_event_id in events_by_id
        ]

    async def _executions(
        self,
        session_id: UUID,
        events_by_id: dict[UUID, EventSource],
    ) -> list[ExecutionSource]:
        rows = list(
            await self._session.scalars(
                select(ExecutionRun)
                .where(ExecutionRun.interview_session_id == session_id)
                .order_by(ExecutionRun.started_at, ExecutionRun.id)
            )
        )
        result: list[ExecutionSource] = []
        for row in rows:
            event = events_by_id.get(row.run_event_id)
            if event is None:
                continue
            counts: dict[str, int] = defaultdict(int)
            for status in await self._session.scalars(
                select(TestResult.status).where(
                    TestResult.execution_run_id == row.id,
                    TestResult.is_visible.is_(True),
                )
            ):
                counts[status] += 1
            result.append(
                ExecutionSource(
                    id=row.id,
                    run_event_id=row.run_event_id,
                    code_snapshot_id=row.code_snapshot_id,
                    server_sequence=event.server_sequence,
                    status=row.status,
                    language=row.language,
                    visible_passed=counts["PASSED"],
                    visible_failed=counts["FAILED"],
                )
            )
        return result

    async def _decisions(self, session_id: UUID) -> list[DecisionSource]:
        rows = list(
            await self._session.scalars(
                select(ExaminerDecision)
                .where(ExaminerDecision.interview_session_id == session_id)
                .order_by(ExaminerDecision.created_at, ExaminerDecision.id)
            )
        )
        return [
            DecisionSource(
                id=row.id,
                status=row.status,
                action=row.action,
                target_claim_id=row.target_claim_id,
                target_event_id=row.target_event_id,
                target_code_snapshot_id=row.target_code_snapshot_id,
                source_event_watermark=row.source_event_watermark,
            )
            for row in rows
        ]

    async def _deliveries(
        self,
        session_id: UUID,
        events_by_id: dict[UUID, EventSource],
    ) -> list[DeliverySource]:
        rows = list(
            (
                await self._session.execute(
                    select(
                        InterviewerPromptDelivery,
                        InterviewerPrompt,
                        TranscriptSegment,
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
                    .where(
                        InterviewerPromptDelivery.interview_session_id == session_id,
                        InterviewerPrompt.interview_session_id == session_id,
                        InterviewerPromptDelivery.delivery_state.in_(
                            ("DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED")
                        ),
                    )
                    .order_by(TranscriptSegment.sequence, InterviewerPromptDelivery.id)
                )
            ).all()
        )
        result: list[DeliverySource] = []
        for delivery, prompt, segment in rows:
            event = events_by_id.get(segment.interview_event_id)
            actual = segment.text.strip()
            if event is None or not actual:
                continue
            result.append(
                DeliverySource(
                    id=delivery.id,
                    prompt_id=prompt.id,
                    prompt_status=prompt.status,
                    prompt_kind=prompt.kind,
                    prompt_origin=prompt.origin,
                    probe_strategy=prompt.probe_strategy,
                    examiner_decision_id=prompt.examiner_decision_id,
                    target_claim_id=prompt.target_claim_id,
                    target_event_id=prompt.target_event_id,
                    source_code_snapshot_id=prompt.source_code_snapshot_id,
                    target_concept_id=prompt.target_concept_id,
                    target_skill_dimension_id=prompt.target_skill_dimension_id,
                    assistance_type=prompt.assistance_type,
                    hint_level=prompt.hint_level,
                    actual_transcript_segment_id=segment.id,
                    actual_event_id=event.id,
                    actual_text=_bounded(actual, 700),
                    intended_text=delivery.intended_text,
                    delivery_state=cast(
                        Literal["DELIVERED", "PARTIALLY_DELIVERED", "INTERRUPTED"],
                        delivery.delivery_state,
                    ),
                    server_sequence=event.server_sequence,
                    stage=segment.interview_stage,
                )
            )
        return result

    async def _evidence(
        self,
        session_id: UUID,
        events_by_id: dict[UUID, EventSource],
    ) -> list[CanonicalEvidenceSource]:
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
        result: list[CanonicalEvidenceSource] = []
        for row in rows:
            links = list(
                await self._session.scalars(
                    select(EvidenceSource)
                    .where(EvidenceSource.evidence_id == row.id)
                    .order_by(EvidenceSource.source_role, EvidenceSource.interview_event_id)
                )
            )
            source_links = [
                EvidenceSourceLink(
                    event_id=link.interview_event_id,
                    server_sequence=events_by_id[link.interview_event_id].server_sequence,
                    source_role=link.source_role,
                )
                for link in links
                if link.interview_event_id in events_by_id
            ]
            if not source_links or any(
                link.source_role == "PRIMARY"
                and events_by_id[link.event_id].event_type == "CODE_SNAPSHOT_CREATED"
                and events_by_id[link.event_id].payload.get("trigger") == "INITIAL_EDITOR_STATE"
                for link in source_links
            ):
                continue
            concepts = list(
                await self._session.scalars(
                    select(Concept)
                    .join(EvidenceConcept, EvidenceConcept.concept_id == Concept.id)
                    .where(EvidenceConcept.evidence_id == row.id)
                    .order_by(EvidenceConcept.is_primary.desc(), Concept.canonical_key)
                )
            )
            skills = list(
                await self._session.scalars(
                    select(SkillDimension)
                    .join(
                        EvidenceSkill,
                        EvidenceSkill.skill_dimension_id == SkillDimension.id,
                    )
                    .where(EvidenceSkill.evidence_id == row.id)
                    .order_by(EvidenceSkill.is_primary.desc(), SkillDimension.canonical_key)
                )
            )
            assessment = await self._session.get(Assessment, row.originating_assessment_id)
            if assessment is None:
                continue
            result.append(
                CanonicalEvidenceSource(
                    id=row.id,
                    evidence_type=row.evidence_type,
                    polarity=cast(Literal["POSITIVE", "NEGATIVE", "MIXED"], row.polarity),
                    strength=cast(Literal["WEAK", "MODERATE", "STRONG"], row.strength),
                    independence_level=cast(
                        Literal[
                            "INDEPENDENT",
                            "AFTER_PROBE",
                            "AFTER_LIGHT_GUIDANCE",
                            "AFTER_STRONG_HINT",
                            "DIRECTLY_TAUGHT",
                        ],
                        row.independence_level,
                    ),
                    finding=_bounded(row.finding, 700),
                    source_links=source_links,
                    concept_targets=[
                        EvidenceTarget(
                            id=target.id,
                            canonical_key=target.canonical_key,
                            display_name=target.display_name,
                        )
                        for target in concepts
                    ],
                    skill_targets=[
                        EvidenceTarget(
                            id=target.id,
                            canonical_key=target.canonical_key,
                            display_name=target.display_name,
                        )
                        for target in skills
                    ],
                    originating_assessment_id=row.originating_assessment_id,
                    candidate_response_id=assessment.candidate_response_id,
                    source_code_snapshot_id=assessment.source_code_snapshot_id,
                )
            )
        return result

    async def _breakpoints(self, session_id: UUID) -> list[CanonicalBreakpointSource]:
        rows = list(
            (
                await self._session.execute(
                    select(Breakpoint, BreakpointEvidence, Evidence)
                    .join(
                        BreakpointEvidence,
                        BreakpointEvidence.breakpoint_id == Breakpoint.id,
                    )
                    .join(Evidence, Evidence.id == BreakpointEvidence.evidence_id)
                    .where(
                        Evidence.interview_session_id == session_id,
                        Evidence.validation_status == "VALID",
                        Evidence.invalidated_at.is_(None),
                    )
                    .order_by(Breakpoint.first_detected_at, Breakpoint.id, Evidence.created_at)
                )
            ).all()
        )
        grouped: dict[UUID, tuple[Breakpoint, list[BreakpointEvidenceLink]]] = {}
        for breakpoint, link, _evidence in rows:
            current = grouped.setdefault(breakpoint.id, (breakpoint, []))
            current[1].append(
                BreakpointEvidenceLink(
                    evidence_id=link.evidence_id,
                    relationship=cast(
                        Literal[
                            "CREATED",
                            "REINFORCED",
                            "CONTRADICTED",
                            "RESOLUTION_SUPPORT",
                        ],
                        link.relationship,
                    ),
                )
            )
        result: list[CanonicalBreakpointSource] = []
        for breakpoint, links in grouped.values():
            concept = await self._session.get(Concept, breakpoint.concept_id)
            skill = await self._session.get(SkillDimension, breakpoint.skill_dimension_id)
            if concept is None or skill is None:
                continue
            result.append(
                CanonicalBreakpointSource(
                    id=breakpoint.id,
                    status=breakpoint.status,
                    severity=breakpoint.severity,
                    summary=_bounded(breakpoint.summary, 700),
                    concept_target=EvidenceTarget(
                        id=concept.id,
                        canonical_key=concept.canonical_key,
                        display_name=concept.display_name,
                    ),
                    skill_target=EvidenceTarget(
                        id=skill.id,
                        canonical_key=skill.canonical_key,
                        display_name=skill.display_name,
                    ),
                    evidence_links=links,
                )
            )
        return result


def _stage_by_version(transitions: list[InterviewStageTransition]) -> dict[int, str]:
    if not transitions:
        return {}
    result = {0: transitions[0].from_stage}
    result.update({item.state_version: item.to_stage for item in transitions})
    return result


def _stage_for_version(stages: dict[int, str], version: int) -> str | None:
    candidates = [key for key in stages if key <= version]
    return stages[max(candidates)] if candidates else None


def _bounded(value: str, maximum: int) -> str:
    return " ".join(value.split())[:maximum]
