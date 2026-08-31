from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.problems.contracts import CandidateLanguage, CandidateProblemDetail

CONTROL_PROTOCOL_VERSION: Literal["counterq.realtime.control.v1"] = (
    "counterq.realtime.control.v1"
)


class RealtimeDevelopmentBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["interview_demo"] = "interview_demo"
    interview_session_id: UUID | None = None
    client_instance_id: str | None = Field(default=None, min_length=1, max_length=128)
    last_acknowledged_server_sequence: int | None = Field(default=None, ge=0)
    problem_version_id: UUID | None = None
    language: CandidateLanguage | None = None

    @model_validator(mode="after")
    def validate_creation_or_restoration(self) -> RealtimeDevelopmentBootstrapRequest:
        if self.interview_session_id is None:
            if self.problem_version_id is None or self.language is None:
                raise ValueError(
                    "problem_version_id and language are required to create an interview"
                )
        elif self.interview_session_id is not None and (
            self.problem_version_id is not None or self.language is not None
        ):
            raise ValueError("Problem and language cannot be changed while restoring a session")
        return self


class RestoredCodeSnapshotMessage(BaseModel):
    id: UUID
    version_number: int
    language: str
    source_code: str
    content_hash: str


class RestoredConversationTurnMessage(BaseModel):
    id: UUID
    speaker: Literal["CANDIDATE", "COUNTERQ"]
    text: str
    sequence: int
    occurred_at: datetime
    delivery_state: str | None = None


class RestoredUnresolvedPromptMessage(BaseModel):
    id: UUID
    kind: str
    status: Literal["AUTHORIZED"]


class RealtimeDevelopmentBootstrapResponse(BaseModel):
    interview_session_id: UUID
    language: Literal["cpp", "python", "java"]
    problem: CandidateProblemDetail
    template: str
    configured_duration_seconds: int
    current_stage: str
    session_status: str
    state_version: int
    deadline_at: datetime
    time_remaining_seconds: int
    time_pressure: str
    control_websocket_path: str
    restoration: Literal["CREATED", "RESTORED"]
    restore_protocol_version: Literal["session.restore.v1"] = "session.restore.v1"
    started_at: datetime
    completed_at: datetime | None = None
    terminal_reason: Literal["USER_ENDED", "TIME_EXPIRED"] | None = None
    latest_code_snapshot: RestoredCodeSnapshotMessage | None = None
    recent_conversation: list[RestoredConversationTurnMessage]
    unresolved_prompt: RestoredUnresolvedPromptMessage | None = None
    highest_client_sequence: int
    last_server_sequence: int
    protocol_version: Literal["counterq.realtime.control.v1"] = CONTROL_PROTOCOL_VERSION


class ClientMessageBase(BaseModel):
    protocol_version: Literal["counterq.realtime.control.v1"] = CONTROL_PROTOCOL_VERSION
    client_event_id: str = Field(min_length=1, max_length=128)
    client_instance_id: str = Field(min_length=1, max_length=128)
    client_sequence: int = Field(ge=1)


class ClientHelloMessage(ClientMessageBase):
    type: Literal["client_hello"]
    last_acknowledged_server_sequence: int | None = Field(default=None, ge=0)


class CandidateSpeechStartedMessage(ClientMessageBase):
    type: Literal["candidate_speech_started"]
    provider_event_id: str | None = Field(default=None, max_length=256)
    provider_item_id: str | None = Field(default=None, max_length=256)
    occurred_at: datetime | None = None


class CandidateSpeechStoppedMessage(ClientMessageBase):
    type: Literal["candidate_speech_stopped"]
    provider_event_id: str | None = Field(default=None, max_length=256)
    provider_item_id: str | None = Field(default=None, max_length=256)
    occurred_at: datetime | None = None


