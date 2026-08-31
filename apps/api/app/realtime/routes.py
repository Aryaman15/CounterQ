from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.provider import (
    ProviderReasoningResult,
    ReasoningEffort,
    ReasoningRequest,
)
from app.ai_gateway.providers.openai_reasoning import OpenAIReasoningProvider
from app.config.environment import DEVELOPMENT_SPIKE_ENVS, development_spike_enabled
from app.config.settings import Settings, get_settings
from app.db.session import get_session, get_sessionmaker
from app.examiner.coordinator import (
    LiveExaminerCoordinator,
    observation_is_live_examiner_eligible,
)
from app.interviews.completion import DeadlineNotReached, InterviewCompletionService
from app.interviews.dev_factory import (
    create_curated_development_interview,
    create_development_interview,
)
from app.interviews.floor import ConversationFloor
from app.interviews.prompt_authorization import PromptAuthorizationError
from app.interviews.restoration import (
    RESTORE_PROTOCOL_VERSION,
    DevelopmentInterviewNotResumable,
    SessionRestorationService,
)
from app.interviews.runtime import IdempotencyConflict, InterviewRuntimeError
from app.problems.service import CuratedProblemError
from app.realtime.control_protocol import (
    CandidateCodeActivityIdleMessage,
    CandidateCodeActivityStartedMessage,
    CandidateCodeSnapshotMessage,
    CandidateEndInterviewMessage,
    CandidateSpeechStartedMessage,
    CandidateSpeechStoppedMessage,
    CandidateTranscriptFinalizedMessage,
    ClientControlMessage,
    ClientHelloMessage,
    ControlErrorMessage,
    ControlSignalAckMessage,
    CounterQDeliveryCompletedMessage,
    CounterQDeliveryInterruptedMessage,
    CounterQDeliveryStartedMessage,
    DeliveryAckMessage,
    DevelopmentAuthorizedPromptMessage,
    DevelopmentAuthorizedPromptRequestMessage,
    DurableEventAckMessage,
    ExaminerDecisionPolicyGateRequestMessage,
    PolicyGateResultMessage,
    PromptDeliveryPermitMessage,
    PromptDeliveryPermitRequestMessage,
    PromptDeliveryPermitResultMessage,
    RealtimeDevelopmentBootstrapRequest,
    RealtimeDevelopmentBootstrapResponse,
    RealtimeDisconnectedMessage,
    RealtimeReconnectedMessage,
    RestoredCodeSnapshotMessage,
    RestoredConversationTurnMessage,
    RestoredUnresolvedPromptMessage,
    ServerHelloMessage,
    SessionDeadlineReachedMessage,
    SessionTerminalAckMessage,
    client_control_message_adapter,
)
from app.realtime.control_service import (
    RealtimeControlError,
    RealtimeControlRuntimeState,
    RealtimeControlService,
)
from app.realtime.openai_provider import OpenAIRealtimeVoiceProvider
from app.realtime.provider import RealtimeProviderError, RealtimeVoiceProvider

router = APIRouter(prefix="/api/realtime", tags=["realtime"])
DEVELOPMENT_REALTIME_ENVS = DEVELOPMENT_SPIKE_ENVS
logger = structlog.get_logger(__name__)


class CreateRealtimeSessionRequest(BaseModel):
    purpose: Literal["interview_demo"] = "interview_demo"


class RealtimeTurnDetectionConfig(BaseModel):
    type: Literal["semantic_vad"]
    eagerness: Literal["low"]
    create_response: Literal[False]
    interrupt_response: Literal[True]


class CreateRealtimeSessionResponse(BaseModel):
    provider: Literal["openai"]
    client_secret: str = Field(description="Short-lived browser credential for OpenAI WebRTC.")
    webrtc_url: str
    model: str
    voice: str
    transcription_model: str
    expires_at: datetime | None
    expires_after_seconds: int
    turn_detection: RealtimeTurnDetectionConfig


def realtime_credential_minting_allowed(settings: Settings) -> bool:
    return development_spike_enabled(settings)


def build_realtime_voice_provider(settings: Settings) -> RealtimeVoiceProvider:
    if settings.realtime_provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "category": "configuration_error",
                "message": "Configured realtime provider is unsupported",
            },
        )
    return OpenAIRealtimeVoiceProvider(settings)


