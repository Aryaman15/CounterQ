# ruff: noqa: E501
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.environment import development_spike_enabled
from app.config.settings import Settings, get_settings
from app.db.session import get_session
from app.execution.models import ExecutionRun, TestResult
from app.execution.provider import ExecutorProvider, ExecutorProviderError
from app.execution.sandbox_provider import LocalSandboxExecutorProvider
from app.execution.service import ExecutionService, RunCommand
from app.interviews.runtime import InterviewRuntimeError

router = APIRouter(prefix="/api/execution", tags=["execution"])


class DevelopmentRunRequest(BaseModel):
    interview_session_id: UUID
    source_code: str = Field(min_length=1, max_length=200_000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    client_event_id: str = Field(min_length=1, max_length=128)
    client_instance_id: str = Field(min_length=1, max_length=128)
    client_sequence: int = Field(ge=1)


class VisibleCaseResponse(BaseModel):
    identifier: str
    input_json: dict[str, object]
    expected_output: str | None
    actual_output: str | None
    status: str
    duration_ms: int | None
    failure_classification: str | None


class DevelopmentRunResponse(BaseModel):
    execution_run_id: UUID
    code_snapshot_id: UUID
    code_snapshot_version: int
    status: str
    stdout: str
    stderr: str
    compiler_output: str
    exit_code: int | None
    timed_out: bool
    output_truncated: bool
    duration_ms: int | None
    cases: list[VisibleCaseResponse]


def build_executor_provider(settings: Settings) -> ExecutorProvider:
    if settings.execution_provider != "local_sandbox":
        raise ExecutorProviderError()
    return LocalSandboxExecutorProvider(settings.execution_sandbox_url)


def get_executor_provider_builder() -> Callable[[Settings], ExecutorProvider]:
    return build_executor_provider


@router.get("/health")
async def execution_health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    return {"status": "READY" if settings.execution_provider == "local_sandbox" else "UNAVAILABLE"}


@router.post("/development-runs", response_model=DevelopmentRunResponse)
async def development_run(
    request: DevelopmentRunRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    provider_builder: Annotated[
        Callable[[Settings], ExecutorProvider], Depends(get_executor_provider_builder)
    ],
) -> DevelopmentRunResponse:
    if not development_spike_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Development execution only"
        )
    provider = provider_builder(settings)
    service = ExecutionService(
        session,
        provider,
        compile_timeout_seconds=settings.execution_compile_timeout_seconds,
        run_timeout_seconds=settings.execution_run_timeout_seconds,
        memory_limit_mb=settings.execution_memory_limit_mb,
        output_limit_bytes=settings.execution_output_limit_bytes,
    )
    command = RunCommand(
        session_id=request.interview_session_id,
        source_code=request.source_code,
        idempotency_key=request.idempotency_key,
        client_event_id=request.client_event_id,
        client_instance_id=request.client_instance_id,
        client_sequence=request.client_sequence,
    )
    try:
        async with session.begin():
            run, provider_request, created = await service.begin(command)
        if created:
            try:
                outcome = await service.execute(provider_request)
            except ExecutorProviderError:
                from app.execution.provider import ExecutionOutcome

                outcome = ExecutionOutcome(status="PROVIDER_ERROR", provider_run_id=None)
            async with session.begin():
                run = await service.complete(run.id, outcome)
        hydrated = await session.scalar(
            select(ExecutionRun)
            .options(
                selectinload(ExecutionRun.code_snapshot), selectinload(ExecutionRun.test_results)
            )
            .where(ExecutionRun.id == run.id)
        )
        if hydrated is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        run = hydrated
    except InterviewRuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Interview is not active"
        ) from exc
    return _response(run, list(run.test_results))


def _response(run: ExecutionRun, results: list[TestResult]) -> DevelopmentRunResponse:
    return DevelopmentRunResponse(
        execution_run_id=run.id,
        code_snapshot_id=run.code_snapshot_id,
        code_snapshot_version=run.code_snapshot.version_number,
        status=run.status,
        stdout=run.stdout,
        stderr=run.stderr,
        compiler_output=run.compiler_output,
        exit_code=run.exit_code,
        timed_out=run.timed_out,
        output_truncated=run.output_truncated,
        duration_ms=run.duration_ms,
        cases=[
            VisibleCaseResponse(
                identifier=result.test_identifier,
                input_json=result.input_json,
                expected_output=result.expected_output,
                actual_output=result.actual_output,
                status=result.status,
                duration_ms=result.duration_ms,
                failure_classification=result.failure_classification,
            )
            for result in results
        ],
    )