class CandidateTranscriptFinalizedMessage(ClientMessageBase):
    type: Literal["candidate_transcript_finalized"]
    provider_item_id: str = Field(min_length=1, max_length=256)
    content_index: int | None = Field(default=None, ge=0)
    transcript: str = Field(min_length=1)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class CandidateCodeSnapshotMessage(ClientMessageBase):
    type: Literal["candidate_code_snapshot"]
    source_code: str
    language: str = Field(min_length=1, max_length=64)
    trigger: Literal["INITIAL_EDITOR_STATE", "EDIT_BURST"]
    occurred_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class CandidateCodeActivityStartedMessage(ClientMessageBase):
    type: Literal["candidate_code_activity_started"]
    occurred_at: datetime | None = None


class CandidateCodeActivityIdleMessage(ClientMessageBase):
    type: Literal["candidate_code_activity_idle"]
    occurred_at: datetime | None = None


class RealtimeDisconnectedMessage(ClientMessageBase):
    type: Literal["realtime_disconnected"]
    provider_session_id: str | None = Field(default=None, max_length=256)
    reason: str | None = Field(default=None, max_length=128)
    occurred_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class RealtimeReconnectedMessage(ClientMessageBase):
    type: Literal["realtime_reconnected"]
    provider_session_id: str | None = Field(default=None, max_length=256)
    occurred_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class DevelopmentAuthorizedPromptRequestMessage(ClientMessageBase):
    type: Literal["development_authorized_prompt_requested"]


class ExaminerDecisionPolicyGateRequestMessage(ClientMessageBase):
    type: Literal["examiner_decision_policy_gate_requested"]
    examiner_decision_id: UUID


class PromptDeliveryPermitRequestMessage(ClientMessageBase):
    type: Literal["prompt_delivery_permit_requested"]
    interviewer_prompt_id: UUID


class CounterQDeliveryStartedMessage(ClientMessageBase):
    type: Literal["counterq_delivery_started"]
    interviewer_prompt_id: UUID
    intended_text: str = Field(min_length=1)
    provider_response_id: str = Field(min_length=1, max_length=256)
    provider_item_id: str | None = Field(default=None, max_length=256)
    provider_event_id: str | None = Field(default=None, max_length=256)
    started_at: datetime | None = None


class CounterQDeliveryCompletedMessage(ClientMessageBase):
    type: Literal["counterq_delivery_completed"]
    interviewer_prompt_id: UUID
    prompt_delivery_id: UUID
    provider_response_id: str = Field(min_length=1, max_length=256)
    provider_item_id: str | None = Field(default=None, max_length=256)
    transcript: str = Field(min_length=1)
    completed_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class CounterQDeliveryInterruptedMessage(ClientMessageBase):
    type: Literal["counterq_delivery_interrupted"]
    interviewer_prompt_id: UUID
    prompt_delivery_id: UUID
    provider_response_id: str = Field(min_length=1, max_length=256)
    provider_item_id: str | None = Field(default=None, max_length=256)
    confirmed_by: str = Field(min_length=1, max_length=128)
    audio_end_ms: int | None = Field(default=None, ge=0)
    interrupted_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class CandidateEndInterviewMessage(ClientMessageBase):
    type: Literal["candidate_end_interview"]
    expected_state_version: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SessionDeadlineReachedMessage(ClientMessageBase):
    type: Literal["session_deadline_reached"]
    expected_state_version: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


ClientControlMessage = Annotated[
    ClientHelloMessage
    | CandidateSpeechStartedMessage
    | CandidateSpeechStoppedMessage
    | CandidateTranscriptFinalizedMessage
    | CandidateCodeSnapshotMessage
    | CandidateCodeActivityStartedMessage
    | CandidateCodeActivityIdleMessage
    | RealtimeDisconnectedMessage
    | RealtimeReconnectedMessage
    | DevelopmentAuthorizedPromptRequestMessage
    | ExaminerDecisionPolicyGateRequestMessage
    | PromptDeliveryPermitRequestMessage
    | CounterQDeliveryStartedMessage
    | CounterQDeliveryCompletedMessage
    | CounterQDeliveryInterruptedMessage
    | CandidateEndInterviewMessage
    | SessionDeadlineReachedMessage,
    Field(discriminator="type"),
]

client_control_message_adapter: TypeAdapter[ClientControlMessage] = TypeAdapter(
    ClientControlMessage
)


