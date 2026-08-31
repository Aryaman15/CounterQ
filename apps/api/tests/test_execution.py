# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.execution.harness import CustomTestValidationError
from app.execution.models import ExecutionRun
from app.execution.models import TestResult as ExecutionTestResult
from app.execution.provider import ExecutionCaseOutcome, ExecutionOutcome, FakeExecutorProvider
from app.execution.routes import DevelopmentRunRequest, _response
from app.execution.service import ExecutionService, RunCommand
from app.interviews.completion import InterviewCompletionService
from app.interviews.dev_factory import create_development_interview
from app.interviews.runtime import SessionClosed
from app.observation.models import InterviewEvent
from app.problems.repository import ProblemRepository


async def test_run_uses_exact_canonical_snapshot_and_persists_visible_results(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    outcome = ExecutionOutcome(
        status="SUCCEEDED",
        provider_run_id="sandbox-1",
        cases=(
            ExecutionCaseOutcome("visible-1", "3", "PASSED", 4),
            ExecutionCaseOutcome("visible-2", "1", "PASSED", 4),
            ExecutionCaseOutcome("visible-3", "3", "PASSED", 4),
        ),
    )
    provider = FakeExecutorProvider(outcome)
    service = ExecutionService(db_session, provider)
    command = RunCommand(
        session_id=development.interview_session.id,
        source_code="class Solution { public: int lengthOfLongestSubstring(string s) { return 3; } };",
        idempotency_key="run-1",
        client_event_id="run-client-1",
        client_instance_id="browser-1",
        client_sequence=1,
    )
    run, request, created = await service.begin(command)
    assert created and request.source_code == command.source_code
    completed = await service.complete(run.id, await service.execute(request))
    retry, _, retry_created = await service.begin(command)
    assert completed.status == "SUCCEEDED"
    assert retry.id == run.id and not retry_created
    assert len(provider.requests) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(ExecutionTestResult)
        .where(ExecutionTestResult.execution_run_id == run.id)
    ) == 3
    events = list(
        (await db_session.scalars(select(InterviewEvent).order_by(InterviewEvent.server_sequence))).all()
    )
    session_events = [
        event for event in events if event.interview_session_id == development.interview_session.id
    ]
    assert [event.event_type for event in session_events] == [
        "CODE_SNAPSHOT_CREATED",
        "RUN_CLICKED",
        "COMPILE_COMPLETED",
        "TEST_COMPLETED",
    ]
    assert session_events[1].code_snapshot_id == run.code_snapshot_id


@pytest.mark.parametrize(
    "outcome",
    [
        ExecutionOutcome(status="COMPILE_ERROR", provider_run_id="compile", compiler_output="bad"),
        ExecutionOutcome(status="RUNTIME_ERROR", provider_run_id="runtime", exit_code=139),
        ExecutionOutcome(status="TIMED_OUT", provider_run_id="timeout", timed_out=True),
        ExecutionOutcome(status="OUTPUT_LIMIT_EXCEEDED", provider_run_id="output", output_truncated=True),
        ExecutionOutcome(status="PROVIDER_ERROR", provider_run_id=None),
    ],
)
async def test_execution_failures_are_canonical_outcomes(
    db_session: AsyncSession, outcome: ExecutionOutcome
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = ExecutionService(db_session, FakeExecutorProvider(outcome))
    run, request, _ = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code="class Solution { public: int lengthOfLongestSubstring(string s) { return 0; } };",
            idempotency_key=f"run-{outcome.status}",
            client_event_id=f"client-{outcome.status}",
            client_instance_id="browser-1",
            client_sequence=1,
        )
    )
    completed = await service.complete(run.id, await service.execute(request))
    assert completed.status == outcome.status


async def test_completed_session_cannot_start_run(db_session: AsyncSession) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    now = datetime.now(UTC)
    await InterviewCompletionService(db_session, clock=lambda: now).complete(
        session_id=development.interview_session.id,
        reason="USER_ENDED",
        expected_state_version=0,
        idempotency_key="end-before-run",
    )
    service = ExecutionService(db_session, FakeExecutorProvider(ExecutionOutcome("SUCCEEDED", None)))
    with pytest.raises(SessionClosed):
        await service.begin(
            RunCommand(
                session_id=development.interview_session.id,
                source_code="class Solution {};",
                idempotency_key="rejected-run",
                client_event_id="rejected-client",
                client_instance_id="browser-1",
                client_sequence=1,
            )
        )


