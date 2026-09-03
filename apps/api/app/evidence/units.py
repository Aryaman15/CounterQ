from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence.independence import IndependenceAttributionService
from app.evidence.models import SkillDimension
from app.evidence.policy import ASSESSMENT_INPUT_CONTRACT_VERSION
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
from app.problems.models import Concept, InterviewPackVersion, ProblemConcept, ProblemVersion


class AssessmentUnitKind(StrEnum):
    PROMPTED_RESPONSE = "PROMPTED_RESPONSE"
    DIRECT_CODE = "DIRECT_CODE"
    EXECUTION_DEBUGGING = "EXECUTION_DEBUGGING"
    SELF_CORRECTION = "SELF_CORRECTION"
    COMBINED_SPEECH_CODE = "COMBINED_SPEECH_CODE"


@dataclass(frozen=True)
class AssessmentSourceFact:
    alias: str
    event_id: UUID
    server_sequence: int
    event_type: str
    event_source: str
    source_role: str


@dataclass(frozen=True)
class AssessmentUnit:
    unit_key: str
    kind: AssessmentUnitKind
    interview_session_id: UUID
    sort_sequence: int
    sources: tuple[AssessmentSourceFact, ...]
    independence_level: str | None
    independence_reason: str
    candidate_response_id: UUID | None
    source_code_snapshot_id: UUID | None
    concept_ids_by_key: dict[str, UUID]
    skill_ids_by_key: dict[str, UUID]
    input_payload: dict[str, object]

    def serialize(self) -> str:
        return serialize_assessment_input(self.input_payload)


def is_successful_recovery_unit(unit: AssessmentUnit) -> bool:
    """Return whether canonical execution facts show failure followed by success."""

    if unit.kind != AssessmentUnitKind.EXECUTION_DEBUGGING:
        return False
    assessment_unit = unit.input_payload.get("assessment_unit")
    if not isinstance(assessment_unit, dict):
        return False
    execution = assessment_unit.get("execution")
    if not isinstance(execution, dict) or execution.get("status") != "SUCCEEDED":
        return False
    previous_execution = execution.get("previous_failed_execution")
    return (
        isinstance(previous_execution, dict)
        and isinstance(previous_execution.get("status"), str)
        and previous_execution["status"] != "SUCCEEDED"
    )