class ServerHelloMessage(BaseModel):
    type: Literal["server_hello"] = "server_hello"
    protocol_version: Literal["counterq.realtime.control.v1"] = CONTROL_PROTOCOL_VERSION
    interview_session_id: UUID
    current_stage: str
    state_version: int
    last_server_sequence: int
    probe_budget_used: int
    probe_budget_max: int


class DurableEventAckMessage(BaseModel):
    type: Literal["durable_event_ack"] = "durable_event_ack"
    client_event_id: str
    created: bool
    interview_event_id: UUID | None = None
    transcript_segment_id: UUID | None = None
    code_snapshot_id: UUID | None = None
    code_diff_id: UUID | None = None
    code_version: int | None = None
    content_hash: str | None = None
    observation_kind: str | None = None
    observation_trigger_class: str | None = None
    observation_interview_stage: str | None = None
    associated_code_snapshot_id: UUID | None = None
    associated_code_snapshot_version: int | None = None
    prompt_delivery_id: UUID | None = None
    interviewer_prompt_id: UUID | None = None
    server_sequence: int | None = None
    interview_state_version: int


class DevelopmentAuthorizedPromptMessage(BaseModel):
    type: Literal["development_authorized_prompt"] = "development_authorized_prompt"
    client_event_id: str
    interviewer_prompt_id: UUID
    text: str
    origin: Literal["SYSTEM"] = "SYSTEM"
    kind: Literal["INSTRUCTION"] = "INSTRUCTION"
    status: Literal["AUTHORIZED"] = "AUTHORIZED"


class PolicyGateResultMessage(BaseModel):
    type: Literal["policy_gate_result"] = "policy_gate_result"
    client_event_id: str
    examiner_decision_id: UUID
    disposition: str
    decision_status: str
    policy_gate_outcome: str | None
    reason: str
    interviewer_prompt_id: UUID | None = None
    prompt_kind: str | None = None
    probe_strategy: str | None = None
    candidate_safe_text: str | None = None


class PromptDeliveryPermitMessage(BaseModel):
    type: Literal["prompt_delivery_permit"] = "prompt_delivery_permit"
    client_event_id: str
    interviewer_prompt_id: UUID
    status: Literal["PERMITTED"] = "PERMITTED"
    reason: str | None = None
    text: str
    origin: str
    kind: str


class PromptDeliveryPermitResultMessage(BaseModel):
    type: Literal["prompt_delivery_permit_result"] = "prompt_delivery_permit_result"
    client_event_id: str
    interviewer_prompt_id: UUID
    status: Literal["DEFERRED", "EXPIRED", "STALE", "REJECTED"]
    reason: str


class DeliveryAckMessage(BaseModel):
    type: Literal["delivery_ack"] = "delivery_ack"
    client_event_id: str
    interviewer_prompt_id: UUID
    prompt_delivery_id: UUID
    delivery_state: str
    actual_transcript_segment_id: UUID | None = None
    interview_event_id: UUID | None = None
    server_sequence: int | None = None
    interview_state_version: int
    created: bool
    observation_kind: str | None = None
    observation_trigger_class: str | None = None
    observation_interview_stage: str | None = None
    probe_budget_used: int | None = None
    probe_budget_max: int | None = None


class ControlSignalAckMessage(BaseModel):
    type: Literal["control_signal_ack"] = "control_signal_ack"
    client_event_id: str
    floor_state: str
    interrupted_prompt_delivery_id: str | None = None


class ControlErrorMessage(BaseModel):
    type: Literal["control_error"] = "control_error"
    client_event_id: str | None = None
    category: str
    message: str


class SessionTerminalAckMessage(BaseModel):
    type: Literal["session_terminal_ack"] = "session_terminal_ack"
    client_event_id: str
    session_status: Literal["COMPLETED"]
    current_stage: Literal["COMPLETED"]
    state_version: int
    last_server_sequence: int
    completed_at: datetime
    terminal_reason: Literal["USER_ENDED", "TIME_EXPIRED"]
    created: bool
