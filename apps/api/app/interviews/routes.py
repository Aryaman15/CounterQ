from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.provider_factory import (
    ReasoningProviderConfigurationError,
    build_reasoning_provider,
)
from app.auth.dependencies import get_current_user
from app.auth.principal import CurrentUser
from app.config.settings import Settings, get_settings
from app.db.session import get_session, get_sessionmaker
from app.evidence.coordinator import SessionEvidenceEvaluationCoordinator
from app.interviews.assistance import (
    AssistanceRequestCommand,
    AssistanceRequestResult,
    CoachAssistanceWorkflow,
)
from app.interviews.assistance_wording import CoachAssistanceWordingService
from app.interviews.authorization import InterviewOwnershipRepository, OwnedInterviewNotFound
from app.interviews.contracts import (
    CandidateInterviewHistoryResponse,
    CreateInterviewRequest,
    CreateInterviewResponse,
    InterviewBootstrapResponse,
    InterviewHistoryQuery,
    RestoreInterviewRequest,
)
from app.interviews.creation import (
    CandidateProfileRequired,
    SelfServeInterviewCreationService,
    SelfServeInterviewSelectionInvalid,
)
from app.interviews.history import CandidateInterviewHistoryReader
from app.interviews.mode_policy import ModePolicy
from app.interviews.restoration import (
    DevelopmentInterviewNotResumable,
    SessionRestorationService,
)
from app.interviews.runtime import InterviewRuntimeError
from app.problems.service import CuratedProblemError
from app.realtime.control_protocol import (
    RestoredCodeSnapshotMessage,
    RestoredConversationTurnMessage,
    RestoredUnresolvedPromptMessage,
)

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


@router.get("", response_model=CandidateInterviewHistoryResponse)
async def list_interviews(
    query: Annotated[InterviewHistoryQuery, Query()],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    database_session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateInterviewHistoryResponse:
    return await CandidateInterviewHistoryReader(database_session).read(
        user_id=current_user.id,
        state=query.state,
        limit=query.limit,
        offset=query.offset,
    )


@router.post("", response_model=CreateInterviewResponse, status_code=status.HTTP_201_CREATED)
async def create_interview(
    request: CreateInterviewRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    database_session: Annotated[AsyncSession, Depends(get_session)],
) -> CreateInterviewResponse:
    try:
        created = await SelfServeInterviewCreationService(database_session).create(
            user_id=current_user.id,
            problem_version_id=request.problem_version_id,
            template=request.template,
            mode=request.mode,
            language=request.language,
        )
    except CandidateProfileRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "category": "onboarding_required",
                "message": "Complete onboarding before starting an interview",
            },
        ) from exc
    except (CuratedProblemError, SelfServeInterviewSelectionInvalid, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "category": "interview_selection_invalid",
                "message": "The selected interview configuration is unavailable",
            },
        ) from exc

    interview = created.interview_session
    configuration = created.configuration
    return CreateInterviewResponse(
        interview_session_id=interview.id,
        template=created.template,
        mode=cast(Literal["COACH", "SIMULATION"], configuration.mode),
        language=cast(Literal["cpp", "python", "java"], configuration.language),
        configured_duration_seconds=configuration.configured_duration_seconds,
        current_stage=interview.current_stage,
        session_status="ACTIVE",
        state_version=0,
        started_at=interview.started_at,
        deadline_at=interview.deadline_at,
        interview_path=f"/interview/{interview.id}",
    )


@router.post("/{interview_session_id}/restore", response_model=InterviewBootstrapResponse)
async def restore_interview(
    interview_session_id: UUID,
    request: RestoreInterviewRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    database_session: Annotated[AsyncSession, Depends(get_session)],
) -> InterviewBootstrapResponse:
    try:
        await InterviewOwnershipRepository(database_session).get_owned(
            principal_user_id=current_user.id,
            interview_session_id=interview_session_id,
        )
    except OwnedInterviewNotFound as exc:
        raise HTTPException(status_code=404, detail="Interview session was not found") from exc

    try:
        restored = await SessionRestorationService(database_session).restore(
            interview_session_id=interview_session_id,
            client_instance_id=request.client_instance_id,
            reconcile_orphaned_deliveries=True,
        )
        await database_session.commit()
    except DevelopmentInterviewNotResumable as exc:
        await database_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "category": "interview_not_resumable",
                "message": "This interview cannot be restored",
            },
        ) from exc

    interview = restored.interview
    return InterviewBootstrapResponse(
        interview_session_id=interview.id,
        language=cast(Literal["cpp", "python", "java"], interview.configuration.language),
        problem=restored.problem,
        template=restored.template,
        configured_duration_seconds=interview.configuration.configured_duration_seconds,
        mode=cast(Literal["COACH", "SIMULATION"], interview.configuration.mode),
        current_stage=interview.current_stage,
        session_status=interview.status,
        state_version=interview.state_version,
        started_at=interview.started_at,
        deadline_at=interview.deadline_at,
        time_remaining_seconds=restored.time_remaining_seconds,
        time_pressure=restored.time_pressure,
        control_websocket_path=f"/api/realtime/control/{interview.id}",
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


class CandidateAssistanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)


