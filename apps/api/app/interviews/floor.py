from __future__ import annotations

from dataclasses import dataclass

from app.db.constants import CONVERSATION_FLOOR_STATES


class ConversationFloorError(ValueError):
    pass


@dataclass(frozen=True)
class ConversationFloor:
    state: str = "IDLE"
    active_prompt_delivery_id: str | None = None

    def __post_init__(self) -> None:
        if self.state not in CONVERSATION_FLOOR_STATES:
            raise ConversationFloorError(f"Unknown conversation floor state: {self.state}")

    def candidate_speech_started(self) -> ConversationFloor:
        if self.state == "COUNTERQ_SPEAKING":
            return ConversationFloor(state="INTERRUPTED", active_prompt_delivery_id=None)
        return ConversationFloor(state="CANDIDATE_SPEAKING")

    def candidate_paused(self) -> ConversationFloor:
        if self.state != "CANDIDATE_SPEAKING":
            return self
        return ConversationFloor(state="CANDIDATE_THINKING")

    def release(self) -> ConversationFloor:
        return ConversationFloor(state="IDLE")

    def try_counterq_speaking(self, prompt_delivery_id: str) -> ConversationFloor | None:
        if self.state in {"COUNTERQ_SPEAKING", "CANDIDATE_SPEAKING"}:
            return None
        return ConversationFloor(
            state="COUNTERQ_SPEAKING",
            active_prompt_delivery_id=prompt_delivery_id,
        )