def get_realtime_voice_provider_builder() -> Callable[[Settings], RealtimeVoiceProvider]:
    return build_realtime_voice_provider


@router.post("/session", response_model=CreateRealtimeSessionResponse)
async def create_realtime_session(
    _request: CreateRealtimeSessionRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_builder: Annotated[
        Callable[[Settings], RealtimeVoiceProvider],
        Depends(get_realtime_voice_provider_builder),
    ],
) -> CreateRealtimeSessionResponse:
    if not realtime_credential_minting_allowed(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Realtime credential minting is enabled only for local development",
            },
        )

    try:
        provider = provider_builder(settings)
        session = await provider.create_browser_session()
    except RealtimeProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"category": exc.category, "message": exc.safe_message},
        ) from exc

    return CreateRealtimeSessionResponse(
        provider="openai",
        client_secret=session.client_secret,
        webrtc_url=session.webrtc_url,
        model=session.model,
        voice=session.voice,
        transcription_model=session.transcription_model,
        expires_at=session.expires_at,
        expires_after_seconds=session.expires_after_seconds,
        turn_detection=RealtimeTurnDetectionConfig(
            type="semantic_vad",
            eagerness="low",
            create_response=False,
            interrupt_response=True,
        ),
    )


@router.post(
    "/development-interview",
    response_model=RealtimeDevelopmentBootstrapResponse,
)
async def create_realtime_development_interview(
    request: RealtimeDevelopmentBootstrapRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RealtimeDevelopmentBootstrapResponse:
    if not realtime_credential_minting_allowed(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "category": "development_only",
                "message": "Realtime development interview bootstrap is local-development only",
            },
        )

    restoration = "RESTORED" if request.interview_session_id is not None else "CREATED"
    try:
        async with session.begin():
            if request.interview_session_id is None:
                if request.purpose == "stage1_fixture":
                    dev = await create_development_interview(
                        session,
                        initial_stage="IMPLEMENTATION",
                        language=request.language or "cpp",
                    )
                else:
                    assert request.problem_version_id is not None
                    assert request.language is not None
                    dev = await create_curated_development_interview(
                        session,
                        problem_version_id=request.problem_version_id,
                        initial_stage="IMPLEMENTATION",
                        language=request.language,
                    )
                interview_session_id = dev.interview_session.id
            else:
                interview_session_id = request.interview_session_id
            restored = await SessionRestorationService(session).restore(
                interview_session_id=interview_session_id,
                client_instance_id=request.client_instance_id,
                reconcile_orphaned_deliveries=request.interview_session_id is not None,
            )
    except DevelopmentInterviewNotResumable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "category": "development_session_not_resumable",
                "message": "The requested development interview is not available to restore",
            },
        ) from exc
    except (CuratedProblemError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "category": "curated_interview_selection_invalid",
                "message": str(exc),
            },
        ) from exc

    interview = restored.interview
    return RealtimeDevelopmentBootstrapResponse(
        interview_session_id=interview.id,
        language=cast(Literal["cpp", "python", "java"], interview.configuration.language),
        problem=restored.problem,
        template=restored.template,
        configured_duration_seconds=interview.configuration.configured_duration_seconds,
        current_stage=interview.current_stage,
        session_status=interview.status,
        state_version=interview.state_version,
        deadline_at=interview.deadline_at,
        time_remaining_seconds=restored.time_remaining_seconds,
        time_pressure=restored.time_pressure,
        control_websocket_path=f"/api/realtime/control/{interview.id}",
        restoration=restoration,
        restore_protocol_version=RESTORE_PROTOCOL_VERSION,
        started_at=interview.started_at,
        completed_at=interview.completed_at,
        terminal_reason=restored.terminal_reason,
        latest_code_snapshot=(
            RestoredCodeSnapshotMessage(
                id=restored.code_snapshot.id,
                version_number=restored.code_snapshot.version_number,
                language=restored.code_snapshot.language,
                source_code=restored.code_snapshot.source_code,
                content_hash=restored.code_snapshot.content_hash,
            )
            if restored.code_snapshot is not None
            else None
        ),
        recent_conversation=[
            RestoredConversationTurnMessage(
                id=turn.id,
                speaker=turn.speaker,
                text=turn.text,
                sequence=turn.sequence,
                occurred_at=turn.occurred_at,
                delivery_state=turn.delivery_state,
            )
            for turn in restored.conversation
        ],
        unresolved_prompt=(
            RestoredUnresolvedPromptMessage(
                id=restored.unresolved_prompt.id,
                kind=restored.unresolved_prompt.kind,
                status="AUTHORIZED",
            )
            if restored.unresolved_prompt is not None
            else None
        ),
        highest_client_sequence=restored.highest_client_sequence,
        last_server_sequence=interview.last_server_sequence,
    )


