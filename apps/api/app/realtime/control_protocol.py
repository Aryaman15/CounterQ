from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter

CONTROL_PROTOCOL_VERSION: Literal["counterq.realtime.control.v1"] = (
    "counterq.realtime.control.v1"
)


class RealtimeDevelopmentBootstrapRequest(BaseModel):
    purpose: Literal["interview_demo"] = "interview_demo"


class RealtimeDevelopmentBootstrapResponse(BaseModel):
    interview_session_id: UUID
    current_stage: str
    state_version: int
    deadline_at: datetime
    control_websocket_path: str
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


ClientControlMessage = Annotated[
    ClientHelloMessage
    | CandidateSpeechStartedMessage
    | CandidateSpeechStoppedMessage
    | CandidateTranscriptFinalizedMessage
    | RealtimeDisconnectedMessage
    | RealtimeReconnectedMessage
    | DevelopmentAuthorizedPromptRequestMessage
    | CounterQDeliveryStartedMessage
    | CounterQDeliveryCompletedMessage
    | CounterQDeliveryInterruptedMessage,
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


class DurableEventAckMessage(BaseModel):
    type: Literal["durable_event_ack"] = "durable_event_ack"
    client_event_id: str
    created: bool
    interview_event_id: UUID | None = None
    transcript_segment_id: UUID | None = None
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
