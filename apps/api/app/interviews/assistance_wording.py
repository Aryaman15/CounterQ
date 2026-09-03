"""AI Gateway boundary for candidate-visible Coach assistance wording."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.ai_gateway.gateway import AIGateway
from app.interviews.assistance_policy import (
    COACH_ASSISTANCE_INSTRUCTIONS,
    CoachAssistanceInput,
    CoachAssistanceOutput,
    coach_assistance_policy_descriptor,
    serialize_coach_assistance_input,
)

COACH_ASSISTANCE_PURPOSE = "coach_assistance"


@dataclass(frozen=True)
class CoachAssistanceWording:
    prompt_text: str
    invocation_id: UUID


class CoachAssistanceWordingService:
    def __init__(self, gateway: AIGateway) -> None:
        self._gateway = gateway

    async def generate(
        self,
        *,
        interview_session_id: UUID,
        request_event_id: UUID,
        wording_input: CoachAssistanceInput,
    ) -> CoachAssistanceWording:
        result = await self._gateway.reason_structured(
            interview_session_id=interview_session_id,
            capability="STANDARD_REASONING",
            purpose=COACH_ASSISTANCE_PURPOSE,
            policy=coach_assistance_policy_descriptor(),
            instructions=COACH_ASSISTANCE_INSTRUCTIONS,
            input_content=serialize_coach_assistance_input(wording_input),
            output_model=CoachAssistanceOutput,
            correlation_id=f"coach-assistance:{request_event_id}",
            metadata={
                "request_event_id": str(request_event_id),
                "selected_hint_level": wording_input.selected_hint_level,
                "assistance_type": wording_input.assistance_type,
            },
        )
        return CoachAssistanceWording(
            prompt_text=result.parsed.prompt_text,
            invocation_id=result.invocation_id,
        )