class AssistanceBudgetResponse(BaseModel):
    max_assistance_interventions: int
    assistance_interventions_used: int
    outstanding_assistance_interventions: int
    remaining_assistance_interventions: int
    max_structural_hints: int
    structural_hints_used: int
    outstanding_structural_hints: int
    remaining_structural_hints: int
    max_direct_teaching_interventions: int
    direct_teaching_interventions_used: int
    outstanding_direct_teaching_interventions: int
    remaining_direct_teaching_interventions: int
    max_guided_retries: int
    guided_retries_used: int
    outstanding_guided_retries: int
    remaining_guided_retries: int


class CandidateAssistanceResponse(BaseModel):
    status: Literal["AUTHORIZED", "REFUSED", "ATTEMPT_REQUIRED", "DEFERRED", "DENIED"]
    reason: str
    mode: Literal["COACH", "SIMULATION"]
    mode_policy_version: Literal["mode-policy.v1"]
    request_event_id: UUID
    request_event_watermark: int
    interviewer_prompt_id: UUID | None
    prompt_kind: Literal["CLARIFICATION", "INSTRUCTION"] | None
    assistance_type: (
        Literal[
            "METACOGNITIVE",
            "PROBLEM_NARROWING",
            "CONCEPTUAL_HINT",
            "STRUCTURAL_HINT",
            "DIRECT_TEACHING",
            "DEBUGGING_HINT",
            "CORRECTNESS_FEEDBACK",
        ]
        | None
    )
    hint_level: (
        Literal[
            "METACOGNITIVE",
            "PROBLEM_NARROWING",
            "CONCEPTUAL_HINT",
            "STRUCTURAL_HINT",
            "DIRECT_TEACHING",
        ]
        | None
    )
    target_concept_id: UUID | None
    target_skill_dimension_id: UUID | None
    source_code_snapshot_id: UUID | None
    invites_guided_retry: bool
    budget: AssistanceBudgetResponse


@router.post(
    "/{interview_session_id}/assistance-requests",
    response_model=CandidateAssistanceResponse,
)
async def request_candidate_assistance(
    interview_session_id: UUID,
    request: CandidateAssistanceRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    database_session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CandidateAssistanceResponse:
    try:
        await InterviewOwnershipRepository(database_session).get_owned(
            principal_user_id=current_user.id,
            interview_session_id=interview_session_id,
        )
    except OwnedInterviewNotFound as exc:
        raise HTTPException(status_code=404, detail="Interview session was not found") from exc
    await database_session.rollback()
    sessionmaker = get_sessionmaker()
    evidence_coordinator: SessionEvidenceEvaluationCoordinator | None = None
    wording_service: CoachAssistanceWordingService | None = None
    try:
        provider = build_reasoning_provider(settings)
    except ReasoningProviderConfigurationError:
        # Refusal/no-attempt behavior remains deterministic. Technical help is
        # deferred because candidate-visible assistance has no fallback prose.
        provider = None
    if provider is not None:
        gateway = AIGateway(
            settings=settings,
            sessionmaker=sessionmaker,
            provider=provider,
        )
        evidence_coordinator = SessionEvidenceEvaluationCoordinator(
            sessionmaker=sessionmaker,
            ai_gateway=gateway,
        )
        wording_service = CoachAssistanceWordingService(gateway)
    try:
        result = await CoachAssistanceWorkflow(
            sessionmaker=sessionmaker,
            evidence_coordinator=evidence_coordinator,
            wording_service=wording_service,
        ).request(
            AssistanceRequestCommand(
                interview_session_id=interview_session_id,
                idempotency_key=request.idempotency_key,
            ),
            principal_user_id=current_user.id,
        )
    except OwnedInterviewNotFound as exc:
        raise HTTPException(status_code=404, detail="Interview session was not found") from exc
    except (InterviewRuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"category": "assistance_request_rejected", "message": str(exc)},
        ) from exc
    return _response(result)


def _response(result: AssistanceRequestResult) -> CandidateAssistanceResponse:
    return CandidateAssistanceResponse(
        status=result.status,
        reason=result.reason,
        mode=result.mode,
        mode_policy_version=ModePolicy.policy_version,
        request_event_id=result.request_event_id,
        request_event_watermark=result.request_event_watermark,
        interviewer_prompt_id=result.interviewer_prompt_id,
        prompt_kind=result.prompt_kind,
        assistance_type=result.assistance_type,
        hint_level=result.hint_level,
        target_concept_id=result.target_concept_id,
        target_skill_dimension_id=result.target_skill_dimension_id,
        source_code_snapshot_id=result.source_code_snapshot_id,
        invites_guided_retry=result.invites_guided_retry,
        budget=AssistanceBudgetResponse.model_validate(result.budget, from_attributes=True),
    )
