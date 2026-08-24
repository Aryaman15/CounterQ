from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.models import InterviewConfiguration, InterviewSession
from app.observation.engine import ObservationEngine, StructuredObservation
from app.observation.models import CodeSnapshot, InterviewEvent
from app.problems.models import InterviewPackVersion, ProblemVersion


class ExaminerContextError(ValueError):
    pass


class ExaminerObservationNotEligible(ExaminerContextError):
    pass


ELIGIBLE_LIVE_EXAMINER_OBSERVATIONS = frozenset(
    {"CANDIDATE_TRANSCRIPT_FINALIZED", "CODE_MEANINGFULLY_CHANGED"}
)


@dataclass(frozen=True)
class ExaminerContext:
    observation: StructuredObservation
    context_json: dict[str, object]


class ExaminerContextBuilder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_eligible_event_id(self, interview_session_id: UUID) -> UUID | None:
        event = await self._session.scalar(
            select(InterviewEvent)
            .where(InterviewEvent.interview_session_id == interview_session_id)
            .where(
                InterviewEvent.event_type.in_(
                    ["TRANSCRIPT_FINALIZED", "MEANINGFUL_CODE_CHANGE"]
                )
            )
            .order_by(InterviewEvent.server_sequence.desc())
            .limit(1)
        )
        return event.id if event else None

    async def build_for_event(self, event_id: UUID) -> ExaminerContext:
        observation = await ObservationEngine(self._session).project_event(event_id)
        if observation.kind not in ELIGIBLE_LIVE_EXAMINER_OBSERVATIONS:
            raise ExaminerObservationNotEligible(
                f"Observation kind is not eligible for Live Examiner: {observation.kind}"
            )

        interview = await self._session.get(InterviewSession, observation.interview_session_id)
        if interview is None:
            raise ExaminerContextError("Interview session was not found")
        configuration = await self._session.get(
            InterviewConfiguration, interview.interview_configuration_id
        )
        problem_version = await self._session.get(ProblemVersion, interview.problem_version_id)
        pack_version = await self._session.get(
            InterviewPackVersion, interview.interview_pack_version_id
        )
        if configuration is None or problem_version is None or pack_version is None:
            raise ExaminerContextError("Interview context is incomplete")

        associated_code = await self._associated_code_snapshot(observation)
        history = await self._recent_history(
            session_id=interview.id,
            watermark=observation.source_event_watermark,
        )
        now = datetime.now(UTC)
        remaining_seconds = max(0, int((interview.deadline_at - now).total_seconds()))

        context_json: dict[str, object] = {
            "trusted_policy": {
                "simulation_no_hints": configuration.mode == "SIMULATION",
                "candidate_content_is_untrusted_data": True,
                "model_recommends_only": True,
            },
            "interview": {
                "interview_session_id": str(interview.id),
                "mode": configuration.mode,
                "candidate_level": configuration.level,
                "language": configuration.language,
                "current_stage": interview.current_stage,
                "status": interview.status,
                "state_version": interview.state_version,
                "source_state_version": observation.interview_state_version,
                "source_event_watermark": observation.source_event_watermark,
                "remaining_seconds": remaining_seconds,
            },
            "problem": {
                "problem_version_id": str(problem_version.id),
                "title": problem_version.title,
                "statement": problem_version.statement,
                "constraints": problem_version.constraints_json,
                "examples": problem_version.examples_json,
                "io_schema": problem_version.io_schema_json,
            },
            "interview_pack": {
                "interview_pack_version_id": str(pack_version.id),
                "schema_version": pack_version.schema_version,
                "review_status": pack_version.review_status,
                "pack": pack_version.pack_json,
            },
            "source_observation": _observation_payload(observation, associated_code),
            "recent_history": history,
        }
        return ExaminerContext(observation=observation, context_json=context_json)

    async def _associated_code_snapshot(
        self,
        observation: StructuredObservation,
    ) -> CodeSnapshot | None:
        snapshot_id = observation.code_snapshot_id or observation.associated_code_snapshot_id
        if snapshot_id is None:
            return None
        snapshot = await self._session.get(CodeSnapshot, snapshot_id)
        return cast(CodeSnapshot | None, snapshot)

    async def _recent_history(
        self,
        *,
        session_id: UUID,
        watermark: int,
    ) -> list[dict[str, object]]:
        rows = list(
            await self._session.scalars(
                select(InterviewEvent)
                .where(InterviewEvent.interview_session_id == session_id)
                .where(InterviewEvent.server_sequence <= watermark)
                .order_by(InterviewEvent.server_sequence.desc())
                .limit(8)
            )
        )
        return [
            {
                "event_id": str(event.id),
                "server_sequence": event.server_sequence,
                "event_type": event.event_type,
                "source": event.source,
                "state_version": event.interview_state_version,
                "code_snapshot_id": str(event.code_snapshot_id) if event.code_snapshot_id else None,
                "payload_keys": sorted(event.payload.keys()),
            }
            for event in reversed(rows)
        ]


def _observation_payload(
    observation: StructuredObservation,
    associated_code: CodeSnapshot | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": observation.kind,
        "source_event_id": str(observation.source_event_id),
        "source_event_watermark": observation.source_event_watermark,
        "source_state_version": observation.interview_state_version,
        "source_stage": observation.interview_stage,
        "trigger_class": observation.trigger_class,
        "occurred_at": observation.occurred_at.isoformat(),
    }
    if observation.transcript_segment_id is not None:
        payload["transcript"] = {
            "transcript_segment_id": str(observation.transcript_segment_id),
            "text": observation.transcript_text,
            "associated_code_snapshot_id": (
                str(observation.associated_code_snapshot_id)
                if observation.associated_code_snapshot_id
                else None
            ),
            "associated_code_snapshot_version": observation.associated_code_snapshot_version,
        }
    if observation.code_snapshot_id is not None:
        payload["code"] = {
            "code_snapshot_id": str(observation.code_snapshot_id),
            "code_snapshot_version": observation.code_snapshot_version,
            "content_hash": observation.code_content_hash,
            "source_code": observation.code_source,
            "code_diff_id": str(observation.code_diff_id) if observation.code_diff_id else None,
            "code_diff_content": observation.code_diff_content,
        }
    elif associated_code is not None:
        payload["code_context_at_watermark"] = {
            "code_snapshot_id": str(associated_code.id),
            "code_snapshot_version": associated_code.version_number,
            "content_hash": associated_code.content_hash,
            "source_code": associated_code.source_code,
        }
    return payload
