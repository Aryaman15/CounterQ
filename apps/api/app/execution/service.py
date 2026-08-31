"""Application service: persist intent, call isolated provider, persist bounded facts."""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.harness import (
    CaseSelection,
    CustomCaseSelection,
    CustomTestValidationError,
    VisibleCaseSelection,
    execution_request_for_problem,
)
from app.execution.models import ExecutionRun
from app.execution.policy import (
    DEFAULT_COMPILE_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_RUN_TIMEOUT_SECONDS,
)
from app.execution.provider import ExecutionOutcome, ExecutionRequest, ExecutorProvider
from app.execution.repository import ExecutionRepository
from app.interviews.models import InterviewSession
from app.interviews.runtime import AcceptEventCommand, InterviewRuntime
from app.observation.models import CodeSnapshot, InterviewEvent
from app.realtime.control_protocol import CandidateCodeSnapshotMessage
from app.realtime.control_service import RealtimeControlService

if TYPE_CHECKING:
    from app.problems.models import ProblemVersion


@dataclass(frozen=True)
class RunCommand:
    session_id: UUID
    source_code: str
    idempotency_key: str
    client_event_id: str
    client_instance_id: str
    client_sequence: int
    run_kind: Literal["VISIBLE", "CUSTOM"] = "VISIBLE"
    custom_arguments: dict[str, object] | None = None


class ExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        provider: ExecutorProvider,
        *,
        clock: Callable[[], datetime] | None = None,
        compile_timeout_seconds: int = DEFAULT_COMPILE_TIMEOUT_SECONDS,
        run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
        memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
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
        language = await self._configured_language(interview)
        selection = _selection_for_command(command)
        problem = await self._problem_version(interview.problem_version_id)
        provider_request = self._build_request(
            io_schema=problem.io_schema_json,
            language=language,
            source_code=command.source_code,
            case_selection=selection,
        )
        snapshot = await self._canonical_snapshot(command, language)
        event_payload: dict[str, object] = {
            "code_snapshot_id": str(snapshot.id),
            "code_version": snapshot.version_number,
            "run_kind": command.run_kind,
        }
        if isinstance(selection, CustomCaseSelection):
            event_payload["custom_arguments"] = selection.arguments
        run_event = await InterviewRuntime(self._session, clock=self._clock).accept_event(
            AcceptEventCommand(
                session_id=command.session_id,
                event_type="RUN_CLICKED",
                source="NATIVE_RUNNER",
                occurred_at=self._clock(),
                idempotency_key=f"run-event:{command.idempotency_key}",
                payload=event_payload,
                provenance={
                    "run_idempotency_key": command.idempotency_key,
                    "run_kind": command.run_kind,
                },
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
        return run, provider_request, True

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
        run.runtime_version = outcome.runtime_version
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
        run_kind, _ = await self._selection_for_run(run)
        await runtime.accept_event(
            AcceptEventCommand(
                session_id=run.interview_session_id,
                event_type="COMPILE_COMPLETED",
                source="NATIVE_RUNNER",
                occurred_at=run.completed_at,
                idempotency_key=f"execution-compile:{run.id}",
                payload={
                    "execution_run_id": str(run.id),
                    "status": run.status,
                    "run_kind": run_kind,
                },
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
                    payload={
                        "execution_run_id": str(run.id),
                        "status": run.status,
                        "run_kind": run_kind,
                    },
                    schema_version="execution.test.v1",
                    code_snapshot_id=run.code_snapshot_id,
                )
            )
        request = await self._request_for_run(run)
        outcomes = {case.identifier: case for case in outcome.cases}
        repository = ExecutionRepository(self._session)
        for definition in request.cases:
            case = outcomes.get(definition.identifier)
            failure_classification: str | None
            if case is None:
                result_status = "NOT_RUN" if run.status != "SUCCEEDED" else "FAILED"
                actual_output = None
                duration_ms = None
                failure_classification = (
                    f"EXECUTION_{run.status}"
                    if run.status != "SUCCEEDED"
                    else "MISSING_CASE_OUTPUT"
                )
            else:
                result_status = case.status
                actual_output = case.actual_output
                duration_ms = case.duration_ms
                failure_classification = case.failure_classification
            await repository.add_result(
                run_id=run.id,
                identifier=definition.identifier,
                input_json=definition.input_json,
                expected_output=definition.expected_output,
                actual_output=actual_output,
                status=result_status,
                duration_ms=duration_ms,
                failure_classification=failure_classification,
            )
        return run

    async def _canonical_snapshot(self, command: RunCommand, language: str) -> CodeSnapshot:
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
                language=language,
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
        interview = await self._session.get(InterviewSession, run.interview_session_id)
        if snapshot is None or interview is None:
            raise ValueError("Execution provenance is incomplete")
        problem = await self._problem_version(run.problem_version_id)
        language = await self._configured_language(interview)
        if snapshot.language != language or run.language != language:
            raise ValueError("Canonical execution language is inconsistent")
        _, selection = await self._selection_for_run(run)
        return self._build_request(
            io_schema=problem.io_schema_json,
            language=language,
            source_code=snapshot.source_code,
            case_selection=selection,
        )

    def _build_request(
        self,
        *,
        io_schema: dict[str, object],
        language: str,
        source_code: str,
        case_selection: CaseSelection,
    ) -> ExecutionRequest:
        return execution_request_for_problem(
            io_schema=io_schema,
            language=language,
            source_code=source_code,
            compile_timeout_seconds=self._compile_timeout_seconds,
            run_timeout_seconds=self._run_timeout_seconds,
            memory_limit_mb=self._memory_limit_mb,
            output_limit_bytes=self._output_limit_bytes,
            case_selection=case_selection,
        )

    async def _selection_for_run(
        self, run: ExecutionRun
    ) -> tuple[Literal["VISIBLE", "CUSTOM"], CaseSelection]:
        event = await self._session.get(InterviewEvent, run.run_event_id)
        if event is None:
            raise ValueError("Execution run event was not found")
        run_kind = event.payload.get("run_kind", "VISIBLE")
        if run_kind == "VISIBLE":
            return "VISIBLE", VisibleCaseSelection()
        if run_kind != "CUSTOM":
            raise ValueError("Execution run has an invalid run kind")
        arguments = event.payload.get("custom_arguments")
        if not isinstance(arguments, dict) or not all(
            isinstance(name, str) for name in arguments
        ):
            raise ValueError("Custom execution provenance is incomplete")
        return "CUSTOM", CustomCaseSelection(dict(arguments))

    async def _problem_version(self, problem_version_id: UUID) -> ProblemVersion:
        from app.problems.models import ProblemVersion

        problem = await self._session.get(ProblemVersion, problem_version_id)
        if problem is None:
            raise ValueError("Problem version was not found")
        return problem

    async def _configured_language(self, interview: InterviewSession) -> str:
        from app.interviews.models import InterviewConfiguration

        configuration = await self._session.get(
            InterviewConfiguration, interview.interview_configuration_id
        )
        if configuration is None or configuration.language not in {"cpp", "python", "java"}:
            raise ValueError("Interview has no supported configured language")
        return configuration.language


def _bounded(value: str, byte_limit: int) -> str:
    encoded = value.encode()[:byte_limit]
    return encoded.decode(errors="replace")


def _selection_for_command(command: RunCommand) -> CaseSelection:
    if command.run_kind == "VISIBLE":
        if command.custom_arguments is not None:
            raise CustomTestValidationError(
                "custom_arguments must be absent for a visible run"
            )
        return VisibleCaseSelection()
    if command.run_kind == "CUSTOM":
        if command.custom_arguments is None:
            raise CustomTestValidationError(
                "custom_arguments are required for a custom run"
            )
        return CustomCaseSelection(dict(command.custom_arguments))
    raise CustomTestValidationError("Unsupported execution run kind")
