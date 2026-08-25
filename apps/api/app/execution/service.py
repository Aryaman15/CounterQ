"""Application service: persist intent, call isolated provider, persist bounded facts."""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.harness import cpp_harness_for_problem
from app.execution.models import ExecutionRun
from app.execution.provider import ExecutionOutcome, ExecutionRequest, ExecutorProvider
from app.execution.repository import ExecutionRepository
from app.interviews.models import InterviewSession
from app.interviews.runtime import AcceptEventCommand, InterviewRuntime
from app.observation.models import CodeSnapshot
from app.realtime.control_protocol import CandidateCodeSnapshotMessage
from app.realtime.control_service import RealtimeControlService


@dataclass(frozen=True)
class RunCommand:
    session_id: UUID
    source_code: str
    idempotency_key: str
    client_event_id: str
    client_instance_id: str
    client_sequence: int


class ExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        provider: ExecutorProvider,
        *,
        clock: Callable[[], datetime] | None = None,
        compile_timeout_seconds: int = 8,
        run_timeout_seconds: int = 2,
        memory_limit_mb: int = 192,
        output_limit_bytes: int = 65536,
    ) -> None:
        self._session = session
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._compile_timeout_seconds = compile_timeout_seconds
        self._run_timeout_seconds = run_timeout_seconds
        self._memory_limit_mb = memory_limit_mb
        self._output_limit_bytes = output_limit_bytes

    async def begin(self, command: RunCommand) -> tuple[ExecutionRun, ExecutionRequest, bool]:
        repository = ExecutionRepository(self._session)
        existing = await repository.run_for_idempotency(command.session_id, command.idempotency_key)
        if existing is not None:
            return existing, await self._request_for_run(existing), False
        interview = await InterviewRuntime(
            self._session, clock=self._clock
        ).ensure_activity_allowed(command.session_id)
        snapshot = await self._canonical_snapshot(command)
        run_event = await InterviewRuntime(self._session, clock=self._clock).accept_event(
            AcceptEventCommand(
                session_id=command.session_id,
                event_type="RUN_CLICKED",
                source="NATIVE_RUNNER",
                occurred_at=self._clock(),
                idempotency_key=f"run-event:{command.idempotency_key}",
                payload={
                    "code_snapshot_id": str(snapshot.id),
                    "code_version": snapshot.version_number,
                },
                provenance={"run_idempotency_key": command.idempotency_key},
                schema_version="execution.run-clicked.v1",
                client_instance_id=command.client_instance_id,
                client_sequence=command.client_sequence,
                code_snapshot_id=snapshot.id,
            )
        )
        run = await repository.add_run(
            session_id=command.session_id,
            run_event_id=run_event.event.id,
            code_snapshot_id=snapshot.id,
            problem_version_id=interview.problem_version_id,
            language=snapshot.language,
            started_at=self._clock(),
            execution_provider=self._provider.provider_name,
            idempotency_key=command.idempotency_key,
        )
        return run, await self._request_for_run(run), True

    async def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        return await self._provider.execute(request)

    async def complete(self, run_id: UUID, outcome: ExecutionOutcome) -> ExecutionRun:
        run = await self._session.get(ExecutionRun, run_id)
        if run is None:
            raise ValueError("ExecutionRun not found")
        if run.status != "RUNNING":
            return run
        run.status = outcome.status
        run.provider_run_id = outcome.provider_run_id
        run.stdout = _bounded(outcome.stdout, self._output_limit_bytes)
        run.stderr = _bounded(outcome.stderr, self._output_limit_bytes)
        run.compiler_output = _bounded(outcome.compiler_output, self._output_limit_bytes)
        run.exit_code = outcome.exit_code
        run.timed_out = outcome.timed_out
        run.output_truncated = outcome.output_truncated or any(
            len(value.encode()) > self._output_limit_bytes
            for value in (outcome.stdout, outcome.stderr, outcome.compiler_output)
        )
        run.duration_ms = outcome.duration_ms
        run.memory_bytes = outcome.memory_bytes
        run.completed_at = self._clock()
        await self._session.flush()
        runtime = InterviewRuntime(self._session, clock=self._clock)
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=run.interview_session_id,
                event_type="COMPILE_COMPLETED",
                source="NATIVE_RUNNER",
                occurred_at=run.completed_at,
                idempotency_key=f"execution-compile:{run.id}",
                payload={"execution_run_id": str(run.id), "status": run.status},
                schema_version="execution.compile.v1",
                code_snapshot_id=run.code_snapshot_id,
            )
        )
        if run.status != "COMPILE_ERROR":
            await runtime.accept_event(
                AcceptEventCommand(
                    session_id=run.interview_session_id,
                    event_type="TEST_COMPLETED",
                    source="NATIVE_RUNNER",
                    occurred_at=run.completed_at,
                    idempotency_key=f"execution-test:{run.id}",
                    payload={"execution_run_id": str(run.id), "status": run.status},
                    schema_version="execution.test.v1",
                    code_snapshot_id=run.code_snapshot_id,
                )
            )
        request = await self._request_for_run(run)
        cases = {case.identifier: case for case in request.cases}
        repository = ExecutionRepository(self._session)
        for case in outcome.cases:
            definition = cases.get(case.identifier)
            if definition is None:
                continue
            await repository.add_result(
                run_id=run.id,
                identifier=case.identifier,
                input_json=definition.input_json,
                expected_output=definition.expected_output,
                actual_output=case.actual_output,
                status=case.status,
                duration_ms=case.duration_ms,
                failure_classification=case.failure_classification,
            )
        return run

    async def _canonical_snapshot(self, command: RunCommand) -> CodeSnapshot:
        result = await RealtimeControlService(
            self._session, clock=self._clock
        ).persist_candidate_code_snapshot(
            session_id=command.session_id,
            message=CandidateCodeSnapshotMessage(
                type="candidate_code_snapshot",
                client_event_id=f"run-snapshot:{command.client_event_id}",
                client_instance_id=command.client_instance_id,
                client_sequence=command.client_sequence,
                source_code=command.source_code,
                language="cpp",
                trigger="EDIT_BURST",
                idempotency_key=f"run-snapshot:{command.idempotency_key}",
            ),
        )
        snapshot = await self._session.get(CodeSnapshot, result.snapshot_id)
        if snapshot is None:
            raise ValueError("Canonical CodeSnapshot was not persisted")
        return snapshot

    async def _request_for_run(self, run: ExecutionRun) -> ExecutionRequest:
        snapshot = await self._session.get(CodeSnapshot, run.code_snapshot_id)
        problem_version = await self._session.get(InterviewSession, run.interview_session_id)
        if snapshot is None or problem_version is None:
            raise ValueError("Execution provenance is incomplete")
        from app.problems.models import ProblemVersion

        problem = await self._session.get(ProblemVersion, problem_version.problem_version_id)
        if problem is None:
            raise ValueError("Problem version was not found")
        harness, cases = cpp_harness_for_problem(problem.io_schema_json)
        return ExecutionRequest(
            language=snapshot.language,
            source_code=snapshot.source_code,
            harness=harness,
            cases=cases,
            compile_timeout_seconds=self._compile_timeout_seconds,
            run_timeout_seconds=self._run_timeout_seconds,
            memory_limit_mb=self._memory_limit_mb,
            output_limit_bytes=self._output_limit_bytes,
        )


def _bounded(value: str, byte_limit: int) -> str:
    encoded = value.encode()[:byte_limit]
    return encoded.decode(errors="replace")
