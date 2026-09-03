from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviews.mode_policy import independence_for_hint_level, strongest_independence
from app.interviews.models import (
    CandidateResponse,
    CandidateResponseSource,
    InterviewerPrompt,
    InterviewerPromptDelivery,
)
from app.observation.models import InterviewEvent, TranscriptSegment

RESPONSE_BEARING_PROMPT_KINDS = frozenset(
    ("BASE_QUESTION", "CLARIFICATION", "PROBE", "INSTRUCTION")
)
_PROMPT_LIFECYCLE_EVENT_TYPES = (
    "COUNTERQ_UTTERANCE_DELIVERED",
    "CANDIDATE_INTERRUPTED_COUNTERQ",
)


@dataclass(frozen=True)
class IndependenceAttribution:
    level: str | None
    reason: str

    @property
    def resolved(self) -> bool:
        return self.level is not None


@dataclass(frozen=True)
class _PromptInfluence:
    prompt: InterviewerPrompt
    delivery: InterviewerPromptDelivery
    event: InterviewEvent
    interrupted: bool


class IndependenceAttributionService:
    """Attribute independence from canonical delivery and causality truth only."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_response(self, response: CandidateResponse) -> IndependenceAttribution:
        if response.interviewer_prompt_id is None:
            response_event = await self._first_response_event(response.id)
            if response_event is None:
                return IndependenceAttribution(None, "RESPONSE_SOURCE_UNAVAILABLE")
            attribution = await self.for_event_window(
                (response_event,), include_previous_response_context=False
            )
            if attribution.level == "INDEPENDENT":
                return IndependenceAttribution("INDEPENDENT", "SPONTANEOUS_RESPONSE")
            return attribution
        delivered = await self._actual_delivery(response.interviewer_prompt_id)
        if delivered is None:
            return IndependenceAttribution(None, "PROMPT_DELIVERY_UNPROVED")
        prompt, _delivery, _event = delivered
        if not is_response_bearing_prompt(prompt):
            return IndependenceAttribution(None, "NON_RESPONSE_BEARING_PROMPT_ASSOCIATION")
        if prompt.assistance_type is not None:
            if prompt.hint_level is None:
                return IndependenceAttribution(None, "ASSISTANCE_LEVEL_UNAVAILABLE")
            return IndependenceAttribution(
                independence_for_hint_level(prompt.hint_level),
                "ACTUAL_ASSISTANCE_DELIVERY",
            )
        if prompt.kind == "PROBE":
            return IndependenceAttribution("AFTER_PROBE", "ACTUAL_DIAGNOSTIC_PROBE_DELIVERY")
        return IndependenceAttribution("INDEPENDENT", "NON_PROBE_INTERVIEWER_CONTEXT")

    async def for_direct_event(self, event: InterviewEvent) -> IndependenceAttribution:
        return await self.for_event_window((event,))

    async def for_event_window(
        self,
        events: tuple[InterviewEvent, ...] | list[InterviewEvent],
        *,
        include_previous_response_context: bool = True,
        assistance_target_concept_ids: set[UUID] | None = None,
        assistance_target_skill_ids: set[UUID] | None = None,
    ) -> IndependenceAttribution:
        """Attribute the complete candidate behavior window, not only its first event."""

        if not events:
            return IndependenceAttribution(None, "CANDIDATE_EVENT_WINDOW_EMPTY")
        ordered = sorted(
            {event.id: event for event in events}.values(), key=lambda item: item.server_sequence
        )
        session_ids = {event.interview_session_id for event in ordered}
        if len(session_ids) != 1:
            raise ValueError("Independence event window must belong to one InterviewSession")
        interactions = await self._prompt_influences_for_window(
            interview_session_id=ordered[0].interview_session_id,
            start_sequence=ordered[0].server_sequence,
            end_sequence=ordered[-1].server_sequence,
            include_previous_response_context=include_previous_response_context,
        )
        assistance = [
            interaction
            for interaction in interactions
            if interaction.prompt.assistance_type is not None
            and interaction.prompt.hint_level is not None
            and (not interaction.interrupted or interaction.delivery.actual_transcript_segment_id)
            and _assistance_target_matches(
                interaction.prompt,
                concept_ids=assistance_target_concept_ids,
                skill_ids=assistance_target_skill_ids,
            )
        ]
        probes = [interaction for interaction in interactions if interaction.prompt.kind == "PROBE"]
        assistance_levels = [
            independence_for_hint_level(cast(str, interaction.prompt.hint_level))
            for interaction in assistance
        ]
        if assistance_levels:
            if probes:
                assistance_levels.append("AFTER_PROBE")
            return IndependenceAttribution(
                strongest_independence(assistance_levels),
                "STRONGEST_ACTUAL_PROMPT_INFLUENCE",
            )
        if not probes:
            reason = (
                "PRIOR_RESPONSE_BEARING_PROMPT_WAS_NOT_PROBE"
                if interactions
                else "NO_PRIOR_DELIVERED_OR_INTERRUPTED_PROBE"
            )
            return IndependenceAttribution("INDEPENDENT", reason)
        for probe in probes:
            if await self._window_has_causal_link(ordered, probe):
                return IndependenceAttribution("AFTER_PROBE", "EXPLICIT_PROBE_CAUSAL_LINK")
        if any(probe.interrupted for probe in probes):
            return IndependenceAttribution(None, "INTERRUPTED_PROBE_CAUSALITY_AMBIGUOUS")
        return IndependenceAttribution(None, "DIRECT_EVENT_AFTER_PROBE_CAUSALITY_AMBIGUOUS")

    async def _first_response_event(self, response_id: UUID) -> InterviewEvent | None:
        return cast(
            InterviewEvent | None,
            await self._session.scalar(
                select(InterviewEvent)
                .join(
                    CandidateResponseSource,
                    CandidateResponseSource.interview_event_id == InterviewEvent.id,
                )
                .where(CandidateResponseSource.candidate_response_id == response_id)
                .order_by(InterviewEvent.server_sequence)
                .limit(1)
            ),
        )

    async def _prompt_influences_for_window(
        self,
        *,
        interview_session_id: UUID,
        start_sequence: int,
        end_sequence: int,
        include_previous_response_context: bool,
    ) -> list[_PromptInfluence]:
        prior_candidate_sequences = list(
            await self._session.scalars(
                select(InterviewEvent.server_sequence)
                .where(
                    InterviewEvent.interview_session_id == interview_session_id,
                    InterviewEvent.event_type == "TRANSCRIPT_FINALIZED",
                    InterviewEvent.source == "CANDIDATE_VOICE",
                    InterviewEvent.server_sequence < start_sequence,
                )
                .order_by(InterviewEvent.server_sequence.desc())
                .limit(2 if include_previous_response_context else 1)
            )
        )
        # Direct code/debugging may still be influenced by the immediately
        # preceding response turn, so retain that turn's prompt window. A new
        # spontaneous transcript is itself a semantic boundary and excludes it.
        if include_previous_response_context:
            lower_bound = prior_candidate_sequences[1] if len(prior_candidate_sequences) > 1 else 0
        else:
            lower_bound = prior_candidate_sequences[0] if prior_candidate_sequences else 0
        lifecycle_events = list(
            await self._session.scalars(
                select(InterviewEvent)
                .where(
                    InterviewEvent.interview_session_id == interview_session_id,
                    InterviewEvent.event_type.in_(_PROMPT_LIFECYCLE_EVENT_TYPES),
                    InterviewEvent.server_sequence > lower_bound,
                    InterviewEvent.server_sequence <= end_sequence,
                )
                .order_by(InterviewEvent.server_sequence)
            )
        )
        influences: list[_PromptInfluence] = []
        for lifecycle_event in lifecycle_events:
            influence = await self._prompt_influence_from_event(lifecycle_event)
            if influence is not None and is_response_bearing_prompt(influence.prompt):
                influences.append(influence)
        return influences

    async def _window_has_causal_link(
        self, events: list[InterviewEvent], influence: _PromptInfluence
    ) -> bool:
        if any(
            event.causation_id == influence.event.id or event.correlation_id == influence.event.id
            for event in events
        ):
            return True
        response_id = await self._session.scalar(
            select(CandidateResponse.id)
            .join(
                CandidateResponseSource,
                CandidateResponseSource.candidate_response_id == CandidateResponse.id,
            )
            .where(
                CandidateResponse.interviewer_prompt_id == influence.prompt.id,
                CandidateResponseSource.interview_event_id.in_(event.id for event in events),
            )
            .limit(1)
        )
        return response_id is not None

    async def _prompt_influence_from_event(self, event: InterviewEvent) -> _PromptInfluence | None:
        if event.event_type == "COUNTERQ_UTTERANCE_DELIVERED":
            delivered = await self._actual_delivery_from_event(event)
            if delivered is None:
                return None
            prompt, delivery, delivery_event = delivered
            return _PromptInfluence(prompt, delivery, delivery_event, interrupted=False)
        if event.event_type != "CANDIDATE_INTERRUPTED_COUNTERQ":
            return None
        prompt_id = event.payload.get("interviewer_prompt_id")
        delivery_id = event.payload.get("prompt_delivery_id")
        if not isinstance(prompt_id, str) or not isinstance(delivery_id, str):
            return None
        try:
            prompt_uuid = UUID(prompt_id)
            delivery_uuid = UUID(delivery_id)
        except ValueError:
            return None
        interrupted_prompt = await self._session.get(InterviewerPrompt, prompt_uuid)
        interrupted_delivery = await self._session.get(InterviewerPromptDelivery, delivery_uuid)
        if (
            interrupted_prompt is None
            or interrupted_prompt.interview_session_id != event.interview_session_id
            or interrupted_delivery is None
            or interrupted_delivery.interview_session_id != event.interview_session_id
            or interrupted_delivery.interviewer_prompt_id != interrupted_prompt.id
            or interrupted_delivery.delivery_state not in {"INTERRUPTED", "PARTIALLY_DELIVERED"}
        ):
            return None
        return _PromptInfluence(interrupted_prompt, interrupted_delivery, event, interrupted=True)

    async def _actual_delivery(
        self, prompt_id: UUID
    ) -> tuple[InterviewerPrompt, InterviewerPromptDelivery, InterviewEvent] | None:
        delivery = await self._session.scalar(
            select(InterviewerPromptDelivery)
            .where(
                InterviewerPromptDelivery.interviewer_prompt_id == prompt_id,
                InterviewerPromptDelivery.delivery_state.in_(("DELIVERED", "PARTIALLY_DELIVERED")),
                InterviewerPromptDelivery.actual_transcript_segment_id.is_not(None),
            )
            .order_by(InterviewerPromptDelivery.delivery_attempt.desc())
            .limit(1)
        )
        if delivery is None:
            return None
        segment = await self._session.get(TranscriptSegment, delivery.actual_transcript_segment_id)
        if segment is None:
            return None
        event = await self._session.get(InterviewEvent, segment.interview_event_id)
        prompt = await self._session.get(InterviewerPrompt, prompt_id)
        if (
            event is None
            or prompt is None
            or event.event_type
            not in {"COUNTERQ_UTTERANCE_DELIVERED", "CANDIDATE_INTERRUPTED_COUNTERQ"}
        ):
            return None
        return prompt, delivery, event

    async def _actual_delivery_from_event(
        self, event: InterviewEvent
    ) -> tuple[InterviewerPrompt, InterviewerPromptDelivery, InterviewEvent] | None:
        delivery_id = event.payload.get("prompt_delivery_id")
        if not isinstance(delivery_id, str):
            return None
        try:
            delivery_uuid = UUID(delivery_id)
        except ValueError:
            return None
        delivery = await self._session.get(InterviewerPromptDelivery, delivery_uuid)
        if (
            delivery is None
            or delivery.delivery_state != "DELIVERED"
            or delivery.actual_transcript_segment_id is None
        ):
            return None
        prompt = await self._session.get(InterviewerPrompt, delivery.interviewer_prompt_id)
        segment = await self._session.get(TranscriptSegment, delivery.actual_transcript_segment_id)
        if prompt is None or segment is None or segment.interview_event_id != event.id:
            return None
        return prompt, delivery, event


def is_response_bearing_prompt(prompt: InterviewerPrompt) -> bool:
    if prompt.kind == "INSTRUCTION":
        return prompt.assistance_type is not None
    return prompt.kind in RESPONSE_BEARING_PROMPT_KINDS


def _assistance_target_matches(
    prompt: InterviewerPrompt,
    *,
    concept_ids: set[UUID] | None,
    skill_ids: set[UUID] | None,
) -> bool:
    if concept_ids is not None and prompt.target_concept_id is not None:
        if prompt.target_concept_id not in concept_ids:
            return False
    if skill_ids is not None and prompt.target_skill_dimension_id is not None:
        if prompt.target_skill_dimension_id not in skill_ids:
            return False
    return True
