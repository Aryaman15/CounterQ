from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.provider import ReasoningProvider
from app.ai_gateway.routes import get_reasoning_provider_builder
from app.auth.dependencies import get_current_user
from app.auth.principal import CurrentUser
from app.config.settings import Settings, get_settings
from app.db.session import get_sessionmaker
from app.execution.provider import ExecutorProvider
from app.execution.routes import get_executor_provider_builder
from app.problems.contracts import (
    CreateCustomProblemPreparationRequest,
    CustomProblemPreparationResponse,
    custom_preparation_response,
)
from app.problems.custom import (
    CustomPreparationIdempotencyConflict,
    CustomPreparationInProgress,
    CustomPreparationNotFound,
    CustomProblemPreparationService,
)

router = APIRouter(
    prefix="/api/problems/custom/preparations", tags=["custom-problems"]
)


def get_custom_problem_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return get_sessionmaker()


@router.post(
    "", response_model=CustomProblemPreparationResponse, status_code=status.HTTP_201_CREATED
)
async def create_custom_problem_preparation(
    request: CreateCustomProblemPreparationRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    maker: Annotated[async_sessionmaker[AsyncSession], Depends(get_custom_problem_sessionmaker)],
) -> CustomProblemPreparationResponse:
    try:
        view = await CustomProblemPreparationService(sessionmaker=maker).create(
            user_id=current_user.id,
            problem_text=request.problem_text,
            idempotency_key=request.idempotency_key,
        )
    except CustomPreparationIdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "category": "custom_problem_idempotency_conflict",
                "message": "That preparation key already represents different text.",
            },
        ) from exc
    return custom_preparation_response(view)


@router.post("/{preparation_id}/prepare", response_model=CustomProblemPreparationResponse)
async def prepare_custom_problem(
    preparation_id: UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    reasoning_provider_builder: Annotated[
        Callable[[Settings], ReasoningProvider], Depends(get_reasoning_provider_builder)
    ],
    executor_provider_builder: Annotated[
        Callable[[Settings], ExecutorProvider], Depends(get_executor_provider_builder)
    ],
    maker: Annotated[async_sessionmaker[AsyncSession], Depends(get_custom_problem_sessionmaker)],
) -> CustomProblemPreparationResponse:
    service = CustomProblemPreparationService(
        sessionmaker=maker,
        gateway=AIGateway(
            settings=settings,
            sessionmaker=maker,
            provider=reasoning_provider_builder(settings),
        ),
        executor=executor_provider_builder(settings),
        compile_timeout_seconds=settings.execution_compile_timeout_seconds,
        run_timeout_seconds=settings.execution_run_timeout_seconds,
        memory_limit_mb=settings.execution_memory_limit_mb,
        output_limit_bytes=settings.execution_output_limit_bytes,
    )
    try:
        return custom_preparation_response(
            await service.prepare(user_id=current_user.id, preparation_id=preparation_id)
        )
    except CustomPreparationNotFound as exc:
        raise HTTPException(
            status_code=404, detail="Custom problem preparation was not found"
        ) from exc
    except CustomPreparationInProgress as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "category": "custom_problem_preparation_in_progress",
                "message": "This problem is already being prepared.",
            },
        ) from exc


@router.get("/{preparation_id}", response_model=CustomProblemPreparationResponse)
async def get_custom_problem_preparation(
    preparation_id: UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    maker: Annotated[async_sessionmaker[AsyncSession], Depends(get_custom_problem_sessionmaker)],
) -> CustomProblemPreparationResponse:
    try:
        view = await CustomProblemPreparationService(sessionmaker=maker).get_owned(
            user_id=current_user.id, preparation_id=preparation_id
        )
    except CustomPreparationNotFound as exc:
        raise HTTPException(
            status_code=404, detail="Custom problem preparation was not found"
        ) from exc
    return custom_preparation_response(view)