@router.websocket("/control/{interview_session_id}")
async def realtime_control_websocket(
    websocket: WebSocket,
    interview_session_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if not realtime_credential_minting_allowed(settings):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    sessionmaker = get_sessionmaker()
    floor = ConversationFloor()
    runtime_state = RealtimeControlRuntimeState()

    try:
        async with sessionmaker() as session:
            service = RealtimeControlService(
                session,
                authorized_prompt_delivery_window_seconds=(
                    settings.authorized_prompt_delivery_window_seconds
                ),
            )
            interview = await service.ensure_session_exists(interview_session_id)
            budget = await service.session_budget(interview_session_id)
            await websocket.send_json(
                ServerHelloMessage(
                    interview_session_id=interview.id,
                    current_stage=interview.current_stage,
                    state_version=interview.state_version,
                    last_server_sequence=interview.last_server_sequence,
                    probe_budget_used=budget.probes_used,
                    probe_budget_max=budget.max_probes,
                ).model_dump(mode="json"),
            )
    except RealtimeControlError:
        await websocket.send_json(
            ControlErrorMessage(
                category="session_not_found",
                message="Interview session is not available for realtime control",
            ).model_dump(mode="json"),
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    while True:
        try:
            raw_message = await websocket.receive_json()
        except WebSocketDisconnect:
            return

        try:
            message = client_control_message_adapter.validate_python(raw_message)
        except ValidationError:
            await websocket.send_json(
                ControlErrorMessage(
                    category="invalid_message",
                    message="Realtime control message failed validation",
                ).model_dump(mode="json"),
            )
            continue

        try:
            if isinstance(message, ClientHelloMessage):
                await websocket.send_json(
                    ControlSignalAckMessage(
                        client_event_id=message.client_event_id,
                        floor_state=floor.state,
                        interrupted_prompt_delivery_id=floor.interrupted_prompt_delivery_id,
                    ).model_dump(mode="json"),
                )
                continue
            if isinstance(message, CandidateSpeechStartedMessage):
                floor = floor.candidate_speech_started()
                runtime_state = RealtimeControlRuntimeState(
                    candidate_speaking=True,
                    candidate_code_active=runtime_state.candidate_code_active,
                )
                await websocket.send_json(
                    ControlSignalAckMessage(
                        client_event_id=message.client_event_id,
                        floor_state=floor.state,
                        interrupted_prompt_delivery_id=floor.interrupted_prompt_delivery_id,
                    ).model_dump(mode="json"),
                )
                continue
            if isinstance(message, CandidateSpeechStoppedMessage):
                floor = floor.candidate_paused()
                runtime_state = RealtimeControlRuntimeState(
                    candidate_speaking=False,
                    candidate_code_active=runtime_state.candidate_code_active,
                )
                await websocket.send_json(
                    ControlSignalAckMessage(
                        client_event_id=message.client_event_id,
                        floor_state=floor.state,
                        interrupted_prompt_delivery_id=floor.interrupted_prompt_delivery_id,
                    ).model_dump(mode="json"),
                )
                continue
            if isinstance(message, CandidateCodeActivityStartedMessage):
                runtime_state = RealtimeControlRuntimeState(
                    candidate_speaking=runtime_state.candidate_speaking,
                    candidate_code_active=True,
                )
                await websocket.send_json(
                    ControlSignalAckMessage(
                        client_event_id=message.client_event_id,
                        floor_state=floor.state,
                        interrupted_prompt_delivery_id=floor.interrupted_prompt_delivery_id,
                    ).model_dump(mode="json"),
                )
                continue
            if isinstance(message, CandidateCodeActivityIdleMessage):
                runtime_state = RealtimeControlRuntimeState(
                    candidate_speaking=runtime_state.candidate_speaking,
                    candidate_code_active=False,
                )
                await websocket.send_json(
                    ControlSignalAckMessage(
                        client_event_id=message.client_event_id,
                        floor_state=floor.state,
                        interrupted_prompt_delivery_id=floor.interrupted_prompt_delivery_id,
                    ).model_dump(mode="json"),
                )
                continue

            async with sessionmaker() as session:
                async with session.begin():
                    service = RealtimeControlService(
                        session,
                        authorized_prompt_delivery_window_seconds=(
                            settings.authorized_prompt_delivery_window_seconds
                        ),
                    )
                    service.floor = floor
                    response = await _handle_durable_control_message(
                        service=service,
                        interview_session_id=interview_session_id,
                        message=message,
                        runtime_state=runtime_state,
                    )
                    floor = service.floor
                await websocket.send_json(response.model_dump(mode="json"))
                await _notify_live_examiner_if_eligible(
                    settings=settings,
                    response=response,
                    interview_session_id=interview_session_id,
                )
        except (
            DeadlineNotReached,
            RealtimeControlError,
            InterviewRuntimeError,
            PromptAuthorizationError,
        ) as exc:
            await websocket.send_json(
                ControlErrorMessage(
                    client_event_id=getattr(message, "client_event_id", None),
                    category="control_rejected",
                    message=safe_control_error_message(exc),
                ).model_dump(mode="json"),
            )
        except SQLAlchemyError as exc:
            # The session transaction has already rolled back before this handler
            # runs.  Each durable message receives a fresh session on the next turn.
            logger.exception(
                "realtime_durable_control_persistence_failed",
                message_type=getattr(message, "type", type(message).__name__),
                interview_session_id=str(interview_session_id),
                exception_class=type(exc).__name__,
            )
            await websocket.send_json(
                ControlErrorMessage(
                    client_event_id=getattr(message, "client_event_id", None),
                    category="control_unavailable",
                    message="CounterQ control is temporarily unavailable. Please try again.",
                ).model_dump(mode="json"),
            )


async def _handle_durable_control_message(
    *,
    service: RealtimeControlService,
    interview_session_id: UUID,
    message: ClientControlMessage,
    runtime_state: RealtimeControlRuntimeState,
) -> (
    DurableEventAckMessage
    | DevelopmentAuthorizedPromptMessage
    | PolicyGateResultMessage
    | PromptDeliveryPermitMessage
    | PromptDeliveryPermitResultMessage
    | DeliveryAckMessage
    | SessionTerminalAckMessage
    | ControlSignalAckMessage
):
    if not isinstance(message, CandidateEndInterviewMessage | SessionDeadlineReachedMessage):
        reconciled = await InterviewCompletionService(service._session).reconcile_expired(
            interview_session_id
        )
        if reconciled is not None:
            interview = reconciled.interview
            return SessionTerminalAckMessage(
                client_event_id=message.client_event_id,
                session_status="COMPLETED",
                current_stage="COMPLETED",
                state_version=interview.state_version,
                last_server_sequence=interview.last_server_sequence,
                completed_at=interview.completed_at or interview.deadline_at,
                terminal_reason="TIME_EXPIRED",
                created=True,
            )
    if isinstance(message, CandidateEndInterviewMessage | SessionDeadlineReachedMessage):
        completion = await InterviewCompletionService(service._session).complete(
            session_id=interview_session_id,
            reason=(
                "USER_ENDED"
                if isinstance(message, CandidateEndInterviewMessage)
                else "TIME_EXPIRED"
            ),
            expected_state_version=message.expected_state_version,
            idempotency_key=message.idempotency_key,
        )
        interview = completion.interview
        return SessionTerminalAckMessage(
            client_event_id=message.client_event_id,
            session_status="COMPLETED",
            current_stage="COMPLETED",
            state_version=interview.state_version,
            last_server_sequence=interview.last_server_sequence,
            completed_at=interview.completed_at or interview.deadline_at,
            terminal_reason=completion.terminal_reason,
            created=completion.created,
        )
    if isinstance(message, CandidateTranscriptFinalizedMessage):
        transcript_result = await service.persist_candidate_transcript(
            session_id=interview_session_id,
            message=message,
        )
        observation = transcript_result.observation
        return DurableEventAckMessage(
            client_event_id=message.client_event_id,
            created=transcript_result.created,
            interview_event_id=transcript_result.event_id,
            transcript_segment_id=transcript_result.transcript_segment_id,
            server_sequence=transcript_result.server_sequence,
            interview_state_version=transcript_result.interview_state_version,
            observation_kind=observation.kind if observation else None,
            observation_trigger_class=observation.trigger_class if observation else None,
            observation_interview_stage=observation.interview_stage if observation else None,
            associated_code_snapshot_id=(
                observation.associated_code_snapshot_id if observation else None
            ),
            associated_code_snapshot_version=(
                observation.associated_code_snapshot_version if observation else None
            ),
        )
    if isinstance(message, CandidateCodeSnapshotMessage):
        code_result = await service.persist_candidate_code_snapshot(
            session_id=interview_session_id,
            message=message,
        )
        observation = code_result.observation
        return DurableEventAckMessage(
            client_event_id=message.client_event_id,
            created=code_result.created,
            interview_event_id=code_result.event_id,
            code_snapshot_id=code_result.snapshot_id,
            code_diff_id=code_result.diff_id,
            code_version=code_result.version_number,
            content_hash=code_result.content_hash,
            server_sequence=code_result.server_sequence,
            interview_state_version=code_result.interview_state_version,
            observation_kind=observation.kind if observation else None,
            observation_trigger_class=observation.trigger_class if observation else None,
            observation_interview_stage=observation.interview_stage if observation else None,
        )
    if isinstance(message, RealtimeDisconnectedMessage | RealtimeReconnectedMessage):
        connectivity_result = await service.persist_realtime_connectivity_event(
            session_id=interview_session_id,
            message=message,
        )
        return DurableEventAckMessage(
            client_event_id=message.client_event_id,
            created=connectivity_result.created,
            interview_event_id=connectivity_result.event_id,
            server_sequence=connectivity_result.server_sequence,
            interview_state_version=connectivity_result.interview_state_version,
        )
    if isinstance(message, DevelopmentAuthorizedPromptRequestMessage):
        prompt_result = await service.create_development_authorized_prompt(
            session_id=interview_session_id,
        )
        return DevelopmentAuthorizedPromptMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=prompt_result.prompt_id,
            text=prompt_result.text,
        )
    if isinstance(message, ExaminerDecisionPolicyGateRequestMessage):
        gate_result = await service.evaluate_examiner_decision(
            session_id=interview_session_id,
            decision_id=message.examiner_decision_id,
            runtime_state=runtime_state,
        )
        return PolicyGateResultMessage(
            client_event_id=message.client_event_id,
            examiner_decision_id=gate_result.decision_id,
            disposition=gate_result.disposition,
            decision_status=gate_result.decision_status,
            policy_gate_outcome=gate_result.policy_gate_outcome,
            reason=gate_result.reason,
            interviewer_prompt_id=gate_result.prompt_id,
            prompt_kind=gate_result.prompt_kind,
            probe_strategy=gate_result.probe_strategy,
            candidate_safe_text=gate_result.candidate_safe_text,
        )
    if isinstance(message, PromptDeliveryPermitRequestMessage):
        permit = await service.permit_prompt_delivery(
            session_id=interview_session_id,
            prompt_id=message.interviewer_prompt_id,
            runtime_state=runtime_state,
        )
        if permit.status != "PERMITTED":
            return PromptDeliveryPermitResultMessage(
                client_event_id=message.client_event_id,
                interviewer_prompt_id=permit.prompt_id,
                status=permit.status,  # type: ignore[arg-type]
                reason=permit.reason,
            )
        return PromptDeliveryPermitMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=permit.prompt_id,
            reason=permit.reason,
            text=permit.text or "",
            origin=permit.origin or "",
            kind=permit.kind or "",
        )
    if isinstance(message, CounterQDeliveryStartedMessage):
        delivery_result = await service.start_delivery(
            session_id=interview_session_id,
            message=message,
        )
        return DeliveryAckMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=delivery_result.prompt_id,
            prompt_delivery_id=delivery_result.delivery_id,
            delivery_state=delivery_result.delivery_state,
            interview_state_version=delivery_result.interview_state_version,
            created=delivery_result.created,
            probe_budget_used=(await service.session_budget(interview_session_id)).probes_used,
            probe_budget_max=(await service.session_budget(interview_session_id)).max_probes,
        )
    if isinstance(message, CounterQDeliveryCompletedMessage):
        delivery_result = await service.complete_delivery(
            session_id=interview_session_id,
            message=message,
        )
        observation = delivery_result.observation
        return DeliveryAckMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=delivery_result.prompt_id,
            prompt_delivery_id=delivery_result.delivery_id,
            delivery_state=delivery_result.delivery_state,
            actual_transcript_segment_id=delivery_result.transcript_segment_id,
            interview_event_id=delivery_result.event_id,
            server_sequence=delivery_result.server_sequence,
            interview_state_version=delivery_result.interview_state_version,
            created=delivery_result.created,
            observation_kind=observation.kind if observation else None,
            observation_trigger_class=observation.trigger_class if observation else None,
            observation_interview_stage=observation.interview_stage if observation else None,
            probe_budget_used=(await service.session_budget(interview_session_id)).probes_used,
            probe_budget_max=(await service.session_budget(interview_session_id)).max_probes,
        )
    if isinstance(message, CounterQDeliveryInterruptedMessage):
        delivery_result = await service.interrupt_delivery(
            session_id=interview_session_id,
            message=message,
        )
        observation = delivery_result.observation
        return DeliveryAckMessage(
            client_event_id=message.client_event_id,
            interviewer_prompt_id=delivery_result.prompt_id,
            prompt_delivery_id=delivery_result.delivery_id,
            delivery_state=delivery_result.delivery_state,
            interview_event_id=delivery_result.event_id,
            server_sequence=delivery_result.server_sequence,
            interview_state_version=delivery_result.interview_state_version,
            created=delivery_result.created,
            observation_kind=observation.kind if observation else None,
            observation_trigger_class=observation.trigger_class if observation else None,
            observation_interview_stage=observation.interview_stage if observation else None,
            probe_budget_used=(await service.session_budget(interview_session_id)).probes_used,
            probe_budget_max=(await service.session_budget(interview_session_id)).max_probes,
        )
    raise RealtimeControlError("Unsupported realtime control message")


