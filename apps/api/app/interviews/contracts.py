from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.interviews.creation import SelfServeInterviewTemplate
from app.problems.contracts import CandidateLanguage, CandidateProblemDetail
from app.realtime.control_protocol import (
    CONTROL_PROTOCOL_VERSION,
    RestoredCodeSnapshotMessage,
    RestoredConversationTurnMessage,
    RestoredUnresolvedPromptMessage,
)


class CreateInterviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_version_id: UUID
    template: SelfServeInterviewTemplate
    mode: Literal["COACH", "SIMULATION"]
    language: CandidateLanguage


class CreateInterviewResponse(BaseModel):
    interview_session_id: UUID
    template: SelfServeInterviewTemplate
    mode: Literal["COACH", "SIMULATION"]
    language: CandidateLanguage
    configured_duration_seconds: int
    current_stage: str
    session_status: Literal["ACTIVE"]
    state_version: Literal[0]
    started_at: datetime
    deadline_at: datetime
    interview_path: str


class InterviewHistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["all", "in_progress", "completed"] = "all"
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class CandidateInterviewHistoryItem(BaseModel):
    interview_session_id: UUID
    problem_title: str
    template: str
    mode: Literal["COACH", "SIMULATION"]
    language: CandidateLanguage
    candidate_level: str
    status: str
    display_status: Literal["IN_PROGRESS", "COMPLETED", "ENDED"]
    started_at: datetime
    completed_at: datetime | None
    deadline_at: datetime
    configured_duration_seconds: int
    can_resume: bool
    interview_path: str
    report_path: str
    countermap_path: str


class CandidateInterviewHistoryResponse(BaseModel):
    items: list[CandidateInterviewHistoryItem]
    limit: int
    offset: int
    has_more: bool


class DeleteInterviewResponse(BaseModel):
    interview_session_id: UUID
    status: Literal["DELETION_PENDING"]
    deletion_request_id: UUID


class RestoreInterviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_instance_id: str = Field(min_length=1, max_length=128)


class InterviewBootstrapResponse(BaseModel):
    interview_session_id: UUID
    language: CandidateLanguage
    problem: CandidateProblemDetail
    template: str
    configured_duration_seconds: int
    mode: Literal["COACH", "SIMULATION"]
    current_stage: str
    session_status: str
    state_version: int
    started_at: datetime
    deadline_at: datetime
    time_remaining_seconds: int
    time_pressure: str
    control_websocket_path: str
    restoration: Literal["RESTORED"] = "RESTORED"
    restore_protocol_version: Literal["session.restore.v1"] = "session.restore.v1"
    completed_at: datetime | None = None
    terminal_reason: Literal["USER_ENDED", "TIME_EXPIRED"] | None = None
    latest_code_snapshot: RestoredCodeSnapshotMessage | None = None
    recent_conversation: list[RestoredConversationTurnMessage]
    unresolved_prompt: RestoredUnresolvedPromptMessage | None = None
    highest_client_sequence: int
    last_server_sequence: int
    protocol_version: Literal["counterq.realtime.control.v1"] = CONTROL_PROTOCOL_VERSION