async def test_compile_failure_persists_every_visible_case_as_not_run(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = ExecutionService(
        db_session,
        FakeExecutorProvider(
            ExecutionOutcome("COMPILE_ERROR", "compile-failure", compiler_output="bad")
        ),
    )
    run, request, _ = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code="class Solution {",
            idempotency_key="compile-not-run",
            client_event_id="compile-not-run-event",
            client_instance_id="compile-not-run-browser",
            client_sequence=1,
        )
    )
    await service.complete(run.id, await service.execute(request))
    results = list(
        (
            await db_session.scalars(
                select(ExecutionTestResult)
                .where(ExecutionTestResult.execution_run_id == run.id)
                .order_by(ExecutionTestResult.test_identifier)
            )
        ).all()
    )
    assert len(results) == len(request.cases) == 3
    assert {result.status for result in results} == {"NOT_RUN"}
    assert {result.failure_classification for result in results} == {
        "EXECUTION_COMPILE_ERROR"
    }


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("cpp", "class Solution { public: int lengthOfLongestSubstring(string s) { return 3; } };"),
        ("python", "class Solution:\n    def lengthOfLongestSubstring(self, s: str) -> int:\n        return 3"),
        ("java", "class Solution { public int lengthOfLongestSubstring(String s) { return 3; } }"),
    ],
)
async def test_configured_language_owns_snapshot_and_harness(
    db_session: AsyncSession, language: str, source: str
) -> None:
    development = await create_development_interview(
        db_session, initial_stage="IMPLEMENTATION", language=language
    )
    provider = FakeExecutorProvider(ExecutionOutcome("SUCCEEDED", f"{language}-run"))
    service = ExecutionService(db_session, provider)
    run, request, created = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code=source,
            idempotency_key=f"{language}-run",
            client_event_id=f"{language}-event",
            client_instance_id="language-test",
            client_sequence=1,
        )
    )
    assert created and run.language == language and request.language == language
    assert provider.requests == []
    await service.complete(run.id, await service.execute(request))
    assert provider.requests[0].harness


async def test_run_rebuild_uses_its_exact_problem_version_not_session_current_version(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = ExecutionService(
        db_session, FakeExecutorProvider(ExecutionOutcome("SUCCEEDED", "exact-version"))
    )
    run, _, _ = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code=(
                "class Solution { public: int lengthOfLongestSubstring(string s) "
                "{ return 3; } };"
            ),
            idempotency_key="exact-problem-version",
            client_event_id="exact-problem-version-event",
            client_instance_id="exact-problem-version-browser",
            client_sequence=1,
        )
    )
    newer = await ProblemRepository(db_session).add_problem_version(
        problem=development.problem,
        version="v2",
        title="A newer immutable version",
        statement="A deliberately different version.",
        content_hash="sha256:exact-problem-version-v2",
        schema_version="problem.v1",
    )
    newer.io_schema_json = {
        "execution": {
            "method_name": "differentMethod",
            "arguments": [{"name": "value", "type": "int"}],
            "return_type": "int",
            "visible_cases": [
                {"arguments": {"value": 1}, "expected_output": 1}
            ],
        }
    }
    newer_pack = await ProblemRepository(db_session).add_interview_pack_version(
        problem_version=newer,
        schema_version="interview-pack.v1",
        pack_json={"fixture": "exact-problem-version-v2"},
        review_status="REVIEWED",
        authored_version="v2",
    )
    development.interview_session.problem_version_id = newer.id
    development.interview_session.interview_pack_version_id = newer_pack.id
    await db_session.flush()

    rebuilt = await service._request_for_run(run)

    assert run.problem_version_id == development.problem_version.id
    assert "lengthOfLongestSubstring" in rebuilt.harness
    assert "differentMethod" not in rebuilt.harness