async def _notify_live_examiner_if_eligible(
    *,
    settings: Settings,
    response: object,
    interview_session_id: UUID,
) -> None:
    observation_kind = getattr(response, "observation_kind", None)
    source_event_id = getattr(response, "interview_event_id", None)
    if not observation_is_live_examiner_eligible(observation_kind) or source_event_id is None:
        return
    provider = (
        OpenAIReasoningProvider(settings)
        if settings.live_examiner_autostart
        else _NoopReasoningProvider()
    )
    coordinator = LiveExaminerCoordinator(
        settings=settings,
        sessionmaker=get_sessionmaker(),
        provider=provider,
    )
    await coordinator.notify_new_observation(
        interview_session_id=interview_session_id,
        source_event_id=source_event_id,
    )


class _NoopReasoningProvider:
    provider_name = "noop"

    async def reason_structured(
        self,
        request: ReasoningRequest,
        *,
        model: str,
        reasoning_effort: ReasoningEffort,
    ) -> ProviderReasoningResult:
        raise RuntimeError("Noop reasoning provider should not be called when autostart is off")


def safe_control_error_message(exc: Exception) -> str:
    if isinstance(exc, DeadlineNotReached):
        return "The interview deadline has not been reached."
    if isinstance(exc, IdempotencyConflict):
        return "Realtime control message conflicts with previously accepted truth"
    if isinstance(exc, PromptAuthorizationError):
        return "Prompt authorization policy rejected the control request"
    if isinstance(exc, RealtimeControlError):
        return "Realtime control message could not be accepted"
    return "Realtime control operation failed"