class AssessmentInputBuilder:
    """Read-only deterministic projection from durable session facts to bounded units."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._independence = IndependenceAttributionService(session)

    async def build_completed_simulation(self, session_id: UUID) -> list[AssessmentUnit]:
        return await self._build_session(
            session_id,
            required_mode="SIMULATION",
            require_completed=True,
        )

    async def build_completed_session(self, session_id: UUID) -> list[AssessmentUnit]:
        return await self._build_session(session_id, require_completed=True)

    async def build_active_checkpoint(self, session_id: UUID) -> list[AssessmentUnit]:
        return await self._build_session(session_id, required_mode="COACH", require_active=True)

    async def build_for_revalidation(self, session_id: UUID) -> list[AssessmentUnit]:
        return await self._build_session(session_id)

    async def _build_session(
        self,
        session_id: UUID,
        *,
        required_mode: str | None = None,
        require_completed: bool = False,
        require_active: bool = False,
    ) -> list[AssessmentUnit]:
        interview = await self._session.get(InterviewSession, session_id)
        if interview is None:
            raise ValueError("InterviewSession was not found")
        configuration = await self._session.get(
            InterviewConfiguration, interview.interview_configuration_id
        )
        if configuration is None or (
            required_mode is not None and configuration.mode != required_mode
        ):
            raise ValueError("Assessment session mode is not eligible")
        if require_completed and (
            interview.status != "COMPLETED" or interview.completed_at is None
        ):
            raise ValueError("Stage 5 session evaluation requires a completed interview")
        if require_active and interview.status != "ACTIVE":
            raise ValueError("Active Evidence checkpoint requires an active interview")
        problem = await self._session.get(ProblemVersion, interview.problem_version_id)
        pack = await self._session.get(InterviewPackVersion, interview.interview_pack_version_id)
        if problem is None or pack is None or pack.review_status != "REVIEWED":
            raise ValueError("Completed session has no reviewed exact problem/pack binding")

        events = list(
            await self._session.scalars(
                select(InterviewEvent)
                .where(InterviewEvent.interview_session_id == session_id)
                .order_by(InterviewEvent.server_sequence)
            )
        )
        events_by_id = {event.id: event for event in events}
        segments = list(
            await self._session.scalars(
                select(TranscriptSegment).where(
                    TranscriptSegment.interview_session_id == session_id
                )
            )
        )
        segments_by_event = {segment.interview_event_id: segment for segment in segments}
        snapshots = list(
            await self._session.scalars(
                select(CodeSnapshot)
                .where(CodeSnapshot.interview_session_id == session_id)
                .order_by(CodeSnapshot.version_number)
            )
        )
        snapshots_by_id = {snapshot.id: snapshot for snapshot in snapshots}
        snapshots_by_event = {snapshot.created_from_event_id: snapshot for snapshot in snapshots}
        diffs = list(
            await self._session.scalars(
                select(CodeDiff).where(CodeDiff.interview_session_id == session_id)
            )
        )
        diffs_by_event = {diff.created_from_event_id: diff for diff in diffs}
        concept_ids_by_key = await self._problem_concepts(problem.id)
        skill_ids_by_key = await self._skills()
        common = {
            "input_contract_version": ASSESSMENT_INPUT_CONTRACT_VERSION,
            "session": {
                "id": str(interview.id),
                "mode": configuration.mode,
                "candidate_level": configuration.level,
            },
            "problem": {
                "id": str(problem.id),
                "title": problem.title,
                "statement": _bounded_text(problem.statement, 12000),
                "constraints": _bound_json(problem.constraints_json),
                "examples": _bound_json(problem.examples_json),
            },
            "reviewed_technical_reference": _bounded_pack(pack.pack_json),
            "concept_allowlist": sorted(concept_ids_by_key),
            "skill_dimension_allowlist": sorted(skill_ids_by_key),
        }

        units: list[AssessmentUnit] = []
        response_event_ids: set[UUID] = set()
        responses = list(
            await self._session.scalars(
                select(CandidateResponse)
                .where(CandidateResponse.interview_session_id == session_id)
                .order_by(CandidateResponse.started_at, CandidateResponse.id)
            )
        )
        for response in responses:
            if response.ended_at is None or response.completion_reason not in {
                "COMPLETE",
                "INTERRUPTED",
                "SUPERSEDED",
                "TIMEOUT",
                "SPONTANEOUS",
            }:
                continue
            response_sources = list(
                await self._session.scalars(
                    select(CandidateResponseSource)
                    .where(CandidateResponseSource.candidate_response_id == response.id)
                    .order_by(CandidateResponseSource.sequence)
                )
            )
            all_source_pairs = [
                (events_by_id[source.interview_event_id], source.source_role)
                for source in response_sources
                if source.interview_event_id in events_by_id
            ]
            if not all_source_pairs:
                continue
            response_event_ids.update(event.id for event, _role in all_source_pairs)
            source_pairs = _bounded_pairs(all_source_pairs)
            attribution = await self._independence.for_response(response)
            facts = self._facts(source_pairs)
            prompt_context = await self._actual_prompt_context(response)
            if prompt_context is not None:
                prompt_event, context = prompt_context
                facts = (*facts, self._fact(prompt_event, "CONTEXT", len(facts) + 1))
            has_code = any(
                event.event_type in {"CODE_SNAPSHOT_CREATED", "MEANINGFUL_CODE_CHANGE"}
                for event, _role in source_pairs
            )
            kind = (
                AssessmentUnitKind.COMBINED_SPEECH_CODE
                if has_code
                else AssessmentUnitKind.PROMPTED_RESPONSE
            )
            source_snapshot = next(
                (
                    snapshots_by_id[event.code_snapshot_id]
                    for event, _role in reversed(source_pairs)
                    if event.code_snapshot_id in snapshots_by_id
                ),
                None,
            )
            payload: dict[str, object] = {
                **common,
                "assessment_unit": {
                    "kind": kind.value,
                    "source_allowlist": [_source_json(fact) for fact in facts],
                    "independence": {
                        "level": attribution.level,
                        "reason": attribution.reason,
                        "software_fact": True,
                    },
                    "candidate_response": {
                        "id": str(response.id),
                        "completion_reason": response.completion_reason,
                        "sources": [
                            _event_content(
                                event, segments_by_event, snapshots_by_event, diffs_by_event
                            )
                            for event, _role in source_pairs
                        ],
                    },
                    "actual_delivered_prompt": context if prompt_context is not None else None,
                    "limitations": ([] if attribution.resolved else ["INDEPENDENCE_UNRESOLVED"]),
                },
            }
            units.append(
                _unit(
                    kind=kind,
                    session_id=session_id,
                    facts=facts,
                    attribution_level=attribution.level,
                    attribution_reason=attribution.reason,
                    response_id=response.id,
                    snapshot_id=source_snapshot.id if source_snapshot is not None else None,
                    concepts=concept_ids_by_key,
                    skills=skill_ids_by_key,
                    payload=payload,
                )
            )

        execution_source_ids: set[UUID] = set()
        runs = list(
            await self._session.scalars(
                select(ExecutionRun)
                .where(ExecutionRun.interview_session_id == session_id)
                .order_by(ExecutionRun.started_at, ExecutionRun.id)
            )
        )
        for run_index, run in enumerate(runs):
            if run.status == "RUNNING":
                continue
            previous_run = runs[run_index - 1] if run_index > 0 else None
            debugging_sequence = (
                previous_run is not None
                and previous_run.status != "SUCCEEDED"
                and run.status == "SUCCEEDED"
            )
            if debugging_sequence and previous_run is not None:
                previous_event = events_by_id[previous_run.run_event_id]
                current_run_events = [
                    event
                    for event in events
                    if event.id == run.run_event_id
                    or event.payload.get("execution_run_id") == str(run.id)
                ]
                upper_sequence = max(event.server_sequence for event in current_run_events)
                run_events = [
                    event
                    for event in events
                    if previous_event.server_sequence <= event.server_sequence <= upper_sequence
                    and (
                        event.event_type
                        in {
                            "TRANSCRIPT_FINALIZED",
                            "CODE_SNAPSHOT_CREATED",
                            "MEANINGFUL_CODE_CHANGE",
                            "RUN_CLICKED",
                            "COMPILE_COMPLETED",
                            "TEST_COMPLETED",
                        }
                    )
                ]
            else:
                run_events = [
                    event
                    for event in events
                    if event.id == run.run_event_id
                    or event.payload.get("execution_run_id") == str(run.id)
                ]
            run_events = _bounded_events(run_events)
            if not run_events:
                continue
            execution_source_ids.update(event.id for event in run_events)
            attribution = await self._independence.for_event_window(run_events)
            facts = self._facts(
                [
                    (event, "PRIMARY" if event.id == run.run_event_id else "SUPPORTING")
                    for event in run_events
                ]
            )
            test_results = list(
                await self._session.scalars(
                    select(TestResult)
                    .where(TestResult.execution_run_id == run.id)
                    .order_by(TestResult.test_identifier)
                )
            )
            snapshot = snapshots_by_id.get(run.code_snapshot_id)
            payload = cast(
                dict[str, object],
                {
                    **common,
                    "assessment_unit": {
                        "kind": AssessmentUnitKind.EXECUTION_DEBUGGING.value,
                        "source_allowlist": [_source_json(fact) for fact in facts],
                        "independence": {
                            "level": attribution.level,
                            "reason": attribution.reason,
                            "software_fact": True,
                        },
                        "execution": {
                            "id": str(run.id),
                            "status": run.status,
                            "compiler_output": _bounded_text(run.compiler_output, 6000),
                            "stderr": _bounded_text(run.stderr, 6000),
                            "stdout": _bounded_text(run.stdout, 6000),
                            "exit_code": run.exit_code,
                            "timed_out": run.timed_out,
                            "code_snapshot": _snapshot_json(snapshot),
                            "tests": [
                                {
                                    "identifier": result.test_identifier,
                                    "status": result.status,
                                    "input": result.input_json,
                                    "expected_output": result.expected_output,
                                    "actual_output": result.actual_output,
                                    "failure_classification": result.failure_classification,
                                }
                                for result in test_results
                            ],
                            "behavior_sequence": [
                                _event_content(
                                    event,
                                    segments_by_event,
                                    snapshots_by_event,
                                    diffs_by_event,
                                )
                                for event in run_events
                            ],
                            "previous_failed_execution": (
                                {
                                    "id": str(previous_run.id),
                                    "status": previous_run.status,
                                    "compiler_output": _bounded_text(
                                        previous_run.compiler_output, 6000
                                    ),
                                    "stderr": _bounded_text(previous_run.stderr, 6000),
                                    "stdout": _bounded_text(previous_run.stdout, 6000),
                                    "code_snapshot": _snapshot_json(
                                        snapshots_by_id.get(previous_run.code_snapshot_id)
                                    ),
                                }
                                if debugging_sequence and previous_run is not None
                                else None
                            ),
                        },
                        "limitations": (
                            [] if attribution.resolved else ["INDEPENDENCE_UNRESOLVED"]
                        ),
                    },
                },
            )
            units.append(
                _unit(
                    kind=AssessmentUnitKind.EXECUTION_DEBUGGING,
                    session_id=session_id,
                    facts=facts,
                    attribution_level=attribution.level,
                    attribution_reason=attribution.reason,
                    response_id=None,
                    snapshot_id=run.code_snapshot_id,
                    concepts=concept_ids_by_key,
                    skills=skill_ids_by_key,
                    payload=payload,
                )
            )

        for event in events:
            if (
                event.id in response_event_ids
                or event.id in execution_source_ids
                or event.event_type not in {"CODE_SNAPSHOT_CREATED", "MEANINGFUL_CODE_CHANGE"}
                or event.source != "NATIVE_EDITOR"
            ):
                continue
            snapshot = snapshots_by_event.get(event.id)
            if snapshot is None:
                continue
            attribution = await self._independence.for_direct_event(event)
            diff = diffs_by_event.get(event.id)
            kind = AssessmentUnitKind.DIRECT_CODE
            facts = self._facts([(event, "PRIMARY")])
            previous_snapshot = (
                snapshots_by_id.get(snapshot.parent_snapshot_id)
                if snapshot.parent_snapshot_id is not None
                else None
            )
            payload = cast(
                dict[str, object],
                {
                    **common,
                    "assessment_unit": {
                        "kind": kind.value,
                        "source_allowlist": [_source_json(fact) for fact in facts],
                        "independence": {
                            "level": attribution.level,
                            "reason": attribution.reason,
                            "software_fact": True,
                        },
                        "code": {
                            "current": _snapshot_json(snapshot),
                            "previous": _snapshot_json(previous_snapshot),
                            "diff": _diff_json(diff),
                            "candidate_revision_observed": diff is not None,
                            "correction_status": "NOT_DETERMINED_BY_SOFTWARE",
                        },
                        "limitations": (
                            [] if attribution.resolved else ["INDEPENDENCE_UNRESOLVED"]
                        ),
                    },
                },
            )
            units.append(
                _unit(
                    kind=kind,
                    session_id=session_id,
                    facts=facts,
                    attribution_level=attribution.level,
                    attribution_reason=attribution.reason,
                    response_id=None,
                    snapshot_id=snapshot.id,
                    concepts=concept_ids_by_key,
                    skills=skill_ids_by_key,
                    payload=payload,
                )
            )
        return sorted(units, key=lambda unit: (unit.sort_sequence, unit.unit_key))

    async def _actual_prompt_context(
        self, response: CandidateResponse
    ) -> tuple[InterviewEvent, dict[str, object]] | None:
        if response.interviewer_prompt_id is None:
            return None
        delivery = await self._session.scalar(
            select(InterviewerPromptDelivery)
            .where(
                InterviewerPromptDelivery.interviewer_prompt_id == response.interviewer_prompt_id,
                InterviewerPromptDelivery.delivery_state.in_(("DELIVERED", "PARTIALLY_DELIVERED")),
                InterviewerPromptDelivery.actual_transcript_segment_id.is_not(None),
            )
            .order_by(InterviewerPromptDelivery.delivery_attempt.desc())
            .limit(1)
        )
        prompt = await self._session.get(InterviewerPrompt, response.interviewer_prompt_id)
        if delivery is None or prompt is None:
            return None
        segment = await self._session.get(TranscriptSegment, delivery.actual_transcript_segment_id)
        if segment is None:
            return None
        event = await self._session.get(InterviewEvent, segment.interview_event_id)
        if event is None or event.event_type not in {
            "COUNTERQ_UTTERANCE_DELIVERED",
            "CANDIDATE_INTERRUPTED_COUNTERQ",
        }:
            return None
        return event, {
            "prompt_id": str(prompt.id),
            "kind": prompt.kind,
            "probe_strategy": prompt.probe_strategy,
            "actual_transcript": _bounded_text(segment.text, 3000),
            "delivery_state": delivery.delivery_state,
            "delivery_event_id": str(event.id),
            "server_sequence": event.server_sequence,
        }

    async def _problem_concepts(self, problem_version_id: UUID) -> dict[str, UUID]:
        rows = (
            await self._session.execute(
                select(Concept.canonical_key, Concept.id)
                .join(ProblemConcept, ProblemConcept.concept_id == Concept.id)
                .where(
                    ProblemConcept.problem_version_id == problem_version_id,
                    Concept.status == "ACTIVE",
                )
            )
        ).all()
        return {str(key): cast(UUID, concept_id) for key, concept_id in rows}

    async def _skills(self) -> dict[str, UUID]:
        rows = (
            await self._session.execute(
                select(SkillDimension.canonical_key, SkillDimension.id).where(
                    SkillDimension.status == "ACTIVE"
                )
            )
        ).all()
        return {str(key): cast(UUID, skill_id) for key, skill_id in rows}

    @staticmethod
    def _facts(pairs: list[tuple[InterviewEvent, str]]) -> tuple[AssessmentSourceFact, ...]:
        return tuple(
            AssessmentInputBuilder._fact(event, _evidence_role(role), index)
            for index, (event, role) in enumerate(pairs, start=1)
        )

    @staticmethod
    def _fact(event: InterviewEvent, role: str, index: int) -> AssessmentSourceFact:
        return AssessmentSourceFact(
            alias=f"source_{index}",
            event_id=event.id,
            server_sequence=event.server_sequence,
            event_type=event.event_type,
            event_source=event.source,
            source_role=role,
        )


def _unit(
    *,
    kind: AssessmentUnitKind,
    session_id: UUID,
    facts: tuple[AssessmentSourceFact, ...],
    attribution_level: str | None,
    attribution_reason: str,
    response_id: UUID | None,
    snapshot_id: UUID | None,
    concepts: dict[str, UUID],
    skills: dict[str, UUID],
    payload: dict[str, object],
) -> AssessmentUnit:
    identity = {
        "kind": kind.value,
        "sources": [str(fact.event_id) for fact in facts],
        "response_id": str(response_id) if response_id else None,
        "snapshot_id": str(snapshot_id) if snapshot_id else None,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return AssessmentUnit(
        unit_key=f"sha256:{digest}",
        kind=kind,
        interview_session_id=session_id,
        sort_sequence=min(fact.server_sequence for fact in facts),
        sources=facts,
        independence_level=attribution_level,
        independence_reason=attribution_reason,
        candidate_response_id=response_id,
        source_code_snapshot_id=snapshot_id,
        concept_ids_by_key=concepts,
        skill_ids_by_key=skills,
        input_payload=payload,
    )


def _evidence_role(response_role: str) -> str:
    return {
        "PRIMARY": "PRIMARY",
        "SUPPORTING": "SUPPORTING",
        "CODE_CONTEXT": "SUPPORTING",
        "RUN_CONTEXT": "SUPPORTING",
    }.get(response_role, "SUPPORTING")


def _source_json(fact: AssessmentSourceFact) -> dict[str, object]:
    return {
        "alias": fact.alias,
        "event_id": str(fact.event_id),
        "server_sequence": fact.server_sequence,
        "event_type": fact.event_type,
        "event_source": fact.event_source,
        "source_role": fact.source_role,
    }


def _event_content(
    event: InterviewEvent,
    segments: dict[UUID, TranscriptSegment],
    snapshots: dict[UUID, CodeSnapshot],
    diffs: dict[UUID, CodeDiff],
) -> dict[str, object]:
    segment = segments.get(event.id)
    snapshot = snapshots.get(event.id)
    diff = diffs.get(event.id)
    return {
        "event_id": str(event.id),
        "server_sequence": event.server_sequence,
        "event_type": event.event_type,
        "event_source": event.source,
        "candidate_transcript": (
            _bounded_text(segment.text, 6000)
            if segment and segment.speaker == "CANDIDATE"
            else None
        ),
        "code_snapshot": _snapshot_json(snapshot),
        "code_diff": _diff_json(diff),
        "execution_fact": event.payload if event.source == "NATIVE_RUNNER" else None,
    }


def _snapshot_json(snapshot: CodeSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "id": str(snapshot.id),
        "version": snapshot.version_number,
        "parent_snapshot_id": str(snapshot.parent_snapshot_id)
        if snapshot.parent_snapshot_id
        else None,
        "language": snapshot.language,
        "content_hash": snapshot.content_hash,
        "source_code": _bounded_text(snapshot.source_code, 12000),
    }


def _diff_json(diff: CodeDiff | None) -> dict[str, object] | None:
    if diff is None:
        return None
    return {
        "id": str(diff.id),
        "from_snapshot_id": str(diff.from_snapshot_id),
        "to_snapshot_id": str(diff.to_snapshot_id),
        "format": diff.diff_format,
        "content": _bounded_text(diff.diff_content, 12000),
        "significance": diff.significance,
    }


def _bounded_pack(pack: dict[str, object]) -> dict[str, object]:
    selected = {
        key: pack[key]
        for key in (
            "expected_approaches",
            "reference_solutions",
            "reference_reasoning",
            "invariants",
            "common_failure_modes",
            "edge_cases",
            "complexity_expectations",
        )
        if key in pack
    }
    return cast(dict[str, object], _bound_json(selected))


def _bound_json(value: object) -> object:
    if isinstance(value, str):
        return value[:6000]
    if isinstance(value, list):
        return [_bound_json(item) for item in value[:8]]
    if isinstance(value, dict):
        return {str(key): _bound_json(item) for key, item in list(value.items())[:24]}
    return value


def _bounded_text(value: str, character_limit: int) -> str:
    return value[:character_limit]


def _bounded_events(events: list[InterviewEvent]) -> list[InterviewEvent]:
    if len(events) <= 12:
        return events
    return [*events[:6], *events[-6:]]


def _bounded_pairs(
    pairs: list[tuple[InterviewEvent, str]],
) -> list[tuple[InterviewEvent, str]]:
    if len(pairs) <= 12:
        return pairs
    return [*pairs[:6], *pairs[-6:]]


def serialize_assessment_input(payload: dict[str, object]) -> str:
    """Canonical serializer shared by production and the opt-in live evaluator."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