async def test_custom_run_persists_no_verdict_and_retries_idempotently(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    exact_source = (
        "class Solution { public: int lengthOfLongestSubstring(string s) "
        "{ return (int)s.size(); } };"
    )
    outcome = ExecutionOutcome(
        status="SUCCEEDED",
        provider_run_id="custom-sandbox-1",
        cases=(ExecutionCaseOutcome("custom-1", "4", "PASSED", 3),),
    )
    provider = FakeExecutorProvider(outcome)
    service = ExecutionService(db_session, provider)
    command = RunCommand(
        session_id=development.interview_session.id,
        source_code=exact_source,
        idempotency_key="custom-idempotent",
        client_event_id="custom-event",
        client_instance_id="custom-browser",
        client_sequence=1,
        run_kind="CUSTOM",
        custom_arguments={"s": 'a"\\b'},
    )

    run, request, created = await service.begin(command)
    assert created
    assert request.source_code == exact_source
    assert request.cases[0].input_json == {"s": 'a"\\b'}
    assert request.cases[0].expected_output is None
    assert run.problem_version_id == development.problem_version.id
    run_event = await db_session.get(InterviewEvent, run.run_event_id)
    assert run_event is not None
    assert run_event.payload["run_kind"] == "CUSTOM"
    assert run_event.payload["custom_arguments"] == {"s": 'a"\\b'}
    await service.complete(run.id, await service.execute(request))

    retry, retry_request, retry_created = await service.begin(command)
    assert retry.id == run.id
    assert not retry_created
    assert retry_request.source_code == exact_source
    assert retry_request.cases == request.cases
    assert len(provider.requests) == 1
    results = list(
        (
            await db_session.scalars(
                select(ExecutionTestResult).where(
                    ExecutionTestResult.execution_run_id == run.id
                )
            )
        ).all()
    )
    assert len(results) == 1
    assert results[0].input_json == {"s": 'a"\\b'}
    assert results[0].expected_output is None
    assert results[0].actual_output == "4"
    assert results[0].status == "PASSED"
    execution_events = list(
        (
            await db_session.scalars(
                select(InterviewEvent)
                .where(InterviewEvent.interview_session_id == run.interview_session_id)
                .where(InterviewEvent.event_type.in_(["RUN_CLICKED", "COMPILE_COMPLETED", "TEST_COMPLETED"]))
            )
        ).all()
    )
    assert len(execution_events) == 3
    assert {event.payload["run_kind"] for event in execution_events} == {"CUSTOM"}

    hydrated = await db_session.scalar(
        select(ExecutionRun)
        .options(
            selectinload(ExecutionRun.code_snapshot),
            selectinload(ExecutionRun.run_event),
        )
        .where(ExecutionRun.id == run.id)
    )
    assert hydrated is not None
    response = _response(hydrated, results)
    assert response.run_kind == "CUSTOM"
    assert response.cases[0].comparison_kind == "NONE"
    assert response.cases[0].status == "EXECUTED"
    assert response.cases[0].actual_output_value == 4


async def test_failed_custom_run_persists_one_not_run_result(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = ExecutionService(
        db_session,
        FakeExecutorProvider(ExecutionOutcome("COMPILE_ERROR", "custom-compile")),
    )
    run, request, _ = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code="class Solution {",
            idempotency_key="custom-compile",
            client_event_id="custom-compile-event",
            client_instance_id="custom-compile-browser",
            client_sequence=1,
            run_kind="CUSTOM",
            custom_arguments={"s": "abc"},
        )
    )
    await service.complete(run.id, await service.execute(request))
    result = await db_session.scalar(
        select(ExecutionTestResult).where(ExecutionTestResult.execution_run_id == run.id)
    )
    assert result is not None
    assert result.status == "NOT_RUN"
    assert result.expected_output is None
    assert result.failure_classification == "EXECUTION_COMPILE_ERROR"


