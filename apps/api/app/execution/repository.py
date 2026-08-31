from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import ExecutionRun, TestResult


class ExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run_for_idempotency(
        self, session_id: UUID, idempotency_key: str
    ) -> ExecutionRun | None:
        result = await self._session.scalar(
            select(ExecutionRun)
            .where(ExecutionRun.interview_session_id == session_id)
            .where(ExecutionRun.idempotency_key == idempotency_key)
        )
        return cast(ExecutionRun | None, result)

    async def add_run(
        self,
        *,
        session_id: UUID,
        run_event_id: UUID,
        code_snapshot_id: UUID,
        problem_version_id: UUID,
        language: str,
        started_at: datetime,
        execution_provider: str,
        idempotency_key: str,
    ) -> ExecutionRun:
        run = ExecutionRun(
            interview_session_id=session_id,
            run_event_id=run_event_id,
            code_snapshot_id=code_snapshot_id,
            problem_version_id=problem_version_id,
            language=language,
            status="RUNNING",
            started_at=started_at,
            execution_provider=execution_provider,
            schema_version="execution.run.v1",
            idempotency_key=idempotency_key,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def add_result(
        self,
        *,
        run_id: UUID,
        identifier: str,
        input_json: dict[str, object],
        expected_output: str | None,
        actual_output: str | None,
        status: str,
        duration_ms: int | None,
        failure_classification: str | None,
    ) -> TestResult:
        result = TestResult(
            execution_run_id=run_id,
            test_identifier=identifier,
            is_visible=True,
            input_json=input_json,
            expected_output=expected_output,
            actual_output=actual_output,
            status=status,
            duration_ms=duration_ms,
            failure_classification=failure_classification,
        )
        self._session.add(result)
        await self._session.flush()
        return result