async def test_custom_retry_rebuilds_exact_problem_version_and_arguments(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    service = ExecutionService(
        db_session, FakeExecutorProvider(ExecutionOutcome("SUCCEEDED", "custom-exact"))
    )
    run, original, _ = await service.begin(
        RunCommand(
            session_id=development.interview_session.id,
            source_code="class Solution { public: int lengthOfLongestSubstring(string s) { return 9; } };",
            idempotency_key="custom-exact",
            client_event_id="custom-exact-event",
            client_instance_id="custom-exact-browser",
            client_sequence=1,
            run_kind="CUSTOM",
            custom_arguments={"s": "exact"},
        )
    )
    newer = await ProblemRepository(db_session).add_problem_version(
        problem=development.problem,
        version="v2",
        title="No custom support",
        statement="A newer immutable version.",
        content_hash="sha256:custom-exact-v2",
        schema_version="problem.v1",
    )
    newer.io_schema_json = {
        "execution": {
            "method_name": "differentMethod",
            "arguments": [{"name": "value", "type": "int"}],
            "return_type": "int",
            "visible_cases": [{"arguments": {"value": 1}, "expected_output": 1}],
            "custom_test_supported": False,
        }
    }
    newer_pack = await ProblemRepository(db_session).add_interview_pack_version(
        problem_version=newer,
        schema_version="interview-pack.v1",
        pack_json={"fixture": "custom-exact-v2"},
        review_status="REVIEWED",
        authored_version="v2",
    )
    development.interview_session.problem_version_id = newer.id
    development.interview_session.interview_pack_version_id = newer_pack.id
    await db_session.flush()

    rebuilt = await service._request_for_run(run)
    assert rebuilt.cases == original.cases
    assert rebuilt.cases[0].input_json == {"s": "exact"}
    assert "lengthOfLongestSubstring" in rebuilt.harness
    assert "differentMethod" not in rebuilt.harness


@pytest.mark.parametrize("field", ["expected_output", "comparator", "method_name"])
def test_run_contract_rejects_server_owned_execution_fields(field: str) -> None:
    payload = {
        "interview_session_id": "00000000-0000-0000-0000-000000000001",
        "source_code": "class Solution {}",
        "idempotency_key": "custom-contract",
        "client_event_id": "custom-contract-event",
        "client_instance_id": "custom-contract-browser",
        "client_sequence": 1,
        "run_kind": "CUSTOM",
        "custom_arguments": {"value": 1},
        field: 1,
    }
    with pytest.raises(ValidationError, match=field):
        DevelopmentRunRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"run_kind": "VISIBLE", "custom_arguments": {"value": 1}},
        {"run_kind": "CUSTOM"},
    ],
)
def test_run_contract_requires_consistent_case_selection(payload: dict[str, object]) -> None:
    base = {
        "interview_session_id": "00000000-0000-0000-0000-000000000001",
        "source_code": "class Solution {}",
        "idempotency_key": "case-selection-contract",
        "client_event_id": "case-selection-contract-event",
        "client_instance_id": "case-selection-contract-browser",
        "client_sequence": 1,
    }
    with pytest.raises(ValidationError):
        DevelopmentRunRequest.model_validate(base | payload)


async def test_custom_support_is_checked_before_snapshot_or_run_persistence(
    db_session: AsyncSession,
) -> None:
    development = await create_development_interview(db_session, initial_stage="IMPLEMENTATION")
    execution = development.problem_version.io_schema_json["execution"]
    assert isinstance(execution, dict)
    execution["custom_test_supported"] = False
    service = ExecutionService(
        db_session, FakeExecutorProvider(ExecutionOutcome("SUCCEEDED", "must-not-run"))
    )

    with pytest.raises(CustomTestValidationError, match="not supported"):
        await service.begin(
            RunCommand(
                session_id=development.interview_session.id,
                source_code="class Solution {};",
                idempotency_key="unsupported-custom",
                client_event_id="unsupported-custom-event",
                client_instance_id="unsupported-custom-browser",
                client_sequence=1,
                run_kind="CUSTOM",
                custom_arguments={"s": "abc"},
            )
        )

    assert await db_session.scalar(
        select(func.count())
        .select_from(ExecutionRun)
        .where(ExecutionRun.interview_session_id == development.interview_session.id)
    ) == 0
    assert await db_session.scalar(
        select(func.count())
        .select_from(InterviewEvent)
        .where(InterviewEvent.interview_session_id == development.interview_session.id)
    ) == 0
